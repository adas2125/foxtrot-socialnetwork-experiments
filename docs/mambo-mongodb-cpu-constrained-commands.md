Run these blocks **in order, one step at a time**. Stop on a failed check. **LOCAL** changes local files/shell state. **READ-ONLY** inspects the cluster and saves evidence. **CLUSTER CHANGE** changes resources or sends traffic that updates application caches/traces. These commands are for you to run; preparing this document does not execute them.

**Starting checkpoint (2026-09-20):** one registered shard, controller `Idle`, bounds 1–1, CPU request/limit 200m/200m, and 524545 posts. Shard 1 finished draining and cleanup; its empty `post` database and StatefulSet were removed, and its PVC was retained. Use that state. Do not repeat seeding, CPU restoration, or the older calibration ladder.

| Phase | Offered load | Duration |
|---|---|---|
| Preparation, fixed one shard | 100 RPS | 120 seconds |
| Fixed-one baseline | 100 RPS | 300 seconds |
| Scaling and cleanup | 100 RPS | 10-minute runs, at most four |
| Settled two-shard measurement | 100 RPS | 300 seconds |

Allow roughly **30–45 minutes**, with a maximum traffic budget of **52 minutes**, plus checks. Completion depends on observed cleanup, not a fixed sleep. Each phase uses the existing wrk2 binary and its own histogram; collection adds short traffic gaps. This is a before/after capacity experiment, not an uninterrupted workload. Preserve every migration-run result. Unlike the original 200-RPS trial, this rerun stays at the subsequently selected **100 RPS** throughout.

Keep two threads, 32 connections, 200m per data member, the existing dataset, and FoxTrot scaling disabled. Use CPU target/tolerance **25/25**, window **1m**, cooldown **300s**, I/O-wait **30/5**. In the checked Mambo implementation this triggers above 50% CPU relative to requests (100m on one shard), or above 35% I/O wait. The CPU scale-down condition is below 0%, so this policy does not automatically scale down. Scaling is an outcome to observe, not guaranteed by 100 RPS.

1. **LOCAL:** start a child Bash shell. Paste this command **separately** so a failed check does not close your SSH login shell.

~~~bash
bash
~~~

**LOCAL:** reload helpers and create a new results directory. Keep the printed path for recovery.

~~~bash
set -euo pipefail
umask 077
export PATH="$HOME/.local/bin:$PATH"
cd /users/adas2125/foxtrot-socialnetwork-experiments
KCFG=/users/adas2125/.kube/amit.kubeconfig
CTX=amit@chirag-m5a-large-0803-215701.k8s.local
source utils/mambo-workload/shell-helpers.sh
source utils/mambo-workload/cpu-experiment.sh
DATASET_DIR=/users/adas2125/.local/state/foxtrot-socialnetwork-experiments/mambo-workload.Cc3WPN/dataset-512m
test -f "$DATASET_DIR/dataset.env"
CPU_DIR="$(mktemp -d "$DATASET_DIR/cpu-r100-clean.XXXXXX")"
cp "$DATASET_DIR/dataset.env" "$CPU_DIR/"
DATA_DIR="$CPU_DIR"
source "$DATA_DIR/dataset.env"
RPS=100
DB_CPU=200m
MONGO_CONTAINER=mongodb
printf 'CPU_DIR=%q\nDATA_DIR=%q\nKCFG=%q\nCTX=%q\nRPS=100\nDB_CPU=200m\nMONGO_CONTAINER=mongodb\n' \
  "$CPU_DIR" "$CPU_DIR" "$KCFG" "$CTX" > "$CPU_DIR/session.env"
printf 'Save this path: %s\n' "$CPU_DIR"
~~~

2. **READ-ONLY:** verify the fixed-one checkpoint, disabled competing autoscalers, and no existing wrk process. A failed guard means stop and inspect, not reset/reseed.

~~~bash
k -n foxtrot get rediscluster redis-cluster -o json |
  jq -e '.spec.autoScaleEnabled == false'
