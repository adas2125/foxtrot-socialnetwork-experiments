Run one numbered step at a time in a Bash shell. Stop on failed checks. **LOCAL** changes local files or shell state. **READ-ONLY** inspects the cluster and may save local output. **CLUSTER CHANGE** changes container files, resources, or application/cache data. These commands are for you to run manually; they were not executed when preparing this document.

Continue from the existing [sanity setup](socialnetwork-mambo-sanity-commands.md): one MongoDB shard/member, three mongos routers, FoxTrot scaling disabled, and a working `sn-wrk2` pod. Find a repeatable MongoDB bottleneck by varying the working set and RPS, then test Mambo. Keep caching enabled.

1. **LOCAL:** start a child shell. Paste separately so failures do not close SSH.

~~~bash
bash
~~~

**LOCAL:** set the cluster and create a private results directory outside the repository.

~~~bash
set -euo pipefail
umask 077
cd /users/adas2125/foxtrot-socialnetwork-experiments
KCFG=/users/adas2125/.kube/amit.kubeconfig
CTX=amit@chirag-m5a-large-0803-215701.k8s.local
k() { kubectl --kubeconfig "$KCFG" --context "$CTX" --request-timeout=30s "$@"; }
h() { helm --kubeconfig "$KCFG" --kube-context "$CTX" "$@"; }
HELPERS=utils/mambo-workload
RESULTS="$HOME/.local/state/foxtrot-socialnetwork-experiments"
mkdir -p "$RESULTS"
RUN_DIR="$(mktemp -d "$RESULTS/mambo-workload.XXXXXX")"
printf 'RUN_DIR=%s\n' "$RUN_DIR"
~~~

2. **READ-ONLY:** verify the fixed baseline and save configuration. Do not reinstall releases. Resolve unready pods or an existing mongos autoscaling policy before continuing.

~~~bash
k -n social-network get pods -o wide
k -n mambo-socialnetwork get pods -o wide
k -n social-network wait --for=condition=Ready pod/sn-wrk2 --timeout=20s
k -n social-network exec sn-wrk2 -c wrk2 -- test -x /work/DeathStarBench/wrk2/wrk
k -n foxtrot get rediscluster redis-cluster -o json |
  tee "$RUN_DIR/foxtrot.before.json" |
  jq -e '.spec.autoScaleEnabled == false and .spec.masters == 4'
k -n mambo-socialnetwork get mongodautoscaler socialnetwork-sanity -o json |
  tee "$RUN_DIR/mambo.before.json" |
  jq -e '.spec.shardScaleBounds == {minShards:1,maxShards:1} and
         .spec.replicaScaleBounds == {minReplicas:1,maxReplicas:1}'
k -n mambo-socialnetwork get mongosautoscalers -o json | jq -e '.items | length == 0'
k -n social-network get deployments,hpa -o yaml > "$RUN_DIR/application.before.yaml"
k -n mambo-socialnetwork get deployments,statefulsets -o yaml > "$RUN_DIR/mongodb.before.yaml"
mongo_read() {
  k -n mambo-socialnetwork exec deployment/sn-mongodb-sharded-mongos -- bash -ec '
    exec /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
      --username root --password "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet --eval "$1"
  ' -- "$1"
}
mongo_read 'const s=db.adminCommand({listShards:1}); if(s.ok!==1 || s.shards.length!==1) throw Error("Expected one shard"); printjson(s);'
~~~

3. **CLUSTER CHANGE:** install loader dependencies in the existing load-generator container and copy the [helpers](../utils/mambo-workload/). This does not start traffic or insert data.

~~~bash
k --request-timeout=10m -n social-network exec sn-wrk2 -c wrk2 -- bash -ec '
  apt-get update
  apt-get install -y --no-install-recommends python3-pymongo python3-redis
  python3 -B -c "from pymongo import MongoClient; from redis.cluster import RedisCluster"
  mkdir -p /work/mambo-workload
'
for file in seed.py read-home-timeline.lua observe.py; do
  k -n social-network exec -i sn-wrk2 -c wrk2 -- \
    bash -ec 'cat > "/work/mambo-workload/$1"' -- "$file" < "$HELPERS/$file"
done
cp -a "$HELPERS" "$RUN_DIR/helpers"
~~~

4. **LOCAL:** define reusable commands. Calling `seed_dataset` inserts new posts/timelines; `run_load` sends HTTP traffic; `observe` only reads metrics. `measure` saves a run and samples metrics approximately once per minute.

