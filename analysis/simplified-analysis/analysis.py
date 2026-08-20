import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

# Paths for the directories used
BASE = Path(__file__).resolve().parent
INPUTS = BASE / "inputs"
TABLES = BASE / "tables"
FIGURES = BASE / "figures"
TARGET_RPS = 2550

CONDITIONS = ["fixed3", "autoscale", "fixed4"]
LABELS = {"fixed3": "Fixed 3", "autoscale": "Autoscaling 3→4", "fixed4": "Fixed 4"}
COLORS = {
    "fixed3": "#0072B2",
    "autoscale": "#D55E00",
    "fixed4": "#009E73",
    "redis-cluster-0": "#0072B2",
    "redis-cluster-1": "#E69F00",
    "redis-cluster-2": "#009E73",
    "redis-cluster-6": "#CC79A7",
}

# Assumes a configuration of active masters and the standby
ACTIVE_MASTERS = {
    "fixed3": ["redis-cluster-0", "redis-cluster-1", "redis-cluster-2"],
    "autoscale": ["redis-cluster-0", "redis-cluster-1", "redis-cluster-2", "redis-cluster-6"],
    "fixed4": ["redis-cluster-0", "redis-cluster-1", "redis-cluster-2", "redis-cluster-6"],
}


def latency_to_ms(value: str) -> float:
    """Normalize the latency suffixes printed by wrk2 to milliseconds."""
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(us|ms|s|m)", value.strip())
    assert match, f"unsupported latency value: {value!r}"
    number = float(match.group(1))
    return number * {"us": 0.001, "ms": 1.0, "s": 1000.0, "m": 60000.0}[match.group(2)]


def read_time_window(path: Path) -> dict[str, object]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return {
        "start_epoch": int(values["start_epoch"]),
        "end_epoch": int(values["end_epoch"]),
        "start_iso": values["start_iso"],
        "end_iso": values["end_iso"],
        "duration_seconds": int(values["end_epoch"]) - int(values["start_epoch"]),
        "wrk_exit_code": int(values["wrk_exit_code"]),
    }


def parse_wrk_output(condition: str, wrk_path: Path, window_path: Path) -> dict[str, object]:
    # print(f"[INFO]: condition = {condition!r}, wrk_path = {wrk_path!r}, window_path = {window_path!r}")

    # reading the wrk2 output file
    text = wrk_path.read_text(encoding="utf-8", errors="replace")

    def match(pattern: str, flags: int = 0) -> re.Match[str]:
        """Search for a pattern in the wrk2 output text and assert it is found."""
        found = re.search(pattern, text, flags)
        assert found, f"{condition}: could not parse {pattern!r} from {wrk_path.name}"
        return found

    runtime = match(r"Running\s+([0-9.]+)([smh])\s+test")

    # calculates duration in seconds based on the reported runtime and its unit
    reported_duration = float(runtime.group(1)) * {"s": 1, "m": 60, "h": 3600}[runtime.group(2)]

    # parses the file for concurrency, mean latency, sent requests, completed requests, and achieved RPS
    concurrency = match(r"(\d+)\s+threads and\s+(\d+)\s+connections")
    mean_latency = match(r"^\s*Latency\s+(\S+)\s+", re.MULTILINE).group(1)
    sent = int(match(r"Sent\s+(\d+)\s+requests").group(1))
    completed = int(match(r"^\s*(\d+)\s+requests in\s+\S+", re.MULTILINE).group(1))
    achieved = float(match(r"Requests/sec:\s+([0-9.]+)").group(1))

    # obtains the latency percentiles from the wrk2 output
    percentiles = {}
    for label in ("50.000", "90.000", "99.000", "99.900", "100.000"):
        percentiles[label] = latency_to_ms(match(rf"^\s*{re.escape(label)}%\s+(\S+)", re.MULTILINE).group(1))

    window = read_time_window(window_path)

    # various assertions
    assert abs(window["duration_seconds"] - 600) <= 5, f"{condition}: recorded window is not approximately 600 seconds"
    assert abs(reported_duration - 600) <= 5, f"{condition}: wrk2 did not report an approximately 600-second run"
    assert int(concurrency.group(1)) == 4, f"{condition}: expected four wrk2 threads"
    assert int(concurrency.group(2)) == 328, f"{condition}: expected 328 connections"
    assert window["wrk_exit_code"] == 0, f"{condition}: wrk2 exit code was not zero"
    # The console does not echo -R. Its sent count must agree with the recorded 2,550-RPS configuration.
    scheduled_rate = sent / float(window["duration_seconds"])
    assert abs(scheduled_rate - TARGET_RPS) / TARGET_RPS < 0.02, f"{condition}: sent count is inconsistent with 2,550 RPS"

    return {
        "condition": condition,
        "target_rps": TARGET_RPS,
        "duration_seconds": window["duration_seconds"],
        "start_epoch": window["start_epoch"],
        "start_iso": window["start_iso"],
        "end_iso": window["end_iso"],
        "threads": int(concurrency.group(1)),
        "connections": int(concurrency.group(2)),
        "sent_requests": sent,
        "completed_requests": completed,
        "completion_ratio": completed / sent,
        "achieved_rps": achieved,
        "mean_latency_ms": latency_to_ms(mean_latency),
        "p50_ms": percentiles["50.000"],
        "p90_ms": percentiles["90.000"],
        "p99_ms": percentiles["99.000"],
        "p999_ms": percentiles["99.900"],
        "max_latency_ms": percentiles["100.000"],
        "wrk_exit_code": window["wrk_exit_code"],
    }