k -n social-network get hpa -o json | jq -e '.items | length == 0'
k -n mambo-socialnetwork get mongosautoscalers -o json | jq -e '.items | length == 0'
k -n mambo-socialnetwork get mongodautoscaler socialnetwork-sanity -o json |
  tee "$CPU_DIR/policy.before.json" |
  jq -e '.status.scalingPhase == "Idle" and
         .spec.shardScaleBounds == {minShards:1,maxShards:1} and
         .spec.replicaScaleBounds == {minReplicas:1,maxReplicas:1}'
k -n mambo-socialnetwork get statefulsets -o json |
  jq -e '[.items[].metadata.name | select(test("-shard[0-9]+-data$"))] ==
         ["sn-mongodb-sharded-shard0-data"]'
k -n mambo-socialnetwork get pod sn-mongodb-sharded-shard0-data-0 -o json |
  tee "$CPU_DIR/shard0.before.json" |
  jq -e 'any(.status.conditions[]; .type=="Ready" and .status=="True") and
         any(.spec.containers[]; .name=="mongodb" and
           .resources.requests.cpu=="200m" and .resources.limits.cpu=="200m")'
k -n social-network get pods -o wide
k -n social-network exec -i sn-wrk2 -c wrk2 -- python3 -B - <<'PY'
from pathlib import Path
running = []
for file in Path('/proc').glob('[0-9]*/comm'):
    try:
        if file.read_text().strip() == 'wrk':
            running.append(file.parent.name)
    except FileNotFoundError:
        pass
assert not running, f'An existing wrk process is running: {running}'
for name in ('/work/DeathStarBench/wrk2/wrk',
             '/work/mambo-workload/read-home-timeline.lua',
             '/work/mambo-workload/observe.py'):
    assert Path(name).is_file(), name
print('PASS: load generator files exist; no wrk process is running')
PY
cpu_ready 1 "$CPU_DIR/preflight"
~~~

`cpu_ready` checks policy/resources, ready pods, registered shards, data ownership totaling 524545, zero orphans, empty range-deletion queues, and CPU/I/O-wait metrics for every data member. An unavailable query fails the check. Its helpers are in [cpu-experiment.sh](../utils/mambo-workload/cpu-experiment.sh) and [cpu-results.py](../utils/mambo-workload/cpu-results.py).

3. **CLUSTER CHANGE:** extend the existing node-exporter DaemonSet to eligible nodes before adding a shard. This removes its hostname restriction; it may start additional exporter pods. It does not roll MongoDB. The change is to the live DaemonSet; a later Helm upgrade can overwrite it.

~~~bash
EXPORTER=sn-sanity-node-exporter-prometheus-node-exporter
k -n mambo-socialnetwork get daemonset "$EXPORTER" -o json \
  > "$CPU_DIR/exporter.before.json"
k -n mambo-socialnetwork patch daemonset "$EXPORTER" --type=merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":null}}}}'
k --request-timeout=6m -n mambo-socialnetwork rollout status \
  "daemonset/$EXPORTER" --timeout=5m
~~~

**READ-ONLY:** require more than one ready exporter and inspect placement. Later readiness checks require a metric series on the actual new data node.

~~~bash
k -n mambo-socialnetwork get daemonset "$EXPORTER" -o json |
  jq -e '.status.desiredNumberScheduled > 1 and
         .status.numberReady == .status.desiredNumberScheduled and
         .status.updatedNumberScheduled == .status.desiredNumberScheduled'
k -n mambo-socialnetwork get pods -o wide
~~~

4. **CLUSTER CHANGE (traffic/cache):** prepare and measure the fixed-one baseline. This block takes about seven minutes plus collection. Output is saved while wrk runs. No cache flushing is performed; different recorded seeds avoid replaying the same request prefix after every restart.

