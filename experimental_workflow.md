# Minimal clean experiment flow

## 1. Define paths

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

# Defining the paths
export KUBECONFIG="$HOME/.kube/cloudlab-k3s.yaml"
export STATEFUL_ROOT="$HOME/stateful-scaling"
export DSB_DIR="$STATEFUL_ROOT/DeathStarBench"
export WRK2_DIR="$DSB_DIR/wrk2"
export FORMAL_WRK="$WRK2_DIR/wrk"

# TODO: The path names below should be adjusted to locations of helper scripts.
export PREVIOUS_ROOT="$STATEFUL_ROOT/results/socialnetwork-day8-formal-20260815-091919"
export TREATMENT_SOURCE="$PREVIOUS_ROOT/phase6-autoscaling-rep1-retry1-20260816-005834"
export P99_SOURCE="$PREVIOUS_ROOT/phase5-fixed3-rep1-20260816-000911/timestamp-p99.py"
export ANALYSIS_TEMPLATE="$PREVIOUS_ROOT/simplified-analysis-20260817T033847Z"

# Setting up all the directories for this experiment
export EXPERIMENT_ROOT="$STATEFUL_ROOT/results/socialnetwork-formal-$(date -u +%Y%m%dT%H%M%SZ)"
export FIXED3_DIR="$EXPERIMENT_ROOT/fixed3"
export AUTOSCALE_DIR="$EXPERIMENT_ROOT/autoscale"
export FIXED4_DIR="$EXPERIMENT_ROOT/fixed4"
export ANALYSIS_DIR="$EXPERIMENT_ROOT/simplified-analysis"
export ANALYSIS_INPUTS="$ANALYSIS_DIR/inputs"

mkdir -p \
  "$FIXED3_DIR" \
  "$AUTOSCALE_DIR/post-treatment-diagnostics" \
  "$FIXED4_DIR" \
  "$ANALYSIS_INPUTS"

# TODO: This can be simplified; Copying over the scripts to their new locations
cp -p "$TREATMENT_SOURCE/run-treatment.sh" "$AUTOSCALE_DIR/"
cp -p "$TREATMENT_SOURCE/monitor-and-freeze.sh" "$AUTOSCALE_DIR/"
cp -p "$P99_SOURCE" "$AUTOSCALE_DIR/timestamp-p99.py"
cp -p "$ANALYSIS_TEMPLATE/analysis.py" "$ANALYSIS_DIR/"
cp -p "$ANALYSIS_TEMPLATE/requirements.txt" "$ANALYSIS_DIR/"

# Retrieves the Kubernetes-internal IP address of the nginx-thrift Service
export NGINX_CLUSTER_IP="$(
  kubectl get service nginx-thrift \
    -n social-network \
    -o jsonpath='{.spec.clusterIP}'
)"
test -n "$NGINX_CLUSTER_IP"

```

## 2. Enable SocialNetwork Replica Autoscaling 

```bash
# Confirm the HPAs actually exist first
for HPA in \
  nginx-thrift-capacity \
  post-storage-capacity \
  home-timeline-capacity
do
  kubectl get hpa "$HPA" -n social-network >/dev/null
done
```

## 2. Freeze application capacity (Optional)

```bash
kubectl delete hpa \
  nginx-thrift-capacity \
  post-storage-capacity \
  home-timeline-capacity \
  -n social-network \
  --ignore-not-found

kubectl scale deployment/nginx-thrift \
  -n social-network \
  --replicas=12

kubectl scale deployment/post-storage-service \
  -n social-network \
  --replicas=5

kubectl scale deployment/home-timeline-service \
  -n social-network \
  --replicas=2

kubectl rollout status \
  deployment/nginx-thrift \
  -n social-network \
  --timeout=5m

kubectl rollout status \
  deployment/post-storage-service \
  -n social-network \
  --timeout=5m

kubectl rollout status \
  deployment/home-timeline-service \
  -n social-network \
  --timeout=5m
