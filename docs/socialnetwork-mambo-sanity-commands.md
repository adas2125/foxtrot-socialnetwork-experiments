Run these blocks in order in one Bash shell. Stop on a failed check. **LOCAL** changes only local files, shell state, or forwarding processes. **READ-ONLY** does not change cluster resources or application data; it may save local output. **CLUSTER CHANGE** changes resources, database configuration, or application/cache data. These are commands for you to run manually.

**LOCAL:** start a child shell first, so a failed check exits that shell instead of closing SSH. Paste this block separately. After a failure, restore the original run directory and variables; do not repeat installation or fixture creation.

~~~bash
set +e
bash
~~~

Helper scripts and the Mambo policy are already in [utils/sanity](../utils/sanity/); the commands below do not generate script or policy files.

1. **LOCAL:** set paths and deployment names. `SN_NS` and `MONGO_NS` are Kubernetes namespaces; `MONGO_RELEASE` is our chosen Helm release name. Assignments create no cluster resources. `RUN_DIR` is a unique temporary directory for downloaded charts, rendered YAML, saved responses, traces, and logs; it is not durable experiment storage.

~~~bash
cd /users/adas2125/foxtrot-socialnetwork-experiments
set -euo pipefail # Stop the child shell if a command or check fails.
KCFG=/users/adas2125/.kube/amit.kubeconfig
CTX=amit@chirag-m5a-large-0803-215701.k8s.local
SN_NS=social-network
MONGO_NS=mambo-socialnetwork
MONGO_RELEASE=sn-mongodb-sharded
MONGO_VALUES=/users/adas2125/Autoscaling/MongoDB/values.yaml
SN_CHART=/users/adas2125/DeathStarBench/socialNetwork/helm-chart/socialnetwork
RUN_DIR="$(mktemp -d /tmp/socialnetwork-sanity.XXXXXX)"
printf 'RUN_DIR=%s\n' "$RUN_DIR"
JAEGER_API="/api/v1/namespaces/$SN_NS/services/http:jaeger:16686/proxy/api/traces"
k() { kubectl --kubeconfig "$KCFG" --context "$CTX" "$@"; }
h() { helm --kubeconfig "$KCFG" --kube-context "$CTX" "$@"; }
~~~

2. **READ-ONLY:** check existing namespaces/releases, then the prerequisites. This sequence expects fresh SocialNetwork/MongoDB deployments and the existing four-master FoxTrot cluster. Missing namespaces are created by the later installs. Do not run the old baseline or scaling scripts.

~~~bash
k get namespaces "$SN_NS" "$MONGO_NS" --ignore-not-found
h list -A --filter "^(sn-sanity|$MONGO_RELEASE)$"
h list --all --namespace "$SN_NS" --filter '^sn-sanity$' -o json | jq -e 'length == 0'
h list --all --namespace "$MONGO_NS" --filter "^($MONGO_RELEASE|sn-sanity-node-exporter)$" -o json | jq -e 'length == 0'
k get nodes -o wide
k get storageclass kops-csi-1-21
k -n mongodboperator-system get deployment mongodboperator-controller-manager
k -n redis-operator-system get deployments
k -n monitoring get service prometheus-operated
k get mongodautoscalers.autoscaler.mongodb.io,mongosautoscalers.autoscaler.mongodb.io -A -o json |
  jq -e --arg ns "$MONGO_NS" '[.items[] | select(.metadata.namespace == $ns)] | length == 0'
k get deployments,statefulsets -A -o json |
  jq -e --arg sn "$SN_NS" --arg mongo "$MONGO_NS" \
  '[.items[] | select(.metadata.namespace == $sn or .metadata.namespace == $mongo)] | length == 0'
# Refuse to reuse old MongoDB volumes; cleanup below relies on this check.
k get pvc -A -o json |
  jq -e --arg ns "$MONGO_NS" --arg prefix "datadir-$MONGO_RELEASE-" \
  '[.items[] | select(.metadata.namespace == $ns and (.metadata.name | startswith($prefix)))] | length == 0'
TRIAL_PVCS_FRESH=true
printf '%s\n' "$TRIAL_PVCS_FRESH" > "$RUN_DIR/trial-pvcs-fresh.txt"
k -n foxtrot get rediscluster redis-cluster -o json |
  jq -e '.spec.autoScaleEnabled == false and .spec.masters == 4'