~~~bash
LOAD_SEED=1000
run_load 120 "$RPS" > "$CPU_DIR/preparation-r100.txt" 2>&1
LOAD_SEED=1001
cpu_trial fixed1 300
BASELINE_TRIAL="$TRIAL"
printf '%s\n' "$BASELINE_TRIAL" > "$CPU_DIR/baseline.path"
cpu_ready 1 "$CPU_DIR/baseline-ready"
~~~

5. **READ-ONLY / LOCAL:** review the baseline before allowing growth.

~~~bash
cat "$BASELINE_TRIAL/wrk2.txt"
python3 utils/mambo-workload/cpu-results.py summary "$BASELINE_TRIAL"
rg -F socialnetwork-sanity "$BASELINE_TRIAL/mambo.log" || true
jq -r '.utc as $time | .prometheus.cpu_cores_by_pod.data.result[] |
  select(.metric.pod | test("shard[0-9]+-data|post-storage-service|nginx-thrift|sn-wrk2")) |
  [$time,.metric.pod,.value[1]] | @tsv' "$BASELINE_TRIAL"/sample-*.json
~~~

Expect achieved throughput near 100 RPS, no HTTP failures, substantial cache misses, and CPU readings that sometimes exceed the 50% trigger. The earlier 100-RPS baseline was about 29 ms mean / 184 ms p99 with roughly 90% item misses; these are reference observations, not acceptance targets. If MongoDB is barely accessed, metrics are missing, or the workload fails, stop here. Preserve socket timeout counts: this wrk fork can count idle connections as timeouts, so those counts alone do not prove failed requests.

6. **CLUSTER CHANGE:** enable one-to-two scale-out. Recheck the clean baseline first. CPU and replica resources remain the same; only the maximum shard count changes.

~~~bash
cpu_ready 1 "$CPU_DIR/before-enable"
k -n foxtrot get rediscluster redis-cluster -o json |
  jq -e '.spec.autoScaleEnabled == false'
date -u +%FT%TZ > "$CPU_DIR/enable-scaling.utc"
k -n mambo-socialnetwork patch mongodautoscaler socialnetwork-sanity --type=merge \
  -p '{"spec":{"shardScaleBounds":{"minShards":1,"maxShards":2}}}'
k -n mambo-socialnetwork get mongodautoscaler socialnetwork-sanity -o json |
  tee "$CPU_DIR/policy.enabled.json" |
  jq -e '.spec.shardScaleBounds == {minShards:1,maxShards:2}'
~~~

7. **CLUSTER CHANGE (traffic/cache), with read-only completion checks:** run 100 RPS in ten-minute windows until migration **and cleanup** finish, at most forty minutes of traffic. Each window has its own latency histogram. This block prints progress between runs; do not launch a second load generator while it is running.

~~~bash
SETTLED=false
for attempt in 1 2 3 4; do
  printf '\nTransition run %s/4: 100 RPS for 10 minutes\n' "$attempt"
  LOAD_SEED="$((1100 + attempt))"
  cpu_trial "transition-$attempt" 600
  if cpu_ready 2 "$CPU_DIR/settled-check-$attempt"; then
    SETTLED=true
    break
  fi
  printf 'Not ready for settled measurement; continuing at the same RPS.\n'
done
if test "$SETTLED" != true; then
  printf 'STOP: scaling/cleanup was not verified within the budget. Results: %s\n' "$CPU_DIR" >&2
  exit 1
fi
date -u +%FT%TZ > "$CPU_DIR/settled-verified.utc"
~~~

The previous run reused a shard volume with pending cleanup and finished its load before data moved. That cleanup was completed during the reset. New migrations can still leave delayed cleanup on the donor. `Idle` or a Ready pod alone is insufficient. If the budget expires, do not call the result “settled”; inspect the saved checks and controller logs before continuing. Traffic has ended, but database background work may continue.

8. **CLUSTER CHANGE (policy, then traffic/cache):** hold two shards and measure five minutes at the **same 100 RPS**, after cleanup has been verified. No additional cache warmup or flush is needed; compare observed miss fractions.