```

## 3. Start Prometheus access

```bash
# Port Forwarding & Prometheus Readiness Probe
kubectl port-forward \
  -n monitoring \
  service/kps-kube-prometheus-stack-prometheus \
  19092:9090 \
  > "$EXPERIMENT_ROOT/prometheus-port-forward.log" 2>&1 &
export PROMETHEUS_PF_PID=$!
trap 'kill "$PROMETHEUS_PF_PID" 2>/dev/null || true' EXIT
sleep 3
curl -fsS \
  http://127.0.0.1:19092/-/ready
# Define the exact queries for foxtrot CPU, SocialNetwork CPU, Overall Node CPU
export FOXTROT_CPU_QUERY='sum by (pod) (rate(container_cpu_usage_seconds_total{container="redis",pod=~"^redis-cluster-.*",namespace="foxtrot",service="kps-kube-prometheus-stack-kubelet"}[1m])) * 100'

export SERVICE_CPU_QUERY='sum by (pod,container) (rate(container_cpu_usage_seconds_total{namespace="social-network",container=~"nginx-thrift|home-timeline-service|post-storage-service|post-storage-memcached"}[1m])) * 100'

export NODE_CPU_QUERY='100 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100'
```

## 4. Use these three metric-export functions

```bash
# Function receives a start & end unix timestamp & destination filename. We contact 
# Prometheus through port-forward; options documented here: 
# https://prometheus.io/docs/prometheus/latest/querying/api/#range-queries. Prometheus returns # a json and we make it readable through the jq command.

export_redis_cpu() {
  local START="$1"
  local END="$2"
  local OUTPUT="$3"

  curl -fsSG \
    http://127.0.0.1:19092/api/v1/query_range \
    --data-urlencode "query=$FOXTROT_CPU_QUERY" \
    --data-urlencode "start=$START" \
    --data-urlencode "end=$END" \
    --data-urlencode 'step=15s' |
  jq -r '
    .data.result[] |
    .metric.pod as $pod |
    .values[] |
    [$pod, (.[0] | todateiso8601), .[1]] |
    @tsv
  ' > "$OUTPUT"
test -s "$OUTPUT"
}

# Returns only 1 series since the query uses avg() instead of by()
export_node_cpu() {
  local START="$1"
  local END="$2"
  local OUTPUT="$3"

  curl -fsSG \
    http://127.0.0.1:19092/api/v1/query_range \
    --data-urlencode "query=$NODE_CPU_QUERY" \
    --data-urlencode "start=$START" \
    --data-urlencode "end=$END" \
    --data-urlencode 'step=15s' |
  jq -r '
    .data.result[].values[] |
    [(.[0] | todateiso8601), .[1]] |
    @tsv
  ' > "$OUTPUT"
test -s "$OUTPUT"
}

# Outputs pod name, container name, and CPU. More replicas means more pods and container 
# names and service names happen to closely resemble each other in this  deployment.
export_service_cpu() {
  local START="$1"
  local END="$2"
  local OUTPUT="$3"

  curl -fsSG \
    http://127.0.0.1:19092/api/v1/query_range \
    --data-urlencode "query=$SERVICE_CPU_QUERY" \
    --data-urlencode "start=$START" \
    --data-urlencode "end=$END" \
    --data-urlencode 'step=15s' |
  jq -r '
    .data.result[] |
    .metric.pod as $pod |
    .metric.container as $container |
    .values[] |
    [$pod, $container, (.[0] | todateiso8601), .[1]] |
    @tsv
  ' > "$OUTPUT"
test -s "$OUTPUT"
}
```

## 5. Use this topology snapshot function

```bash
# The first argument is the destination filename. Loop runs through each pod and determines its
# role (e.g. master or slave). It also counts the number of keys stored in its database. The next
# step is to extract the owned slots. To do this, it uses CLUSTER NODES. The awk command
# selects the line whose third field contains myself. Fields 9 onward contain owned slots, so it
# prints something like: 5460,10923-13652. VERIFIED!