def load_p99_trace(start_epoch: int) -> pd.DataFrame:
    trace = pd.read_csv(INPUTS / "autoscale-p99.tsv", sep="\t")
    assert list(trace.columns) == ["epoch_ns", "p99_us"], "autoscale p99: unexpected columns"
    assert trace["epoch_ns"].is_monotonic_increasing, "autoscale p99 timestamps are not monotonic"
    assert (trace["p99_us"] > 0).all(), "autoscale p99 contains a non-positive value"
    trace["observed_at_utc"] = pd.to_datetime(trace["epoch_ns"], unit="ns", utc=True)
    trace["elapsed_seconds"] = trace["epoch_ns"] / 1e9 - start_epoch
    trace["p99_ms"] = trace["p99_us"] / 1000.0
    return trace


def load_cpu_data() -> dict[str, pd.DataFrame]:
    """
    Returns a dictionary containing concatenated CPU data for redis, node, and service across all conditions.
    """
    redis_parts = []
    node_parts = []
    service_parts = []

    # iterating through all conditions to load redis CPU and node CPU data
    for condition in CONDITIONS:
        # assigning the column names for the redis CPU data
        redis = pd.read_csv(
            INPUTS / f"{condition}-redis-cpu.tsv",
            sep="\t",
            names=["pod", "timestamp_utc", "cpu_percent"],
        )

        # adding the condition column and converting the timestamp to datetime
        redis["condition"] = condition
        redis["timestamp_utc"] = pd.to_datetime(redis["timestamp_utc"], utc=True)
        redis_parts.append(redis)

        # reading CPU from the node & assigning the column names
        node = pd.read_csv(
            INPUTS / f"{condition}-node-cpu.tsv",
            sep="\t",
            names=["timestamp_utc", "cpu_percent"],
        )

        # adding some additional columns to the node CPU data
        node["condition"] = condition
        node["timestamp_utc"] = pd.to_datetime(node["timestamp_utc"], utc=True)
        node_parts.append(node)

    # NOTE: service CPU data is only available for the fixed3 and fixed4 conditions
    for condition in ("fixed3", "fixed4"):
        # loading CPU for the service & assigning the column names
        service = pd.read_csv(
            INPUTS / f"{condition}-service-cpu.tsv",
            sep="\t",
            names=["pod", "resource", "timestamp_utc", "cpu_percent"],
        )

        # again adding the condition column and converting the timestamp to datetime
        service["condition"] = condition
        service["timestamp_utc"] = pd.to_datetime(service["timestamp_utc"], utc=True)
        service_parts.append(service)

    # return all the concatenated CPU data for redis, node, and service across all conditions
    return {
        "redis": pd.concat(redis_parts, ignore_index=True),
        "node": pd.concat(node_parts, ignore_index=True),
        "service": pd.concat(service_parts, ignore_index=True),
    }