redis_read() { k -n foxtrot exec redis-cluster-0 -c redis -- redis-cli -c --raw "$@"; }
redis_read CLUSTER INFO | tr -d '\r' | tee "$RUN_DIR/redis-info.before.txt"
grep -qx 'cluster_state:ok' "$RUN_DIR/redis-info.before.txt"
grep -qx 'cluster_size:4' "$RUN_DIR/redis-info.before.txt"
grep -qx 'cluster_slots_assigned:16384' "$RUN_DIR/redis-info.before.txt"

redis_keys() {
  for p in redis-cluster-0 redis-cluster-1 redis-cluster-2 redis-cluster-6; do
    k -n foxtrot exec "$p" -c redis -- redis-cli --raw CLUSTER NODES |
      awk '$3 ~ /myself/ && $3 ~ /master/ && NF >= 9 {found=1} END {exit !found}'
    k -n foxtrot exec "$p" -c redis -- redis-cli --scan
  done | LC_ALL=C sort -u
}
redis_keys > "$RUN_DIR/redis-keys.before.txt"
test "$(wc -l < "$RUN_DIR/redis-keys.before.txt")" -eq 962
~~~

3. **LOCAL:** download chart 9.4.12, then render YAML without installing anything. The version and MongoDB values follow the [previous rebalancing experiment](../../NoSQLMark-experiments/docs/experiment_overview_rebalancing_latency.md); downloading first lets render and install use the same archive. SocialNetwork applies the existing FoxTrot values followed by the sanity overlay.

~~~bash
helm pull mongodb-sharded --repo https://charts.bitnami.com/bitnami \
  --version 9.4.12 --destination "$RUN_DIR" --kubeconfig "$KCFG" --kube-context "$CTX"
h template "$MONGO_RELEASE" "$RUN_DIR/mongodb-sharded-9.4.12.tgz" \
  --namespace "$MONGO_NS" -f "$MONGO_VALUES" > "$RUN_DIR/mongodb-rendered.yaml"
h template sn-sanity "$SN_CHART" --namespace "$SN_NS" \
  -f manifests/socialnetwork-foxtrot-values.yaml \
  -f manifests/socialnetwork-sanity-values.yaml \
  --set-string jaeger.container.imageVersion=1.62.0 > "$RUN_DIR/socialnetwork-rendered.yaml"
~~~

4. **CLUSTER CHANGE:** install only the new MongoDB release. The existing values retain three mongos routers.

~~~bash
h install "$MONGO_RELEASE" "$RUN_DIR/mongodb-sharded-9.4.12.tgz" \
  --namespace "$MONGO_NS" --create-namespace -f "$MONGO_VALUES" \
  --wait --timeout 15m
~~~

**READ-ONLY:** inspect readiness, Secret key names, and the shard count. `mongo_read` is a shell helper; the queries passed to it here only inspect MongoDB.

~~~bash
k -n "$MONGO_NS" get deployment,statefulset,pod,service,pvc
k -n "$MONGO_NS" get secret "$MONGO_RELEASE" -o json |
  jq -e '.data | has("mongodb-root-password") and has("mongodb-replica-set-key")'
mongo_read() {
  k -n "$MONGO_NS" exec deployment/"$MONGO_RELEASE"-mongos -- bash -ec '
    exec /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
      --username root --password "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet --eval "$1"
  ' -- "$1"
}
mongo_read 'if (db.adminCommand({hello:1}).msg !== "isdbgrid") throw Error("Expected mongos"); const s=db.adminCommand({listShards:1}); if (s.ok !== 1 || s.shards.length !== 1) throw Error("Expected one shard"); printjson(s);'
~~~

5. **CLUSTER CHANGE:** run [init-sanity-mongodb.js](../utils/sanity/init-sanity-mongodb.js) through mongos. It prepares database `post`, collection `post`, a unique `post_id` index, and a hashed `post_id` shard key, then verifies them. It inserts no posts and adds no shards.

~~~bash
k -n "$MONGO_NS" exec -i deployment/"$MONGO_RELEASE"-mongos -- bash -ec '
  exec /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
    --username root --password "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
    --authenticationDatabase admin --quiet --file /dev/stdin
' < utils/sanity/init-sanity-mongodb.js
~~~

