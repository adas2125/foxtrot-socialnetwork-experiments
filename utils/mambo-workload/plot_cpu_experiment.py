from datetime import datetime
import json
import math
from pathlib import Path
import re
import matplotlib.pyplot as plt

RUN_DIR = (
    Path(__file__).resolve().parents[2]
    / "analysis"
    / "mambo-cpu-r100"
)
OUTPUT_DIR = RUN_DIR / "plots"
TRIALS = [
    ("Baseline", "fixed1-r100.zG6DvV"),
    ("Transition 1", "transition-1-r100.Cpi4Hv"),
    ("Transition 2", "transition-2-r100.kr5dUU"),
    ("Transition 3", "transition-3-r100.4swhK6"),
    ("Settled", "fixed2-settled-r100.48lq4g"),
]

def read_json(path):
    return json.loads(path.read_text())

def timestamp(text):
    return datetime.fromisoformat(text.strip().replace("Z", "+00:00")).timestamp()

def save(fig, output, name):
    fig.tight_layout()
    fig.savefig(output / f"{name}.png", dpi=150)
    plt.close(fig)

def parse_wrk(path):
    """returns dict with mean, p50, p95, and p99 latency values in milliseconds"""
    text = path.read_text()
    # The detailed HDR spectrum and its Mean footer use milliseconds in this fork.
    mean = re.search(r"#\[Mean\s*=\s*([\d.]+)", text)
    if mean is None:
        raise ValueError(f"Missing latency summary: {path}")
    result = {"mean_ms": float(mean[1])}
    units = {"us": 0.001, "ms": 1, "s": 1000, "m": 60000, "h": 3600000}
    spectrum = [(float(value), float(percentile)) for value, percentile in re.findall(
        r"^\s*([\d.]+)\s+(0\.\d+|1\.0+)\s+\d+\s+\S+\s*$", text, re.M)]
    for percentile in (50, 95, 99):
        match = re.search(rf"^\s*{percentile}\.000%\s+([\d.]+)(us|ms|s|m|h)\b", text, re.M)
        if match:
            value = float(match[1]) * units[match[2]]
        else:
            # p95 is absent from the short table but present in the detailed spectrum.
            value = next((v for v, p in spectrum if abs(p - percentile / 100) < 1e-7), None)
        if value is None:
            raise ValueError(f"Missing p{percentile}: {path}")
        result[f"p{percentile}_ms"] = value
    return result

def collect_trials():
    """collects trial data from the run directories"""
    rows = []
    # looping through baseline, transitions, and settled phase
    for phase, name in TRIALS:
        directory = RUN_DIR / name
        env = dict(line.split("=", 1) for line in (directory / "run.env").read_text().splitlines() if "=" in line)

        # recording phase, directory, start, and duration
        row = {"phase": phase, "directory": name,
               "start_utc": (directory / "start.utc").read_text().strip(),
               "duration_seconds": int(env["DURATION_SECONDS"])}
        # adding mean, p50, p95, and p99 latency values from the wrk2 output
        row.update(parse_wrk(directory / "wrk2.txt"))
        rows.append(row)
    return rows

def plot_performance(rows, output):
    """plots latency performance for the collected trial rows"""
    fig, ax = plt.subplots(figsize=(10, 5))
    x = list(range(len(rows)))
    for field in ("mean_ms", "p50_ms", "p95_ms", "p99_ms"):
        ax.plot(x, [row[field] for row in rows], "o-", label=field.replace("_ms", ""))
    ax.set_yscale("log")
    ax.set_ylabel("Reported latency (ms, log scale)")
    ax.set_title("Latencies")
    ax.legend(ncol=4)
    ax.set_xticks(x)
    ax.set_xticklabels([r["phase"] for r in rows])
    save(fig, output, "01_performance")

def collect_events(root):
    events = []
    # reading the timestamps of the events
    for filename, description in (("enable-scaling.utc", "Scaling enabled"),
                                   ("settled-verified.utc", "Cleanup first verified")):
        if (root / filename).exists():
            print(f"root / filename: {root / filename}")
            events.append((timestamp((root / filename).read_text()), description))
    log = root / "scaling-complete.log"
    
    # obtaining timestamps of scaling completion events from log file
    if log.exists():
        for line in log.read_text().splitlines():
            for phrase, description in (("Scaling up shards", "Scale-up triggered"),
                                         ("Resharding completed; shard scaling-up process finished", "Controller completion")):
                match = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", line)
                if phrase in line and match:
                    events.append((timestamp(match[0]), description))
    return sorted(set(events))