def count_slots(ranges: str) -> int:
    """
    Helper function to count the number of slots represented by a range string.
    For example, "1-3,5" would represent 4 slots: 1, 2, 3, and 5.
    """
    if not ranges or ranges == "none":
        return 0
    total = 0
    for item in ranges.split(","):
        if "-" in item:
            start, end = map(int, item.split("-", 1))
            total += end - start + 1
        else:
            total += 1
    return total


def load_topology_events(start_epoch: int, end_iso: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Inputs: 
    start_epoch: int
        The starting epoch time in seconds for the autoscale monitoring period.
    end_iso: str
        The ending time in ISO 8601 format.

    Returns:
    tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
        A tuple containing three DataFrames: the monitor DataFrame, the events DataFrame, and the freeze rows DataFrame.
    """

    # loading the topology monitor data
    monitor = pd.read_csv(INPUTS / "autoscale-topology.tsv", sep="\t")

    # adding derived columns for timestamp and elapsed time
    monitor["timestamp_utc"] = pd.to_datetime(monitor["epoch_ns"], unit="ns", utc=True)
    monitor["elapsed_seconds"] = monitor["epoch_ns"] / 1e9 - start_epoch

    masters_up = ((monitor["masters"].shift() == 3) & (monitor["masters"] == 4)).sum()
    cluster_up = ((monitor["cluster_size"].shift() == 3) & (monitor["cluster_size"] == 4)).sum()
    assert monitor.iloc[0]["masters"] == 3 and monitor.iloc[0]["cluster_size"] == 3, "autoscaling did not begin at three masters"
    assert masters_up == 1 and cluster_up == 1, "autoscaling did not reach four masters exactly once"
    assert ((monitor["slots_ok"] == 16384) & (monitor["slots_fail"] == 0)).all(), "not all 16,384 slots remained healthy"

    # reading the autoscale events text file
    event_text = (INPUTS / "autoscale-events.txt").read_text(encoding="utf-8", errors="replace")

    def event_time(phrase: str) -> pd.Timestamp:
        """Returning the timestamp of the first line containing the given phrase."""
        line = next((line for line in event_text.splitlines() if phrase in line), None)
        assert line is not None, f"missing autoscaling event: {phrase}"
        return pd.Timestamp(line.split()[0])

    # find the row when autoscaling was disabled
    freeze_rows = monitor[monitor["action"] == "freeze_completed_scale_at_four"]
    assert len(freeze_rows) == 1, "expected one freeze event after the completed scale-up"
    
    # recording the key autoscaling events with their timestamps (BOOKMARK)
    start = pd.Timestamp(start_epoch, unit="s", tz="UTC")
    event_rows = [
        ("workload_start", start, "formal wrk2 start"),
        ("scale_up_trigger", event_time("Triggering scale-up using standby pod"), "artificial 10% Redis CPU threshold crossed"),
        ("reshard_start", event_time("Creating reshard job to activate standby"), "operator created the reshard job"),
        ("reshard_complete", event_time("Reshard job succeeded"), "operator observed successful resharding"),
        ("freeze", pd.Timestamp(freeze_rows.iloc[0]["iso_time"]), "monitor froze autoscaling after four masters"),
        ("workload_end", pd.Timestamp(end_iso), "formal wrk2 end"),
    ]
    events = pd.DataFrame(event_rows, columns=["event", "timestamp_utc", "description"])
    events["elapsed_seconds"] = (events["timestamp_utc"] - start).dt.total_seconds()

    # reading the topology snapshots for before and after the scale-up
    before = pd.read_csv(INPUTS / "fixed3-topology.tsv", sep="\t", dtype=str).fillna("none")
    after = pd.read_csv(INPUTS / "fixed4-topology.tsv", sep="\t", dtype=str).fillna("none")
    topology_parts = []
    for snapshot, frame in (("fixed3_before", before), ("fixed4_after", after)):
        frame = frame.copy()
        frame["snapshot"] = snapshot
        frame["logical_keys"] = frame["keys"].astype(int)
        frame["slot_count"] = frame["slots"].map(count_slots)
        frame["slot_bearing"] = frame["slot_count"] > 0
        # add the processed snapshot to the list of topology parts
        topology_parts.append(frame[["snapshot", "pod", "role", "slot_bearing", "slot_count", "slots", "logical_keys"]])
    topology = pd.concat(topology_parts, ignore_index=True)

    # validity checks
    before_data = topology[(topology["snapshot"] == "fixed3_before") & (topology["role"] == "master") & topology["slot_bearing"]]
    after_data = topology[(topology["snapshot"] == "fixed4_after") & (topology["role"] == "master") & topology["slot_bearing"]]
    assert len(before_data) == 3, "fixed-three did not contain three slot-bearing masters"
    assert len(after_data) == 4, "fixed-four did not contain four slot-bearing masters"
    assert before_data["slot_count"].sum() == 16384 and after_data["slot_count"].sum() == 16384, "topology snapshots do not cover all 16,384 slots"
    assert before_data["logical_keys"].sum() == 962 and after_data["logical_keys"].sum() == 962, "logical key total was not preserved at 962"
    return monitor, events, topology


def build_summary_tables(
    runs: list[dict[str, object]],
    cpu: dict[str, pd.DataFrame],
    events: pd.DataFrame,
    topology: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    condition_summary = pd.DataFrame(runs).drop(columns=["start_epoch", "start_iso", "end_iso"])
    cpu_rows = []

    for condition in CONDITIONS:
        redis = cpu["redis"]
        for pod in ACTIVE_MASTERS[condition]:
            # extracting CPU usage values for the current condition and pod
            values = redis[(redis["condition"] == condition) & (redis["pod"] == pod)]["cpu_percent"]
            if len(values):
                cpu_rows.append((condition, "redis_master", pod, "full_window", values.mean(), values.max(), len(values), "percent of one CPU core"))

        # node CPU, appends to cpu_rows
        node = cpu["node"][cpu["node"]["condition"] == condition]["cpu_percent"]
        cpu_rows.append((condition, "node", "node", "full_window", node.mean(), node.max(), len(node), "percent of total node capacity"))

    # obtaining service total CPU usage by summing across replicas
    service_totals = cpu["service"].groupby(["condition", "resource", "timestamp_utc"], as_index=False)["cpu_percent"].sum()
    for (condition, resource), group in service_totals.groupby(["condition", "resource"], sort=True):
        values = group["cpu_percent"]
        cpu_rows.append((condition, "service_total", resource, "full_window", values.mean(), values.max(), len(values), "percent of one CPU core, summed across replicas"))

    event_lookup = events.set_index("event")["timestamp_utc"]

    # Selects only node CPU samples from the autoscaling run.
    auto_node = cpu["node"][cpu["node"]["condition"] == "autoscale"]
    
    # CPU samples have whole-second timestamps; floor event boundaries to that recorded resolution.
    reshard_start = event_lookup["reshard_start"].floor("s")
    reshard_end = event_lookup["reshard_complete"].floor("s")
    for period, values in (
        ("during_reshard", auto_node[(auto_node["timestamp_utc"] >= reshard_start) & (auto_node["timestamp_utc"] <= reshard_end)]["cpu_percent"]),
        ("after_reshard", auto_node[auto_node["timestamp_utc"] > reshard_end]["cpu_percent"]),
    ):
        assert len(values), f"no autoscaling node CPU samples for {period}"
        # adds mean and maximum CPU usage for the period
        cpu_rows.append(("autoscale", "node", "node", period, values.mean(), values.max(), len(values), "percent of total node capacity"))

    cpu_summary = pd.DataFrame(
        cpu_rows,
        columns=["condition", "resource_type", "resource", "period", "average_cpu_percent", "maximum_cpu_percent", "samples", "denominator"],
    )
    # saves the condition summary to a CSV file
    condition_summary.to_csv(TABLES / "condition-summary.csv", index=False, float_format="%.6f")
    cpu_summary.to_csv(TABLES / "cpu-summary.csv", index=False, float_format="%.6f")
    events.to_csv(TABLES / "autoscaling-events.csv", index=False, float_format="%.6f")
    topology.to_csv(TABLES / "topology-summary.csv", index=False)
    return condition_summary, cpu_summary


def save_figure(fig: plt.Figure, name: str) -> None:
    fig.savefig(FIGURES / f"{name}.png", dpi=300, bbox_inches="tight", facecolor="white")
    fig.savefig(FIGURES / f"{name}.pdf", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def make_figure_1(condition_summary: pd.DataFrame) -> None:
    frame = condition_summary.set_index("condition").loc[CONDITIONS]
    x = np.arange(3)
    colors = [COLORS[name] for name in CONDITIONS]
    labels = [LABELS[name] for name in CONDITIONS]
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.2))
    fig.suptitle("Overall condition comparison", fontsize=13)

    # Requests per second from wrk log
    axes[0].bar(x, frame["target_rps"], width=0.72, color="#D9D9D9", label="Configured offered rate")
    achieved = axes[0].bar(x, frame["achieved_rps"], width=0.42, color=colors, label="Aggregate achieved throughput")
    axes[0].bar_label(achieved, fmt="%.2f", fontsize=7)
    axes[0].set(title="Configured load and aggregate throughput", ylabel="Requests per second", xticks=x, xticklabels=labels)
    axes[0].set_ylim(0, 3300)
    axes[0].legend(frameon=False, fontsize=7)

    # plotting the latencies
    width = 0.23
    for offset, (column, label, color) in enumerate(
        [("p50_ms", "p50", "#56B4E9"), ("p90_ms", "p90", "#E69F00"), ("p99_ms", "p99", "#D55E00")]
    ):
        bars = axes[1].bar(x + (offset - 1) * width, frame[column], width, label=label, color=color)
        axes[1].bar_label(bars, fmt="%.1f", fontsize=7)
    axes[1].set_yscale("log")
    axes[1].set(title="Latency percentiles", ylabel="Latency (ms, logarithmic scale)", xticks=x, xticklabels=labels)
    axes[1].legend(frameon=False, ncol=3)

    # how many requests returned successfully within the measurement window (that were sent)
    completion = axes[2].bar(x, frame["completion_ratio"], color=colors, width=0.62)
    axes[2].bar_label(completion, fmt="%.5f", fontsize=7)
    axes[2].axhline(1.0, color="#777777", linestyle="--", linewidth=0.8)
    axes[2].set(title="Completion within measurement window", ylabel="Completed / sent requests", xticks=x, xticklabels=labels)
    axes[2].set_ylim(0, 1.06)

    fig.text(0.5, 0.01, "2,550 RPS is configured offered load, not measured per-second sends. One run per condition (n=1).", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.05, 1, 0.94))
    save_figure(fig, "figure1-overall-comparison")


def make_figure_2(
    p99: pd.DataFrame,
    cpu: dict[str, pd.DataFrame],
    monitor: pd.DataFrame,
    events: pd.DataFrame,
    cpu_summary: pd.DataFrame,
) -> None:
    elapsed = events.set_index("event")["elapsed_seconds"].to_dict()
    # obtaining when the resharding starts and completes
    start, end = elapsed["reshard_start"], elapsed["reshard_complete"]

    # obtaining autoscale CPU usage for Redis and node components
    auto_redis = cpu["redis"][cpu["redis"]["condition"] == "autoscale"].copy()
    auto_node = cpu["node"][cpu["node"]["condition"] == "autoscale"].copy()

    # obtaining the start time of the workload
    workload_start = events.loc[events["event"] == "workload_start", "timestamp_utc"].iloc[0]

    # calculating elapsed seconds for autoscale CPU usage relative to workload start
    auto_redis["elapsed_seconds"] = (auto_redis["timestamp_utc"] - workload_start).dt.total_seconds()
    auto_node["elapsed_seconds"] = (auto_node["timestamp_utc"] - workload_start).dt.total_seconds()


    node_phases = cpu_summary[(cpu_summary["condition"] == "autoscale") & (cpu_summary["resource_type"] == "node")].set_index("period")
    # obtaining fixed-condition CPU usage for node components
    fixed_node = cpu_summary[(cpu_summary["condition"].isin(["fixed3", "fixed4"])) & (cpu_summary["resource_type"] == "node") & (cpu_summary["period"] == "full_window")]
    fixed_average = fixed_node["average_cpu_percent"].mean()

    # obtaining average CPU usage for node components during and after resharding
    during_average = node_phases.loc["during_reshard", "average_cpu_percent"]
    after_average = node_phases.loc["after_reshard", "average_cpu_percent"]

    fig, axes = plt.subplots(4, 1, figsize=(11.5, 10.5), sharex=True, gridspec_kw={"height_ratios": [1.35, 1, 1, 1]})
    fig.suptitle("Autoscaling transition timeline", fontsize=13)
    for ax in axes:
        ax.axvspan(start, end, color="#F0E442", alpha=0.22)
        ax.axvline(elapsed["scale_up_trigger"], color="#E69F00", linestyle="--", linewidth=1)
        ax.axvline(elapsed["reshard_complete"], color="#009E73", linestyle="--", linewidth=1)
        ax.axvline(elapsed["freeze"], color="#CC79A7", linestyle=":", linewidth=1)

    # plotting latency for the p99 metric of the autoscale workload
    axes[0].plot(p99["elapsed_seconds"], p99["p99_ms"], color="#0072B2", linewidth=0.7)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("Interval wrk2-reported\np99 latency (ms, log)")
    axes[0].set_title("Watcher observation time, not request-send time; the -p histogram resets after each emission", loc="left", fontsize=9)

    # plotting CPU usage for node components during and after resharding
    axes[1].plot(auto_node["elapsed_seconds"], auto_node["cpu_percent"], color="#333333", marker="o", markersize=2.5, linewidth=1.1)
    axes[1].axhline(fixed_average, color="#56B4E9", linestyle="--", linewidth=1, label=f"Fixed-condition average: {fixed_average:.1f}%")
    axes[1].set_ylabel("Node CPU\n(% total capacity)")
    axes[1].text(0.01, 0.95, f"During reshard: {during_average:.1f}% average\nAfter reshard: {after_average:.1f}% average", transform=axes[1].transAxes, va="top", fontsize=8)
    axes[1].legend(frameon=False, loc="lower right", fontsize=8)

    # plotting CPU usage for Redis pods during and after resharding
    for pod in ACTIVE_MASTERS["autoscale"]:
        group = auto_redis[auto_redis["pod"] == pod]
        axes[2].plot(group["elapsed_seconds"], group["cpu_percent"], marker="o", markersize=2, linewidth=1, color=COLORS[pod], label=pod)
    axes[2].axhline(10, color="#555555", linestyle="--", linewidth=0.9, label="Artificial 10% trigger")
    axes[2].set_ylabel("Redis CPU\n(% of one core)")
    axes[2].legend(frameon=False, ncol=5, fontsize=7)

    # plotting the evolution of the cluster configuration and StatefulSet readiness during and after resharding
    axes[3].step(monitor["elapsed_seconds"], monitor["masters"], where="post", label="CR spec.masters", color="#0072B2")
    axes[3].step(monitor["elapsed_seconds"], monitor["cluster_size"], where="post", label="Redis cluster_size", color="#D55E00")
    axes[3].step(monitor["elapsed_seconds"], monitor["sts_ready"], where="post", label="StatefulSet ready pods", color="#009E73")
    axes[3].set(ylabel="Count", xlabel="Elapsed seconds from formal workload start", xlim=(0, 600), ylim=(0, 11))
    axes[3].legend(frameon=False, ncol=3, fontsize=8)
    axes[3].text(0.99, 0.05, "cluster_size=4 appears before reshard completion", transform=axes[3].transAxes, ha="right", fontsize=8)

    handles = [
        Patch(facecolor="#F0E442", alpha=0.3, label=f"Verified reshard interval ({end - start:.1f} s)"),
        Line2D([0], [0], color="#E69F00", linestyle="--", label="Scale-up trigger"),
        Line2D([0], [0], color="#009E73", linestyle="--", label="Reshard complete"),
        Line2D([0], [0], color="#CC79A7", linestyle=":", label="Freeze"),
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.965), ncol=4, frameon=False, fontsize=8)
    fig.text(0.5, 0.01, "Node and container CPU have different denominators. Resharding initiated the disruption; node saturation is a likely amplifier, not a proven sole cause. n=1.", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.04, 1, 0.93))
    save_figure(fig, "figure2-autoscaling-timeline")


def make_figure_3(cpu_summary: pd.DataFrame) -> None:
    # obtaining CPU usage for Redis pods under fixed conditions
    redis = cpu_summary[(cpu_summary["resource_type"] == "redis_master") & (cpu_summary["condition"].isin(["fixed3", "fixed4"]))]
    pods = ["redis-cluster-0", "redis-cluster-1", "redis-cluster-2", "redis-cluster-6"]
    x = np.arange(4)
    width = 0.36
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.5), sharey=True)
    fig.suptitle("Redis CPU redistribution across fixed conditions", fontsize=13)
    for ax, metric, title in (
        (axes[0], "average_cpu_percent", "Full-window average"),
        (axes[1], "maximum_cpu_percent", "Full-window maximum"),
    ):
        for index, condition in enumerate(("fixed3", "fixed4")):
            values = []
            for pod in pods:
                # getting the row corresponding to the current condition and pod
                row = redis[(redis["condition"] == condition) & (redis["resource"] == pod)]
                values.append(float(row.iloc[0][metric]) if len(row) else 0)
            # plotting the bar for the current condition and pod
            bars = ax.bar(x + (index - 0.5) * width, values, width, color=COLORS[condition], label=LABELS[condition])
            ax.bar_label(bars, fmt="%.2f", fontsize=7)
        ax.axhline(10, color="#555555", linestyle="--", linewidth=0.9)
        ax.set(title=title, ylabel="CPU (% of one core)", xticks=x, xticklabels=[pod.replace("redis-cluster-", "master ") for pod in pods])
        ax.set_ylim(0, 13.5)

    before = redis[(redis["condition"] == "fixed3") & (redis["resource"] == "redis-cluster-0")]["average_cpu_percent"].iloc[0]
    after = redis[(redis["condition"] == "fixed4") & (redis["resource"].isin(["redis-cluster-0", "redis-cluster-6"]))]["average_cpu_percent"].sum()
    axes[0].text(0.02, 0.96, f"Master 0 before: {before:.2f}%\nMasters 0 + 6 after: {after:.2f}%", transform=axes[0].transAxes, va="top", fontsize=8, bbox={"facecolor": "white", "edgecolor": "#BBBBBB"})
    fig.legend(handles=[Patch(color=COLORS["fixed3"], label="Fixed 3"), Patch(color=COLORS["fixed4"], label="Fixed 4"), Line2D([0], [0], color="#555555", linestyle="--", label="Artificial 10% trigger")], loc="upper center", bbox_to_anchor=(0.5, 0.91), ncol=3, frameon=False, fontsize=8)
    fig.text(0.5, 0.01, "Master 6 was a zero-slot standby before scaling. One formal repetition per condition (n=1).", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.05, 1, 0.84))
    save_figure(fig, "figure3-redis-cpu-redistribution")


def make_figure_4(cpu_summary: pd.DataFrame) -> None:
    components = ["nginx-thrift", "post-storage-service", "home-timeline-service", "post-storage-memcached"]
    component_labels = ["NGINX", "Post-storage service", "Home-timeline service", "Post-storage Memcached"]

    # identifying the relevant service and node data for plotting
    services = cpu_summary[(cpu_summary["resource_type"] == "service_total") & (cpu_summary["period"] == "full_window")]
    nodes = cpu_summary[(cpu_summary["resource_type"] == "node") & (cpu_summary["period"] == "full_window")].set_index("condition")
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), gridspec_kw={"width_ratios": [1.5, 1]})
    fig.suptitle("Resource-context comparison", fontsize=13)

    y = np.arange(4)
    height = 0.34
    for index, condition in enumerate(("fixed3", "fixed4")):
        # obtaining the average CPU usage for each service component under the current condition
        values = [services[(services["condition"] == condition) & (services["resource"] == resource)]["average_cpu_percent"].iloc[0] for resource in components]
        bars = axes[0].barh(y + (index - 0.5) * height, values, height, color=COLORS[condition], label=LABELS[condition])
        axes[0].bar_label(bars, fmt="%.1f", fontsize=7)
    axes[0].set_xscale("log")
    axes[0].set(title="Application services — full window", xlabel="Average total CPU across replicas (% of one core, log)", yticks=y, yticklabels=component_labels)
    axes[0].invert_yaxis()
    axes[0].legend(frameon=False)

    # obtaining the node CPU usage for the selected conditions
    x = np.arange(2)
    width = 0.34
    selected = nodes.loc[["fixed3", "fixed4"]]
    averages = axes[1].bar(x - width / 2, selected["average_cpu_percent"], width, color="#56B4E9", label="Average")
    maxima = axes[1].bar(x + width / 2, selected["maximum_cpu_percent"], width, color="#D55E00", label="Maximum")
    axes[1].bar_label(averages, fmt="%.2f", fontsize=7)
    axes[1].bar_label(maxima, fmt="%.2f", fontsize=7)
    axes[1].set(title="Node CPU — different denominator", ylabel="CPU (% of total node capacity)", xticks=x, xticklabels=["Fixed 3", "Fixed 4"])
    axes[1].set_ylim(0, 90)
    axes[1].legend(frameon=False, ncol=2)

    fig.text(0.5, 0.01, "Container totals and node percentages are not directly comparable. One formal repetition per condition (n=1).", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.05, 1, 0.93))
    save_figure(fig, "figure4-resource-comparison")


def make_figure_5(topology: pd.DataFrame) -> None:
    snapshots = ["fixed3_before", "fixed4_after"]
    labels = ["Before: Fixed 3", "After: Fixed 4"]
    pods = ["redis-cluster-0", "redis-cluster-1", "redis-cluster-2", "redis-cluster-6"]
    data = topology[(topology["role"] == "master") & topology["slot_bearing"]]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.8))
    fig.suptitle("Slot and logical-key redistribution", fontsize=13)

    for ax, column, ylabel, title in (
        (axes[0], "slot_count", "Redis hash slots", "16,384 healthy slots"),
        (axes[1], "logical_keys", "Logical keys", "962 logical keys"),
    ):
        bottoms = np.zeros(2)
        for pod in pods:
            values = []
            for snapshot in snapshots:
                # getting the row corresponding to the current snapshot and pod
                row = data[(data["snapshot"] == snapshot) & (data["pod"] == pod)]
                values.append(float(row.iloc[0][column]) if len(row) else 0)
            # creating the bar for the current pod across the snapshots
            bars = ax.bar(np.arange(2), values, bottom=bottoms, color=COLORS[pod], label=pod.replace("redis-cluster-", "master "))
            for index, (bar, value) in enumerate(zip(bars, values)):
                if value:
                    ax.text(bar.get_x() + bar.get_width() / 2, bottoms[index] + value / 2, f"{int(value)}", ha="center", va="center", fontsize=8)
            bottoms += values
        ax.set(title=title, ylabel=ylabel, xticks=np.arange(2), xticklabels=labels)
        ax.legend(frameon=False, ncol=2, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.13))
    fig.text(0.5, 0.01, "Foxtrot split master 0; masters 1 and 2 retained their ranges.\nSlot-bearing masters only; replicas and zero-slot standbys are excluded. n=1.", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.07, 1, 0.93))
    save_figure(fig, "figure5-slot-key-redistribution")


if __name__ == "__main__":
    TABLES.mkdir(exist_ok=True)
    FIGURES.mkdir(exist_ok=True)
    plt.rcParams.update({
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.alpha": 0.25,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "pdf.fonttype": 42,
    })

    # returns a dictionary containing parsed wrk output for each condition (e.g. 'fixed3', 'autoscale', 'fixed4')
    runs = [
        parse_wrk_output(condition, INPUTS / f"{condition}-wrk.txt", INPUTS / f"{condition}-window.txt")
        for condition in CONDITIONS
    ]

    # extracting the autoscale run from the list of parsed runs
    autoscale = next(run for run in runs if run["condition"] == "autoscale")
    # loading the recorded latency percentiles during the autoscale period
    p99 = load_p99_trace(int(autoscale["start_epoch"]))
    # loading cpu data across all conditions
    cpu = load_cpu_data()
    monitor, events, topology = load_topology_events(int(autoscale["start_epoch"]), str(autoscale["end_iso"]))
    condition_summary, cpu_summary = build_summary_tables(runs, cpu, events, topology)


    make_figure_1(condition_summary)
    make_figure_2(p99, cpu, monitor, events, cpu_summary)
    make_figure_3(cpu_summary)
    make_figure_4(cpu_summary)
    make_figure_5(topology)

    node = cpu_summary[(cpu_summary["condition"] == "autoscale") & (cpu_summary["resource_type"] == "node")].set_index("period")