6. **CLUSTER CHANGE:** install SocialNetwork. Pin Jaeger before application startup: the legacy UDP receiver used by these clients was removed in [Jaeger 1.63](https://github.com/jaegertracing/jaeger/releases/tag/v1.63.0). Check startup/index creation; do not continue after authentication or driver errors.

~~~bash
h install sn-sanity "$SN_CHART" --namespace "$SN_NS" --create-namespace \
  -f manifests/socialnetwork-foxtrot-values.yaml \
  -f manifests/socialnetwork-sanity-values.yaml \
  --set-string jaeger.container.imageVersion=1.62.0 --wait --timeout 15m
~~~

**READ-ONLY:** inspect startup logs and deployments/HPAs. Jaeger should report version 1.62.0 and "Starting agent". Replacing Jaeger after application startup can disconnect these old clients' UDP sockets; in that recovery case, restart post-storage-service, home-timeline-service, then nginx-thrift and refresh its local forward before testing again.

~~~bash
k -n "$SN_NS" logs deployment/post-storage-service --tail=80
k -n "$SN_NS" logs deployment/home-timeline-service --tail=40
k -n "$SN_NS" logs deployment/jaeger --tail=60
k -n "$SN_NS" get deployments,hpa
~~~

7. **LOCAL:** open forwarding connections. The Python helper waits until all three local ports accept connections. Trace verification below uses the Kubernetes API, so it does not depend on the local Jaeger forward. Refresh a forward after replacing its target pod, and update PF_PIDS with the new PID.

~~~bash
PF_PIDS=""
k -n "$SN_NS" port-forward service/nginx-thrift 18080:8080 > "$RUN_DIR/nginx-forward.log" 2>&1 &
PF_PIDS="$PF_PIDS $!"
k -n "$SN_NS" port-forward service/post-storage-memcached 11212:11211 > "$RUN_DIR/cache-forward.log" 2>&1 &
PF_PIDS="$PF_PIDS $!"
k -n "$SN_NS" port-forward service/jaeger 16686:16686 > "$RUN_DIR/jaeger-forward.log" 2>&1 &
PF_PIDS="$PF_PIDS $!"
python3 utils/sanity/wait_for_ports.py
~~~

8. **READ-ONLY:** verify one ready data member and one shard before attaching Mambo.

~~~bash
k -n "$MONGO_NS" get statefulset "$MONGO_RELEASE"-shard0-data -o json |
  jq -e '.spec.replicas == 1 and .status.readyReplicas == 1'
mongo_read 'const s=db.adminCommand({listShards:1}); if (s.ok !== 1 || s.shards.length !== 1) throw Error("Expected one shard"); print("One shard verified");'
~~~

**CLUSTER CHANGE:** this cluster lacked node exporter, which Mambo needs for I/O-wait metrics. Install a trial exporter on the MongoDB data node, following the previous experiment's chart version. This single-node setup is for the fixed-topology trial.

~~~bash
DATA_NODE="$(k -n "$MONGO_NS" get pod "$MONGO_RELEASE-shard0-data-0" -o jsonpath='{.spec.nodeName}')"
DATA_HOSTNAME="$(k get node "$DATA_NODE" -o jsonpath='{.metadata.labels.kubernetes\.io/hostname}')"
h install sn-sanity-node-exporter prometheus-node-exporter \
  --repo https://prometheus-community.github.io/helm-charts --version 4.56.1 \
  --namespace "$MONGO_NS" \
  --set prometheus.monitor.enabled=true \
  --set prometheus.monitor.additionalLabels.release=kps \
  --set-string "nodeSelector.kubernetes\.io/hostname=${DATA_HOSTNAME:?Missing hostname}" \
  --wait --timeout 5m
~~~

**CLUSTER CHANGE:** apply the existing policy file with shard and member bounds fixed at 1–1. Its namespace/release must match `MONGO_NS`/`MONGO_RELEASE`; changing shell variables does not rewrite this file or the SocialNetwork overlay.

~~~bash
k -n "$MONGO_NS" apply -f utils/sanity/mongodautoscaler-sanity.yaml
~~~

**READ-ONLY:** allow Prometheus scraping and Mambo reconciliation to complete. The scoped log must show CPU, I/O-wait, one shard, and one member. Fixed bounds need not populate status metrics.

~~~bash
for attempt in $(seq 1 24); do
  k -n mongodboperator-system logs deployment/mongodboperator-controller-manager \
    -c manager --since=10m > "$RUN_DIR/mambo.log"
  if grep -F 'No datanode scaling action' "$RUN_DIR/mambo.log" |
     grep -F "$MONGO_NS" | grep -F socialnetwork-sanity; then
    break
  fi
  sleep 5
done
grep -F 'No datanode scaling action' "$RUN_DIR/mambo.log" |
  grep -F "$MONGO_NS" | grep -F socialnetwork-sanity
~~~

9. **READ-ONLY:** choose local test names and check that both user IDs are unused before writing test data.

~~~bash
A="$(date +%s)"
B="$((A + 1))"
USER_A="sanity_a_$A"
USER_B="sanity_b_$B"
MARKER="sn-sanity-$A"
TEST_PASSWORD=sanity-test-password
printf '%s\n' "$A" > "$RUN_DIR/author.id"
printf '%s\n' "$B" > "$RUN_DIR/reader.id"
test "$(redis_read EXISTS "$A")" = 0
test "$(redis_read EXISTS "$B")" = 0
k -n "$SN_NS" exec deployment/user-mongodb -- mongo --quiet user --eval \
  "if (db.user.countDocuments({\$or:[{user_id:{\$in:[NumberLong('$A'),NumberLong('$B')]}},{username:{\$in:['$USER_A','$USER_B']}}]}) !== 0) quit(1)"
~~~

10. **CLUSTER CHANGE:** register A/B, make B follow A, and compose one plain-text post. Run this block once.

~~~bash
curl -fsS http://127.0.0.1:18080/wrk2-api/user/register \
  --data-urlencode first_name=Sanity --data-urlencode last_name=Author \
  --data-urlencode "username=$USER_A" --data-urlencode "password=$TEST_PASSWORD" \
  --data-urlencode "user_id=$A"
curl -fsS http://127.0.0.1:18080/wrk2-api/user/register \
  --data-urlencode first_name=Sanity --data-urlencode last_name=Reader \
  --data-urlencode "username=$USER_B" --data-urlencode "password=$TEST_PASSWORD" \
  --data-urlencode "user_id=$B"
curl -fsS http://127.0.0.1:18080/wrk2-api/user/follow \
  --data-urlencode "user_id=$B" --data-urlencode "followee_id=$A"
curl -fsS http://127.0.0.1:18080/wrk2-api/post/compose \
  --data-urlencode "user_id=$A" --data-urlencode "username=$USER_A" \
  --data-urlencode "text=$MARKER" --data-urlencode post_type=0
~~~

**READ-ONLY:** find the new post and verify B's Redis timeline entry.

~~~bash
POST_ID="$(mongo_read "const c=db.getSiblingDB('post').post; if (c.countDocuments({text:'$MARKER'}) !== 1) throw Error('Expected one post'); const p=c.findOne({text:'$MARKER'}); if (p.creator.user_id.toString() !== '$A') throw Error('Wrong author'); print(p.post_id.toString());")"
test "$(redis_read ZCARD "$B")" = 1
test "$(redis_read ZREVRANGE "$B" 0 0)" = "$POST_ID"
printf '%s\n' "$POST_ID" > "$RUN_DIR/post.id"
~~~

11. **READ-ONLY:** check that the new post is absent from Memcached before its first timeline read.

~~~bash
python3 utils/sanity/check_cache_miss.py "$POST_ID"
~~~

12. **CLUSTER CHANGE (cache data):** read B's timeline twice; the first read populates Memcached. Trace-ID generation and comparison of saved responses are local operations. Sampled trace headers observe these requests using the existing [incoming-context support](https://github.com/opentracing-contrib/nginx-opentracing/blob/master/doc/Reference.md#opentracing_trust_incoming_span).

~~~bash
TRACE1="$(python3 utils/sanity/new_trace_id.py)"
TRACE2="$(python3 utils/sanity/new_trace_id.py)"
printf '%s\n' "$TRACE1" > "$RUN_DIR/trace1.id"
printf '%s\n' "$TRACE2" > "$RUN_DIR/trace2.id"
curl -fsS -H "uber-trace-id:$TRACE1:123456789abcdef0:0:1" \
  "http://127.0.0.1:18080/wrk2-api/home-timeline/read?user_id=$B&start=0&stop=10" \
  > "$RUN_DIR/first-read.json"
jq -e --arg marker "$MARKER" --arg id "$POST_ID" --arg author "$A" \
  'length == 1 and .[0].text == $marker and .[0].post_id == $id and .[0].creator.user_id == $author' \
  "$RUN_DIR/first-read.json"
curl -fsS -H "uber-trace-id:$TRACE2:123456789abcdef0:0:1" \
  "http://127.0.0.1:18080/wrk2-api/home-timeline/read?user_id=$B&start=0&stop=10" \
  > "$RUN_DIR/second-read.json"
python3 utils/sanity/compare_reads.py "$RUN_DIR"
~~~

13. **READ-ONLY:** verify the request traces through the Kubernetes API. If retrieval fails, keep the saved IDs and retry retrieval only; do not repeat application reads or clear the cache. After recovery, set TRACE_DIR to the directory containing the successful reads and saved IDs; keep RUN_DIR pointing to the original baseline.

~~~bash
TRACE_DIR="${TRACE_DIR:-$RUN_DIR}"
fetch_trace() {
  for attempt in $(seq 1 30); do
    if k get --request-timeout=10s --raw "$JAEGER_API/$1" > "$2" &&
       jq -e '[.data[]?.spans[]?.operationName] |
              index("read_home_timeline_server") != null and
              index("post_storage_mmc_mget_client") != null and
              index("post_storage_read_posts_server") != null' "$2" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "Trace retrieval/verification failed; preserve the request and inspect the error above." >&2
  return 1
}
fetch_trace "$(cat "$TRACE_DIR/trace1.id")" "$TRACE_DIR/first-trace.json"
fetch_trace "$(cat "$TRACE_DIR/trace2.id")" "$TRACE_DIR/second-trace.json"
jq -e '[.data[].spans[].operationName] |
       index("read_home_timeline_redis_find_client") != null and
       index("mongo_find_client") != null and index("mmc_set_client") != null' \
  "$TRACE_DIR/first-trace.json"
jq -e '[.data[].spans[].operationName] |
       index("read_home_timeline_redis_find_client") != null and
       index("mongo_find_client") == null and index("mmc_set_client") == null' \
  "$TRACE_DIR/second-trace.json"
~~~

14. **READ-ONLY:** final checks. Expect the original 962 Redis keys plus B's new timeline key, and unchanged topology.

~~~bash
redis_keys > "$RUN_DIR/redis-keys.after.txt"
comm -23 "$RUN_DIR/redis-keys.before.txt" "$RUN_DIR/redis-keys.after.txt" \
  > "$RUN_DIR/missing-original-keys.txt"
test ! -s "$RUN_DIR/missing-original-keys.txt"
test "$(wc -l < "$RUN_DIR/redis-keys.after.txt")" -eq 963
redis_read CLUSTER INFO
k -n foxtrot get rediscluster redis-cluster -o json |
  jq -e '.spec.autoScaleEnabled == false and .spec.masters == 4'
k -n "$MONGO_NS" get deployment "$MONGO_RELEASE"-mongos -o json |
  jq -e '.spec.replicas == 3 and .status.readyReplicas == 3'
k -n "$MONGO_NS" get statefulset "$MONGO_RELEASE"-configsvr -o json |
  jq -e '.spec.replicas == 3 and .status.readyReplicas == 3'
k -n "$MONGO_NS" get statefulset "$MONGO_RELEASE"-shard0-data -o json |
  jq -e '.spec.replicas == 1 and .status.readyReplicas == 1'
mongo_read 'const s=db.adminCommand({listShards:1}); if (s.ok !== 1 || s.shards.length !== 1) throw Error("Expected one shard"); print("One shard verified");'
k -n "$SN_NS" get deployments -o json |
  jq -e 'all(.items[]; .spec.replicas == 1 and .status.readyReplicas == 1)'
k -n "$SN_NS" get hpa -o json | jq -e '.items | length == 0'
k -n "$MONGO_NS" get mongosautoscalers
printf 'Evidence retained at %s\n' "$RUN_DIR"
~~~

15. **LOCAL:** stop only the local port forwards. Leave cluster resources and test data in place.

~~~bash
for pid in $PF_PIDS; do
  args="$(ps -p "$pid" -o args= || true)"
  prefix="kubectl --kubeconfig $KCFG --context $CTX -n $SN_NS port-forward service/"
  case "$args" in
    "${prefix}nginx-thrift 18080:8080"|"${prefix}post-storage-memcached 11212:11211"|"${prefix}jaeger 16686:16686") kill "$pid" ;;
  esac