snapshot_topology() {
  local OUTPUT="$1"
  {
    printf 'pod\trole\tkeys\tslots\n'

    for POD in $(
      kubectl get pods \
        -n foxtrot \
        -o name |
      sed 's#pod/##' |
      rg '^redis-cluster-[0-9]+$' |
      sort -V
    )
    do
      ROLE="$(
        kubectl exec -n foxtrot "$POD" -c redis \
          -- redis-cli --raw INFO replication |
        tr -d '\r' |
        sed -n 's/^role://p'
      )"

      KEYS="$(
        kubectl exec -n foxtrot "$POD" -c redis \
          -- redis-cli --raw DBSIZE
      )"

      SLOTS="$(
        kubectl exec -n foxtrot "$POD" -c redis \
          -- redis-cli --raw CLUSTER NODES |
        awk '
          $3 ~ /myself/ {
            if (NF < 9)
              printf "none"
            else
              for (i = 9; i <= NF; i++)
                printf "%s%s", (i == 9 ? "" : ","), $i
          }
        '
      )"

      printf '%s\t%s\t%s\t%s\n' \
        "$POD" "$ROLE" "$KEYS" "$SLOTS"
    done
  } > "$OUTPUT"
}
```

## 6. Run Fixed 3

```bash
# Establish its policy:
BASELINE_JSON="$(
  kubectl get rediscluster redis-cluster -n foxtrot -o json
)"

printf '%s' "$BASELINE_JSON" |
jq -e '
  .spec.masters == 3 and
  .spec.autoScaleEnabled == false and
  .status.currentMasters == 3 and
  .status.standbyPod == "redis-cluster-6" and
  (.status.isResharding // false) == false and
  (.status.isProvisioningStandby // false) == false
' >/dev/null

CLUSTER_INFO="$(
  kubectl exec -n foxtrot redis-cluster-0 -c redis -- \
    redis-cli --raw CLUSTER INFO |
  tr -d '\r'
)"

for EXPECTED in \
  cluster_state:ok \
  cluster_size:3 \
  cluster_known_nodes:8 \
  cluster_slots_ok:16384 \
  cluster_slots_fail:0
do
  rg -qx "$EXPECTED" <<< "$CLUSTER_INFO"
done

for N in 8 9; do
  test -z "$(
    kubectl get pod "redis-cluster-$N" \
      -n foxtrot --ignore-not-found -o name
  )"

  kubectl delete pvc "data-redis-cluster-$N" \
    -n foxtrot --ignore-not-found --wait=true
done

kubectl patch rediscluster redis-cluster \
  -n foxtrot \
  --type=merge \
  -p '{
    "spec": {
      "cpuThreshold": 10,
      "cpuThresholdLow": 1,
      "memoryThresholdLow": 1
    }
  }'

snapshot_topology \
  "$FIXED3_DIR/foxtrot-roles-keys-slots-before.txt"

# Calculates ACTIVE_MASTERS (slot-bearing masters) and LOGICAL_KEYS (sum of keys 
# on those masters)
read -r ACTIVE_MASTERS LOGICAL_KEYS < <(
  awk -F '\t' '
    NR > 1 && $2 == "master" && $4 != "none" {
      masters++
      keys += $3
    }
    END { print masters + 0, keys + 0 }
  ' "$FIXED3_DIR/foxtrot-roles-keys-slots-before.txt"
)

# Validation checks
test "$ACTIVE_MASTERS" -eq 3
test "$LOGICAL_KEYS" -eq 962
# Run the workload
export LUA_PATH='/usr/share/lua/5.1/?.lua;/usr/share/lua/5.1/?/init.lua;;'
export LUA_CPATH='/usr/lib/x86_64-linux-gnu/lua/5.1/?.so;;'

test -f /usr/share/lua/5.1/socket.lua
test -f /usr/lib/x86_64-linux-gnu/lua/5.1/socket/core.so

cd "$FIXED3_DIR"

export FIXED3_START_EPOCH=$(date +%s)
export FIXED3_START_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

