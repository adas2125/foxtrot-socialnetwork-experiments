"""LOCAL: validate saved readiness snapshots or summarize a completed CPU trial."""
import json
import math
from pathlib import Path
import sys


def read(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def data_pods(snapshot):
    return {
        p['metadata']['name']: p for p in snapshot['items']
        if p['metadata']['name'].startswith('sn-mongodb-sharded-shard')
        and p['metadata']['name'].endswith('-data-0')
    }


def ready(path, count):
    require(count in (1, 2), 'Expected one or two shards')
    policy = read(path / 'policy.json')
    require(policy['status'].get('scalingPhase') == 'Idle', 'Controller is not Idle')
    spec = policy['spec']
    require(spec['replicaScaleBounds'] == {'minReplicas': 1, 'maxReplicas': 1}, 'Unexpected replica bounds')
    bounds = spec['shardScaleBounds']
    require(bounds == {'minShards': 1, 'maxShards': 1} if count == 1 else
            bounds in ({'minShards': 1, 'maxShards': 2}, {'minShards': 2, 'maxShards': 2}),
            'Unexpected shard bounds')
    for key, value in {'cpuTargetPercent': 25, 'cpuTolerancePercent': 25,
                       'iowaitTargetPercent': 30, 'iowaitTolerancePercent': 5,
                       'window': '1m', 'cooldownSeconds': 300}.items():
        require(spec['policy'].get(key) == value, f'Unexpected policy: {key}')
    mongo = read(path / 'mongo.json')
    names = {f'sn-mongodb-sharded-shard-{i}' for i in range(count)}
    require({s['_id'] for s in mongo['shards']} == names, 'Registered shard count/name mismatch')
    require(mongo['balance'].get('balancerCompliant') is True, 'Collection is still imbalanced')
    require(len(mongo['distribution']) == 1, 'Missing/ambiguous post.post statistics')
    shards = mongo['distribution'][0]['shards']
    require({s['shardName'] for s in shards} == names, 'Data has not reached every shard')
    require(all(s['numOwnedDocuments'] > 0 and s['numOrphanedDocs'] == 0 for s in shards),
            'Empty shard or orphan cleanup still present')
    require(sum(s['numOwnedDocuments'] for s in shards) == 524545, 'Owned-document total is not 524545')
    pods = data_pods(read(path / 'pods.json'))
    require(set(pods) == {f'sn-mongodb-sharded-shard{i}-data-0' for i in range(count)},
            'Unexpected data pods')
    metrics = read(path / 'metrics.json')['prometheus']
    for metric in ('cpu_cores_by_pod', 'node_iowait_percent'):
        require(metrics[metric].get('status') == 'success', f'Missing metric query: {metric}')
    cpu = {r['metric']['pod'] for r in metrics['cpu_cores_by_pod']['data']['result']
           if r['metric'].get('namespace') == 'mambo-socialnetwork' and math.isfinite(float(r['value'][1]))}
    io = {r['metric']['instance'] for r in metrics['node_iowait_percent']['data']['result']
          if math.isfinite(float(r['value'][1]))}
    for i in range(count):
        pod = pods[f'sn-mongodb-sharded-shard{i}-data-0']
        require(any(c['type'] == 'Ready' and c['status'] == 'True'
                    for c in pod['status'].get('conditions', [])), 'Data pod not ready')
        container = next(c for c in pod['spec']['containers'] if c['name'] == 'mongodb')
        require(container['resources']['requests'] == {'cpu': '200m', 'memory': '512Mi'}
                and container['resources']['limits'] == {'cpu': '200m', 'memory': '1Gi'},
                'Data-member resources differ from the baseline')
        require(pod['metadata']['name'] in cpu, 'Missing data-pod CPU series')
        require(pod['status']['hostIP'] + ':9100' in io, 'Missing data-node I/O-wait series')
        require(read(path / f'cleanup-{i}.json')['pendingCleanup'] == 0, 'Range deletion tasks remain')
    print('PASS: ready, populated, clean shards with CPU and I/O-wait metrics')
    for shard in shards:
        print(shard['shardName'], 'owned documents:', shard['numOwnedDocuments'])


def summary(path):
    before, after = (read(path / name)['cache'] for name in ('before.json', 'after.json'))
    require(before['pid'] == after['pid'] and after['uptime'] > before['uptime'], 'Cache restarted or bad interval')
    hits = after['get_hits'] - before['get_hits']
    misses = after['get_misses'] - before['get_misses']
    require(hits >= 0 and misses >= 0 and hits + misses > 0, 'Invalid cache deltas')
    print('item_miss_fraction:', misses / (hits + misses))
    a, b = (path / name for name in ('cpu.before', 'cpu.after'))
    pods_a, pods_b = (data_pods(read(p / 'pods.json')) for p in (a, b))
    for name in sorted(pods_a.keys() | pods_b.keys()):
        if name not in pods_a or name not in pods_b:
            print(name, 'added/removed during trial; no whole-trial cgroup delta')
            continue
        pa, pb = pods_a[name], pods_b[name]
        identity = lambda p: (p['metadata']['uid'], [(c['name'], c['restartCount'], c.get('containerID'))
                                                     for c in p['status']['containerStatuses']])
        require(identity(pa) == identity(pb), f'{name}: pod/container restarted; comparison invalid')
        index = name[len('sn-mongodb-sharded-shard'):-len('-data-0')]
        if not all((p / f'cgroup-{index}.txt').is_file() for p in (a, b)):
            print(name, 'not running at a boundary; no whole-trial cgroup delta')
            continue
        counters = lambda p: {k: int(v) for k, v in (line.split() for line in p.read_text().splitlines())}
        ca, cb = (counters(p / f'cgroup-{index}.txt') for p in (a, b))
        delta = {key: cb[key] - ca[key] for key in ca.keys() & cb.keys()}
        require(all(v >= 0 for v in delta.values()), f'{name}: CPU counters reset')
        print(name, {k: v for k, v in delta.items()
                     if k in ('nr_periods', 'nr_throttled', 'throttled_usec', 'throttled_time')})
        if delta.get('nr_periods', 0):
            print('throttled_period_fraction:', delta['nr_throttled'] / delta['nr_periods'])
    print('Full wrk2 latency/error output:', path / 'wrk2.txt')


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'ready':
            ready(Path(sys.argv[2]), int(sys.argv[3]))
        elif sys.argv[1] == 'summary':
            summary(Path(sys.argv[2]))
        else:
            raise ValueError('Use ready DIRECTORY COUNT or summary TRIAL')
    except (ValueError, KeyError, StopIteration, OSError) as exc:
        sys.exit(f'CHECK FAILED: {exc}')