def plot_cpu(root, rows, events, output):
    """plots CPU usage for the collected trial rows and events"""
    fig, ax = plt.subplots(figsize=(12, 6))
    origin = timestamp(rows[0]["start_utc"])
    colors = ("tab:blue", "tab:orange")
    for run_index, row in enumerate(rows):
        start = timestamp(row["start_utc"])
        left = (start - origin) / 60
        right = left + row["duration_seconds"] / 60
        ax.axvspan(left, right, color="gray", alpha=0.08)
        # ax.text((left + right) / 2, 1.02, row["phase"], ha="center", fontsize=8,
        #         transform=ax.get_xaxis_transform())

        # obtaining the samples files
        samples = sorted((root / row["directory"]).glob("sample-*.json"),
                         key=lambda p: int(p.stem.split("-")[-1]))
        points = {0: [], 1: []}
        for file in samples:
            sample = read_json(file)
            query = sample["prometheus"].get("cpu_cores_by_pod", {})
            result = query.get("data", {}).get("result", []) if query.get("status") == "success" else []

            # looping through the 2 shards
            for shard in points:
                # locating the pod for the current shard
                pod = f"sn-mongodb-sharded-shard{shard}-data-0"
                series = next((r for r in result if r["metric"].get("pod") == pod
                               and r["metric"].get("namespace") == "mambo-socialnetwork"), None)
                # obtaining time and value
                at = float(series["value"][0]) if series else timestamp(sample["utc"])
                value = float(series["value"][1]) * 1000 if series else math.nan

                # adding the (time, value) point to the shard's list
                points[shard].append(((at - origin) / 60, value if math.isfinite(value) else math.nan))
        
        # Plot runs independently: no interpolated CPU line across collection gaps.
        for shard, data in points.items():
            # adding to the plot for the current shard
            if data:
                x, y = zip(*data)
                ax.plot(x, y, "o-", markersize=3, color=colors[shard],
                        label=f"Shard {shard}" if run_index == 0 else None)
    
    # adding a horizontal line for the per-shard CPU limit
    ax.axhline(200, color="red", linestyle="--", label="Per-shard CPU limit: 200m")
    
    # adding vertical lines and annotations for events
    for i, (at, description) in enumerate(events):
        minute = (at - origin) / 60
        ax.axvline(minute, color="black", linestyle=":", alpha=0.5)
        ax.text(minute, 0.02 + (i % 2) * 0.18, description, rotation=90, fontsize=8,
                va="bottom", transform=ax.get_xaxis_transform())
    ax.set(xlabel="Minutes since baseline start", ylabel="CPU usage (millicores)",
           title="MongoDB CPU; shading marks nominal load windows", ylim=(0, None))
    ax.legend(loc="upper left")
    save(fig, output, "02_cpu_timeline")

def plot_ownership(root, output):
    checkpoints = ["baseline-ready", "fixed2-after"]
    observations = {0: [], 1: []}
    for checkpoint in checkpoints:
        path = root / checkpoint / "mongo.json"
        data = read_json(path)
        distribution = next((d for d in data.get("distribution", []) if d.get("ns") == "post.post"), None)
        if distribution is None:
            raise ValueError(f"Missing post.post distribution: {path}")
        shards = {s["shardName"]: s for s in distribution["shards"]}
        registered = {s["_id"] for s in data["shards"]}
        for shard in (0, 1):
            name = f"sn-mongodb-sharded-shard-{shard}"
            entry = shards.get(name)
            # Unregistered shards own no cluster documents; missing data for
            # a registered shard is unknown, rather than assumed zero.
            count = entry["numOwnedDocuments"] if entry else (0 if name not in registered else math.nan)
            observations[shard].append(count)
    fig, ax = plt.subplots(figsize=(8, 5))
    for shard, offset in ((0, -0.18), (1, 0.18)):
        ax.bar([i + offset for i in range(len(checkpoints))], observations[shard],
               width=0.35, label=f"Shard {shard}")
    ax.set(ylabel="Owned posts", title="MongoDB document ownership before and after scaling")
    ax.legend()
    ax.ticklabel_format(axis="y", style="plain")
    ax.set_xticks(range(len(checkpoints)))
    ax.set_xticklabels(checkpoints)
    save(fig, output, "04_document_ownership")


if __name__ == "__main__":
    rows = collect_trials()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # plotting the 3 figures
    plot_performance(rows, OUTPUT_DIR)
    plot_cpu(RUN_DIR, rows, collect_events(RUN_DIR), OUTPUT_DIR)
    plot_ownership(RUN_DIR, OUTPUT_DIR)
    print(f"PNG figures saved to {OUTPUT_DIR}")