printf 'start_epoch=%s\nstart_iso=%s\n' \
  "$FIXED3_START_EPOCH" \
  "$FIXED3_START_ISO" \
> "$FIXED3_DIR/fixed3-r2550-time-window.txt"

set +e
"$FORMAL_WRK" \
  -D exp \
  -t4 \
  -c328 \
  -d600s \
  -L \
  -r \
  -p \
  -R2550 \
  -s "$DSB_DIR/socialNetwork/wrk2/scripts/social-network/read-home-timeline.lua" \
  "http://${NGINX_CLUSTER_IP}:8080/wrk2-api/home-timeline/read" \
  2>&1 |
tee "$FIXED3_DIR/fixed3-r2550-c328-600s.txt"

export FIXED3_WRK_EXIT_CODE="${PIPESTATUS[0]}"
set -e

export FIXED3_END_EPOCH=$(date +%s)
export FIXED3_END_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

printf 'end_epoch=%s\nend_iso=%s\nwrk_exit_code=%s\n' \
  "$FIXED3_END_EPOCH" \
  "$FIXED3_END_ISO" \
  "$FIXED3_WRK_EXIT_CODE" \
>> "$FIXED3_DIR/fixed3-r2550-time-window.txt"

test "$FIXED3_WRK_EXIT_CODE" -eq 0
# Export metrics immediately
export_redis_cpu \
  "$FIXED3_START_EPOCH" \
  "$FIXED3_END_EPOCH" \
  "$FIXED3_DIR/fixed3-r2550-foxtrot-cpu.tsv"

export_node_cpu \
  "$FIXED3_START_EPOCH" \
  "$FIXED3_END_EPOCH" \
  "$FIXED3_DIR/fixed3-r2550-node-cpu.tsv"

export_service_cpu \
  "$FIXED3_START_EPOCH" \
  "$FIXED3_END_EPOCH" \
  "$FIXED3_DIR/fixed3-r2550-service-cpu.tsv"
```

## 7. Run Autoscaling 3→4

```bash
export PHASE6_RETRY_DIR="$AUTOSCALE_DIR"
export PHASE5_DIR="$AUTOSCALE_DIR"

export PHASE6_INITIAL_LAST_SCALE="$(
  kubectl get rediscluster redis-cluster \
    -n foxtrot \
    -o jsonpath='{.status.lastScaleTime}'
)"
test -n "$PHASE6_INITIAL_LAST_SCALE"

cd "$AUTOSCALE_DIR"

# Starting the autoscaling workload
if ! timeout --signal=INT --kill-after=30s 15m \
  bash "$AUTOSCALE_DIR/run-treatment.sh"
then
  kubectl patch rediscluster redis-cluster \
    -n foxtrot --type=merge \
    -p '{"spec":{"autoScaleEnabled":false}}'
  echo "STOP: autoscaling did not converge within 15 minutes"
  exit 1
fi
# That script must produce:
# autoscaling-r2550-c328-600s.txt
# autoscaling-r2550-time-window.txt
# autoscaling-r2550-p99-timeseries.tsv
# autoscaling-topology-timeseries.tsv
# Read its recorded window:
export AUTO_START_EPOCH="$(
  sed -n 's/^start_epoch=//p' \
    "$AUTOSCALE_DIR/autoscaling-r2550-time-window.txt"
)"

export AUTO_END_EPOCH="$(
  sed -n 's/^end_epoch=//p' \
    "$AUTOSCALE_DIR/autoscaling-r2550-time-window.txt"
)"

export AUTO_START_ISO="$(
  sed -n 's/^start_iso=//p' \
    "$AUTOSCALE_DIR/autoscaling-r2550-time-window.txt"
)"
# Export Redis and node CPU
export_redis_cpu \
  "$AUTO_START_EPOCH" \
  "$AUTO_END_EPOCH" \
  "$AUTOSCALE_DIR/autoscaling-r2550-foxtrot-cpu.tsv"