~~~bash
k -n mambo-socialnetwork patch mongodautoscaler socialnetwork-sanity --type=merge \
  -p '{"spec":{"shardScaleBounds":{"minShards":2,"maxShards":2}}}'
cpu_ready 2 "$CPU_DIR/fixed2-before"
LOAD_SEED=1200
cpu_trial fixed2-settled 300
SETTLED_TRIAL="$TRIAL"
printf '%s\n' "$SETTLED_TRIAL" > "$CPU_DIR/settled.path"
cpu_ready 2 "$CPU_DIR/fixed2-after"
~~~

9. **READ-ONLY / LOCAL:** compare the fixed-one baseline and settled two-shard results. Keep the transition histograms separate; they show scaling cost.

~~~bash
BASELINE_TRIAL="$(cat "$CPU_DIR/baseline.path")"
SETTLED_TRIAL="$(cat "$CPU_DIR/settled.path")"
for trial in "$BASELINE_TRIAL" "$SETTLED_TRIAL"; do
  printf '\nRESULT: %s\n' "$trial"
  cat "$trial/run.env"
  cat "$trial/wrk2.txt"
  python3 utils/mambo-workload/cpu-results.py summary "$trial"
done
jq '.distribution' "$CPU_DIR/fixed2-after/mongo.json"
k -n mongodboperator-system logs deployment/mongodboperator-controller-manager \
  -c manager --since-time="$(cat "$CPU_DIR/enable-scaling.utc")" \
  > "$CPU_DIR/scaling-complete.log"
rg -F socialnetwork-sanity "$CPU_DIR/scaling-complete.log" || true
~~~

Compare mean/p95/p99, achieved RPS, HTTP/socket errors, cache misses, and per-shard CPU/throttling. Do not average percentiles across runs. The previous data split was approximately 79%/21%, so two shards need not divide work equally. Report any latency change alongside cache-miss changes and ownership skew; this single ordered run cannot by itself separate cache warming from scaling benefit. Repeat matched cases before making a general performance claim.

10. **READ-ONLY:** with traffic finished, verify the logical post count and paused topology. Keep both shards at 200m, bounds 2–2, and retain all PVCs/results. Returning to one shard later requires the controlled drain-and-cleanup procedure, not deleting a live shard.

~~~bash
k --request-timeout=2m -n mambo-socialnetwork \
  exec deployment/sn-mongodb-sharded-mongos -- bash -ec '
  exec /opt/bitnami/mongodb/bin/mongosh \
    --host 127.0.0.1 --port 27017 --username root \
    --password "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
    --authenticationDatabase admin --quiet --eval "$1"
' -- '
  const n=db.getSiblingDB("post").post.countDocuments({}, {maxTimeMS:90000});
  print("Logical posts: " + n);
  if(n!==524545) throw Error("Unexpected logical post count");
'
k -n mambo-socialnetwork get mongodautoscaler socialnetwork-sanity -o json |
  jq -e '.status.scalingPhase=="Idle" and
         .spec.shardScaleBounds=={minShards:2,maxShards:2}'
k -n foxtrot get rediscluster redis-cluster -o json |
  jq -e '.spec.autoScaleEnabled == false'
printf 'Experiment evidence: %s\n' "$CPU_DIR"
~~~

**Recovery only — LOCAL:** if your shell exits, start a child Bash again and substitute the saved directory below. Do not repeat completed load runs or create a new directory automatically. Check for an existing wrk process using step 2 before launching traffic again; an interrupted kubectl session does not prove remote traffic stopped.

~~~bash
export PATH="$HOME/.local/bin:$PATH"
cd /users/adas2125/foxtrot-socialnetwork-experiments
set -euo pipefail
umask 077
CPU_DIR=/replace/with/the/printed/cpu-r100-clean.directory
source "$CPU_DIR/session.env"
source "$CPU_DIR/dataset.env"
source utils/mambo-workload/shell-helpers.sh
source utils/mambo-workload/cpu-experiment.sh
~~~