~~~bash
seed_dataset() {
  printf 'BASE=%s\nREADERS=%s\nPOSTS=%s\nTEXT_BYTES=1024\nSEED=42\n' \
    "$BASE" "$READERS" "$POSTS" > "$DATA_DIR/dataset.env"
  k -n mambo-socialnetwork get secret sn-mongodb-sharded -o json |
    jq -r '.data["mongodb-root-password"] | @base64d' |
    k --request-timeout=30m -n social-network exec -i sn-wrk2 -c wrk2 -- \
      python3 -B /work/mambo-workload/seed.py \
      --base "$BASE" --readers "$READERS" --posts "$POSTS" --seed 42 |
    tee "$DATA_DIR/seed.log"
}
run_load() {
  k --request-timeout=25m -n social-network exec sn-wrk2 -c wrk2 -- \
    env READER_BASE="$BASE" READERS="$READERS" POSTS="$POSTS" PAGE_SIZE=10 SEED=42 \
    /work/DeathStarBench/wrk2/wrk -D exp -t2 -c32 -d"${1}s" -R"$2" -L \
    -s /work/mambo-workload/read-home-timeline.lua \
    http://nginx-thrift.social-network.svc.cluster.local:8080
}
observe() {
  k --request-timeout=1m -n social-network exec sn-wrk2 -c wrk2 -- \
    python3 -B /work/mambo-workload/observe.py
}
verify_response() {
  k -n social-network exec sn-wrk2 -c wrk2 -- curl -fsS --max-time 15 \
    "http://nginx-thrift.social-network.svc.cluster.local:8080/wrk2-api/home-timeline/read?user_id=$1&start=0&stop=10" |
    jq -e --arg reader "$1" --arg prefix "mambo-$BASE-" \
      'length == 10 and all(.[]; .creator.user_id == $reader and (.text | startswith($prefix)))'
}
measure() {
  local label="$1" seconds="$2" load_pid sample=0
  TRIAL="$(mktemp -d "$DATA_DIR/${label}-r${RPS}.XXXXXX")"
  cp "$DATA_DIR/dataset.env" "$TRIAL/"
  printf 'RPS=%s\nTHREADS=2\nCONNECTIONS=32\nDURATION_SECONDS=%s\n' \
    "$RPS" "$seconds" > "$TRIAL/run.env"
  observe > "$TRIAL/before.json"
  date -u +%FT%TZ > "$TRIAL/start.utc"
  run_load "$seconds" "$RPS" > "$TRIAL/wrk2.txt" 2>&1 &
  load_pid=$!
  while kill -0 "$load_pid" 2>/dev/null; do
    sample=$((sample + 1))
    observe > "$TRIAL/sample-$sample.json"
    k -n mambo-socialnetwork get pods -o wide > "$TRIAL/pods-$sample.txt"
    sleep 60
  done
  wait "$load_pid"
  date -u +%FT%TZ > "$TRIAL/end.utc"
  observe > "$TRIAL/after.json"
  cat "$TRIAL/wrk2.txt"
}
~~~

5. **CLUSTER CHANGE:** load a small pilot and verify the HTTP path. New ID ranges are checked before insertion. If loading fails, inspect the saved parameters/progress; do not blindly rerun it. The loader writes BSON Int64 IDs and exact string IDs in Redis.

~~~bash
DATA_DIR="$RUN_DIR/pilot"
mkdir -p "$DATA_DIR"
BASE="$((10000000000 + $(date +%s)))"
READERS=4
POSTS=64
seed_dataset
verify_response "$BASE"
verify_response "$((BASE + READERS - 1))"
run_load 30 10 2>&1 | tee "$DATA_DIR/wrk2-r10.txt"
~~~

6. **CLUSTER CHANGE:** create the first large candidate: 1,024 readers × 512 distinct posts × 1 KiB varied text (approximately 512 MiB of text). This exceeds the current 64 MiB Memcached capacity; measure actual misses. Direct seeding tests HomeTimeline reads, not compose/follow behavior.

~~~bash
DATA_DIR="$RUN_DIR/dataset-512m"
mkdir -p "$DATA_DIR"
BASE="$((10000000000 + $(date +%s)))"
READERS=1024
POSTS=512
seed_dataset
verify_response "$BASE"
verify_response "$((BASE + READERS / 2))"
verify_response "$((BASE + READERS - 1))"
~~~

7. **CLUSTER CHANGE:** measure one RPS level with fixed capacity. Start at 50 RPS. Review each result before repeating at 100, 200, or another chosen rate. Each run has five minutes of preparation and ten minutes of measurement; extend preparation if cache behavior has not stabilized. Keep thread/connection counts consistent unless the generator is the bottleneck.

~~~bash
RPS=50
run_load 300 "$RPS" > "$DATA_DIR/warmup-r$RPS-$(date +%s).txt" 2>&1
measure fixed1 600
~~~

8. **READ-ONLY:** inspect misses, utilization, errors, and Mambo's inputs. CPU in Mambo logs is normalized to requests, not limits. Empty Prometheus results are missing evidence, not zero utilization.

~~~bash
python3 - "$TRIAL" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
a = json.loads((p / "before.json").read_text())["cache"]
b = json.loads((p / "after.json").read_text())["cache"]
assert b["pid"] == a["pid"] and b["uptime"] > a["uptime"], "Cache restarted"
hits = b["get_hits"] - a["get_hits"]
misses = b["get_misses"] - a["get_misses"]
assert hits >= 0 and misses >= 0 and hits + misses > 0
print({"hits": hits, "misses": misses, "item_miss_fraction": misses / (hits + misses),
       "evictions": b["evictions"] - a["evictions"], "cache_bytes": b["bytes"]})