done
~~~

16. **Optional teardown after a successful trial.** Run in the same shell, before adding any other data or starting another experiment. Skip this section to retain the deployment. Keep `$RUN_DIR` as evidence; namespaces, shared FoxTrot resources, operators, and monitoring are retained. For a partial/failed run, inspect what was created before choosing cleanup commands.

**READ-ONLY:** confirm B's key was absent before this run and contains only our post. Identify the four fresh MongoDB PVCs and their backing volumes; require Delete reclaim policy and volumes created no earlier than the trial PVCs.

~~~bash
: "${B:?Use the original trial shell}" "${POST_ID:?}" "${RUN_DIR:?}"
test "${TRIAL_PVCS_FRESH:-false}" = true
test -s "$RUN_DIR/redis-keys.before.txt"
test "$(grep -Fxc -- "$B" "$RUN_DIR/redis-keys.before.txt")" = 0
test "$(redis_read ZCARD "$B")" = 1
test "$(redis_read ZREVRANGE "$B" 0 0)" = "$POST_ID"
TRIAL_PVCS=(
  "datadir-$MONGO_RELEASE-configsvr-0"
  "datadir-$MONGO_RELEASE-configsvr-1"
  "datadir-$MONGO_RELEASE-configsvr-2"
  "datadir-$MONGO_RELEASE-shard0-data-0"
)
k -n "$MONGO_NS" get pvc "${TRIAL_PVCS[@]}" -o wide
TRIAL_PVS="$(k -n "$MONGO_NS" get pvc "${TRIAL_PVCS[@]}" -o jsonpath='{.items[*].spec.volumeName}')"
test -n "$TRIAL_PVS"
TRIAL_PVC_CREATED="$(k -n "$MONGO_NS" get pvc "${TRIAL_PVCS[@]}" -o json | jq -r '[.items[].metadata.creationTimestamp] | min')"
k get pv $TRIAL_PVS -o json |
  jq -e --arg ns "$MONGO_NS" --arg created "$TRIAL_PVC_CREATED" \
  '(.items | length == 4) and all(.items[];
    .spec.persistentVolumeReclaimPolicy == "Delete" and
    .spec.claimRef.namespace == $ns and .metadata.creationTimestamp >= $created)'