export_node_cpu \
  "$AUTO_START_EPOCH" \
  "$AUTO_END_EPOCH" \
  "$AUTOSCALE_DIR/autoscaling-r2550-node-cpu.tsv"
# Capture operator events
kubectl logs \
  -n redis-operator-system \
  deployment/redis-operator-controller-manager \
  --since-time="$AUTO_START_ISO" \
> "$AUTOSCALE_DIR/post-treatment-diagnostics/operator.log"

# Retains every line containing at least one of scale, reshard, standby, master
rg -i \
  'scale|reshard|standby|master' \
  "$AUTOSCALE_DIR/post-treatment-diagnostics/operator.log" \
> "$AUTOSCALE_DIR/post-treatment-diagnostics/operator-scaling-lines.txt"

EVENTS="$AUTOSCALE_DIR/post-treatment-diagnostics/operator-scaling-lines.txt"

# Searches for the three literal messages
for PHRASE in \
  "Triggering scale-up using standby pod" \
  "Creating reshard job to activate standby" \
  "Reshard job succeeded"
do
  rg -Fq "$PHRASE" "$EVENTS"
done
# Capture the resulting four-master topology
snapshot_topology \
  "$AUTOSCALE_DIR/post-treatment-diagnostics/all-pod-topology.tsv"
# Verify
kubectl get rediscluster redis-cluster \
  -n foxtrot \
  -o jsonpath='masters={.spec.masters} autoscaling={.spec.autoScaleEnabled}{"\n"}'

kubectl exec \
  -n foxtrot \
  redis-cluster-0 \
  -c redis \
  -- redis-cli CLUSTER INFO
# Required state:
# masters=4
# autoscaling=false
# cluster_state=ok
# cluster_size=4
# cluster_slots_ok=16384
```

## 8. Run Fixed 4

After the autoscaling treatment has completed and been frozen:

```bash
cd "$FIXED4_DIR"

snapshot_topology \
  "$FIXED4_DIR/foxtrot-roles-keys-slots-before.txt"
export FIXED4_START_EPOCH=$(date +%s)
export FIXED4_START_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

printf 'start_epoch=%s\nstart_iso=%s\n' \
  "$FIXED4_START_EPOCH" \
  "$FIXED4_START_ISO" \
> "$FIXED4_DIR/fixed4-r2550-time-window.txt"


set +e
"$FORMAL_WRK" \
  -D exp \
  -t4 \
  -c328 \
  -d600s \
  -L \
  -r \
  -p \
  -R2550 \
  -s "$DSB_DIR/socialNetwork/wrk2/scripts/social-network/read-home-timeline.lua" \
  "http://${NGINX_CLUSTER_IP}:8080/wrk2-api/home-timeline/read" \
  2>&1 |
tee "$FIXED4_DIR/fixed4-r2550-c328-600s.txt"

export FIXED4_WRK_EXIT_CODE="${PIPESTATUS[0]}"
set -e

export FIXED4_END_EPOCH=$(date +%s)
export FIXED4_END_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

printf 'end_epoch=%s\nend_iso=%s\nwrk_exit_code=%s\n' \
  "$FIXED4_END_EPOCH" \
  "$FIXED4_END_ISO" \
  "$FIXED4_WRK_EXIT_CODE" \
>> "$FIXED4_DIR/fixed4-r2550-time-window.txt"
test "$FIXED4_WRK_EXIT_CODE" -eq 0
# Export metrics
export_redis_cpu \
  "$FIXED4_START_EPOCH" \
  "$FIXED4_END_EPOCH" \
  "$FIXED4_DIR/fixed4-r2550-foxtrot-cpu.tsv"

export_node_cpu \
  "$FIXED4_START_EPOCH" \
  "$FIXED4_END_EPOCH" \
  "$FIXED4_DIR/fixed4-r2550-node-cpu.tsv"

export_service_cpu \
  "$FIXED4_START_EPOCH" \
  "$FIXED4_END_EPOCH" \
  "$FIXED4_DIR/fixed4-r2550-service-cpu.tsv"