PY
jq '.prometheus' "$TRIAL/after.json"
k -n mongodboperator-system logs deployment/mongodboperator-controller-manager \
  -c manager --since-time="$(cat "$TRIAL/start.utc")" > "$TRIAL/mambo.log"
rg -F socialnetwork-sanity "$TRIAL/mambo.log" || true
k -n social-network get pods -o wide > "$TRIAL/application-pods.txt"
~~~

Repeat steps 7–8, changing one variable. A cache item miss is not a database operation: PostStorage batches missing posts. Confirm the MongoDB path with sampled traces as in the sanity procedure. A Memcached miss can still hit MongoDB's own cache.

| Result | Next run |
| --- | --- |
| Misses disappear after preparation | Expand distinct post bodies using a fresh dataset directory and ID range |
| Misses persist; MongoDB has headroom | Increase RPS |
| Latency rises; MongoDB stays lightly loaded | Inspect application, Redis, network, and generator bottlenecks |
| MongoDB pressure and latency rise together | Repeat at that RPS before enabling growth |
| Errors or delivered-RPS shortfall | Record overload; do not judge only successful-request latency |

9. **CLUSTER CHANGE — after selecting a repeatable MongoDB-heavy workload:** extend node-exporter coverage before allowing new shard placement. This expands the existing exporter to eligible nodes.

~~~bash
h upgrade sn-sanity-node-exporter prometheus-node-exporter \
  --repo https://prometheus-community.github.io/helm-charts --version 4.56.1 \
  --namespace mambo-socialnetwork --reuse-values --set-json 'nodeSelector=null' \
  --wait --timeout 10m
~~~

**READ-ONLY:** verify exporter placement and populated I/O-wait metrics for every potential data node before continuing.

~~~bash
k -n mambo-socialnetwork get daemonsets,pods -o wide
observe > "$DATA_DIR/before-scaling.json"
jq '.prometheus.node_iowait_percent' "$DATA_DIR/before-scaling.json"
~~~

10. **CLUSTER CHANGE:** allow Mambo to scale between one and two shards, retaining one member per shard. These bounds also allow scale-down after load stops. Keep FoxTrot scaling disabled. Use the selected `RPS` and the same dataset. The policy has a five-minute metric window and 300-second cooldown.

~~~bash
mongo_read 'const s=db.adminCommand({listShards:1}); if(s.ok!==1 || s.shards.length!==1) throw Error("Expected one starting shard");'
k -n foxtrot get rediscluster redis-cluster -o json | jq -e '.spec.autoScaleEnabled == false'
k -n mambo-socialnetwork patch mongodautoscaler socialnetwork-sanity --type=merge \
  -p '{"spec":{"shardScaleBounds":{"minShards":1,"maxShards":2},"replicaScaleBounds":{"minReplicas":1,"maxReplicas":1}}}'
measure mambo 1200
~~~

11. **READ-ONLY:** repeat step 8, then verify actual membership and data distribution. A new pod alone does not demonstrate useful scaling. Extend the test if provisioning or migration is still underway.

~~~bash
mongo_read 'printjson(db.adminCommand({listShards:1})); db.getSiblingDB("post").post.getShardDistribution();' \
  > "$TRIAL/shard-distribution.txt"
cat "$TRIAL/shard-distribution.txt"
k -n mambo-socialnetwork get mongodautoscaler socialnetwork-sanity -o yaml \
  > "$TRIAL/policy.after.yaml"
~~~

12. **CLUSTER CHANGE — optional fixed-larger comparison:** after two shards are present, hold the bounds at two. Verify useful data distribution before measuring. Use this result to check whether more capacity actually helps.

~~~bash
mongo_read 'const s=db.adminCommand({listShards:1}); if(s.ok!==1 || s.shards.length!==2) throw Error("Expected two shards before holding bounds");'
k -n mambo-socialnetwork patch mongodautoscaler socialnetwork-sanity --type=merge \
  -p '{"spec":{"shardScaleBounds":{"minShards":2,"maxShards":2}}}'
run_load 300 "$RPS" > "$DATA_DIR/warmup-fixed2-r$RPS-$(date +%s).txt" 2>&1
measure fixed2 600
~~~

Repeat step 8. Compare equal steady-state windows; the whole-run autoscaling histogram includes the transition. Repeat valid fixed-capacity measurements at least three times and preserve unsuccessful results. Extra replica members alone are not the target because the application currently reads primaries.

A repeated one-to-two scaling trial needs a separately planned return to one shard. Do not delete shard pods/PVCs to reset it, flush FoxTrot keys, or clear shared caches. Keep results and datasets between sessions. On resuming, recover the original `RUN_DIR`/`DATA_DIR` and source its saved `dataset.env`; do not rerun insertion steps.