~~~

**CLUSTER CHANGE:** remove the trial policy and the three trial releases, including the node exporter. Delete only the four MongoDB PVCs and B's exact post entry in FoxTrot. This destroys the trial's database/cache data; removing the last sorted-set member also removes B's key.

~~~bash
k -n "$MONGO_NS" delete mongodautoscaler socialnetwork-sanity --wait=true
h uninstall sn-sanity-node-exporter --namespace "$MONGO_NS" --wait --timeout 5m
h uninstall sn-sanity --namespace "$SN_NS" --wait --timeout 5m
h uninstall "$MONGO_RELEASE" --namespace "$MONGO_NS" --wait --timeout 5m
k -n "$MONGO_NS" delete pvc "${TRIAL_PVCS[@]}" --wait=true --timeout=5m
test "$(k -n foxtrot exec redis-cluster-0 -c redis -- \
  redis-cli -c --raw ZREM "$B" "$POST_ID")" = 1
~~~

**READ-ONLY:** verify the original Redis key list is restored and the trial volumes are reclaimed. The Helm listings should be empty. This compares key names, not a backup of every original key's contents.

~~~bash
redis_keys > "$RUN_DIR/redis-keys.cleanup.txt"
cmp "$RUN_DIR/redis-keys.before.txt" "$RUN_DIR/redis-keys.cleanup.txt"
k -n foxtrot get rediscluster redis-cluster -o json |
  jq -e '.spec.autoScaleEnabled == false and .spec.masters == 4'
k wait --for=delete pv $TRIAL_PVS --timeout=5m
h list --all --namespace "$SN_NS" --filter '^sn-sanity$'
h list --all --namespace "$MONGO_NS" --filter "^($MONGO_RELEASE|sn-sanity-node-exporter)$"
k -n "$MONGO_NS" get mongodautoscaler socialnetwork-sanity --ignore-not-found
~~~
