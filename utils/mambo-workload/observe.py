"""READ-ONLY: snapshot cache counters and Prometheus signals from sn-wrk2."""
import datetime
import json
import socket
import urllib.parse
import urllib.request


def snapshot():
    with socket.create_connection(
        ("post-storage-memcached.social-network.svc.cluster.local", 11211), timeout=10
    ) as sock:
        sock.sendall(b"stats\r\n")
        data = b""
        while not data.endswith(b"END\r\n"):
            chunk = sock.recv(65536)
            if not chunk:
                raise RuntimeError("Incomplete Memcached response")
            data += chunk
    stats = {}
    wanted = {"pid", "uptime", "get_hits", "get_misses", "evictions", "bytes", "curr_items", "limit_maxbytes"}
    for line in data.decode().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] in wanted:
            stats[parts[1]] = int(parts[2])
    queries = {
        "cpu_cores_by_pod": 'sum by (namespace,pod) (rate(container_cpu_usage_seconds_total{namespace=~"social-network|mambo-socialnetwork|foxtrot",container!="",container!="POD"}[1m]))',
        "throttled_seconds_per_second": 'sum by (namespace,pod) (rate(container_cpu_cfs_throttled_seconds_total{namespace=~"social-network|mambo-socialnetwork|foxtrot",container!="",container!="POD"}[1m]))',
        "working_set_bytes": 'sum by (namespace,pod) (container_memory_working_set_bytes{namespace=~"social-network|mambo-socialnetwork|foxtrot",container!="",container!="POD"})',
        "node_iowait_percent": '100 * sum by(instance)(rate(node_cpu_seconds_total{mode="iowait"}[5m])) / sum by(instance)(rate(node_cpu_seconds_total[5m]))',
    }
    metrics = {}
    for name, query in queries.items():
        url = "http://prometheus-operated.monitoring.svc.cluster.local:9090/api/v1/query?" + urllib.parse.urlencode({"query": query})
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                metrics[name] = json.load(response)
        except Exception as exc:
            metrics[name] = {"error": str(exc)}
    return {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "cache": stats, "prometheus": metrics}


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2))