```

## 9. Stop the only background service

```bash
kill "$PROMETHEUS_PF_PID" 2>/dev/null || true
wait "$PROMETHEUS_PF_PID" 2>/dev/null || true
trap - EXIT

cp -p "$FIXED3_DIR/fixed3-r2550-c328-600s.txt" \
  "$ANALYSIS_INPUTS/fixed3-wrk.txt"
cp -p "$AUTOSCALE_DIR/autoscaling-r2550-c328-600s.txt" \
  "$ANALYSIS_INPUTS/autoscale-wrk.txt"
cp -p "$FIXED4_DIR/fixed4-r2550-c328-600s.txt" \
  "$ANALYSIS_INPUTS/fixed4-wrk.txt"

cp -p "$FIXED3_DIR/fixed3-r2550-time-window.txt" \
  "$ANALYSIS_INPUTS/fixed3-window.txt"
cp -p "$AUTOSCALE_DIR/autoscaling-r2550-time-window.txt" \
  "$ANALYSIS_INPUTS/autoscale-window.txt"
cp -p "$FIXED4_DIR/fixed4-r2550-time-window.txt" \
  "$ANALYSIS_INPUTS/fixed4-window.txt"

cp -p "$FIXED3_DIR/fixed3-r2550-foxtrot-cpu.tsv" \
  "$ANALYSIS_INPUTS/fixed3-redis-cpu.tsv"
cp -p "$AUTOSCALE_DIR/autoscaling-r2550-foxtrot-cpu.tsv" \
  "$ANALYSIS_INPUTS/autoscale-redis-cpu.tsv"
cp -p "$FIXED4_DIR/fixed4-r2550-foxtrot-cpu.tsv" \
  "$ANALYSIS_INPUTS/fixed4-redis-cpu.tsv"

cp -p "$FIXED3_DIR/fixed3-r2550-node-cpu.tsv" \
  "$ANALYSIS_INPUTS/fixed3-node-cpu.tsv"
cp -p "$AUTOSCALE_DIR/autoscaling-r2550-node-cpu.tsv" \
  "$ANALYSIS_INPUTS/autoscale-node-cpu.tsv"
cp -p "$FIXED4_DIR/fixed4-r2550-node-cpu.tsv" \
  "$ANALYSIS_INPUTS/fixed4-node-cpu.tsv"

cp -p "$FIXED3_DIR/fixed3-r2550-service-cpu.tsv" \
  "$ANALYSIS_INPUTS/fixed3-service-cpu.tsv"
cp -p "$FIXED4_DIR/fixed4-r2550-service-cpu.tsv" \
  "$ANALYSIS_INPUTS/fixed4-service-cpu.tsv"

cp -p "$AUTOSCALE_DIR/autoscaling-r2550-p99-timeseries.tsv" \
  "$ANALYSIS_INPUTS/autoscale-p99.tsv"
cp -p "$AUTOSCALE_DIR/autoscaling-topology-timeseries.tsv" \
  "$ANALYSIS_INPUTS/autoscale-topology.tsv"
cp -p "$AUTOSCALE_DIR/post-treatment-diagnostics/operator-scaling-lines.txt" \
  "$ANALYSIS_INPUTS/autoscale-events.txt"

cp -p "$FIXED3_DIR/foxtrot-roles-keys-slots-before.txt" \
  "$ANALYSIS_INPUTS/fixed3-topology.tsv"
cp -p "$FIXED4_DIR/foxtrot-roles-keys-slots-before.txt" \
  "$ANALYSIS_INPUTS/fixed4-topology.tsv"

test "$(find "$ANALYSIS_INPUTS" -maxdepth 1 -type f -size +0c | wc -l)" -eq 19

python3 -m venv "$ANALYSIS_DIR/.venv"
"$ANALYSIS_DIR/.venv/bin/pip" install \
  -r "$ANALYSIS_DIR/requirements.txt"

MPLBACKEND=Agg \
  "$ANALYSIS_DIR/.venv/bin/python" \
  "$ANALYSIS_DIR/analysis.py"
```
