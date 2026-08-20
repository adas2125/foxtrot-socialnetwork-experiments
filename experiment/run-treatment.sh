#!/usr/bin/env bash

set -euo pipefail

: "${PHASE6_RETRY_DIR:?}"
: "${PHASE6_INITIAL_LAST_SCALE:?}"
: "${PHASE5_DIR:?}"
: "${FORMAL_WRK:?}"
: "${DSB_DIR:?}"
: "${NGINX_CLUSTER_IP:?}"
: "${FOXTROT_CPU_QUERY:?}"

TOPOLOGY_PID=""
P99_PID=""

TOPOLOGY_FILE="$PHASE6_RETRY_DIR/autoscaling-topology-timeseries.tsv"
P99_FILE="$PHASE6_RETRY_DIR/autoscaling-r2550-p99-timeseries.tsv"
P99_LOG="$PHASE6_RETRY_DIR/autoscaling-p99-watcher.log"
TMP_DIR="$(mktemp -d /tmp/p6-retry.XXXXXX)"
RAW_P99="$TMP_DIR/url0thread0.txt"

cleanup() {
  set +e

  if test -s "$TOPOLOGY_FILE" &&
   rg -q $'\tfreeze_completed_scale_at_four$' "$TOPOLOGY_FILE"
  then
    kubectl patch rediscluster redis-cluster \
      -n foxtrot \
      --type=merge \
      -p '{"spec":{"autoScaleEnabled":false}}' \
      >> "$PHASE6_RETRY_DIR/cleanup-freeze.log" 2>&1
  else
    printf '%s\n' \
      'Autoscaling was not disabled: complete convergence was not confirmed.' \
      >> "$PHASE6_RETRY_DIR/cleanup-freeze.log"
  fi

  if test -n "$P99_PID" &&
     kill -0 "$P99_PID" 2>/dev/null
  then
    kill -TERM "$P99_PID"
    wait "$P99_PID" 2>/dev/null || true
  fi

  if test -n "$TOPOLOGY_PID" &&
     kill -0 "$TOPOLOGY_PID" 2>/dev/null
  then
    kill -TERM "$TOPOLOGY_PID"
    wait "$TOPOLOGY_PID" 2>/dev/null || true
  fi
}

trap cleanup EXIT
trap 'exit 130' INT TERM

printf 'temporary_directory=%s\n' "$TMP_DIR" |
tee "$PHASE6_RETRY_DIR/temporary-directory.txt"

CR_STATE="$(
  kubectl get rediscluster redis-cluster \
    -n foxtrot \
    -o jsonpath='{.spec.masters}{"\t"}{.spec.autoScaleEnabled}{"\t"}{.spec.cpuThreshold}{"\t"}{.spec.cpuThresholdLow}{"\t"}{.spec.memoryThresholdLow}'
)"

IFS=$'\t' read -r \
  PREFLIGHT_MASTERS \
  PREFLIGHT_AUTOSCALING \
  PREFLIGHT_CPU_HIGH \
  PREFLIGHT_CPU_LOW \
  PREFLIGHT_MEMORY_LOW \
  <<< "$CR_STATE"

CLUSTER_INFO="$(
  kubectl exec -n foxtrot redis-cluster-0 -c redis \
    -- redis-cli --raw CLUSTER INFO |
  tr -d '\r'
)"

KNOWN_NODES="$(
  printf '%s\n' "$CLUSTER_INFO" |
  sed -n 's/^cluster_known_nodes://p'
)"

CLUSTER_SIZE="$(
  printf '%s\n' "$CLUSTER_INFO" |
  sed -n 's/^cluster_size://p'
)"

SLOTS_OK="$(
  printf '%s\n' "$CLUSTER_INFO" |
  sed -n 's/^cluster_slots_ok://p'
)"

if test "$PREFLIGHT_MASTERS" != "3" ||
   test "$PREFLIGHT_AUTOSCALING" != "false" ||
   test "$PREFLIGHT_CPU_HIGH" != "10" ||
   test "$PREFLIGHT_CPU_LOW" != "1" ||
   test "$PREFLIGHT_MEMORY_LOW" != "1" ||
   test "$KNOWN_NODES" != "8" ||
   test "$CLUSTER_SIZE" != "3" ||
   test "$SLOTS_OK" != "16384"
then
  echo "STOP: treatment preflight failed"
  printf '%s\n' "$CR_STATE"
  printf '%s\n' "$CLUSTER_INFO"
  exit 2
fi

echo "PASS: treatment preflight valid" |
tee "$PHASE6_RETRY_DIR/treatment-preflight.txt"

"$PHASE6_RETRY_DIR/monitor-and-freeze.sh" \
  "$TOPOLOGY_FILE" \
  "$PHASE6_INITIAL_LAST_SCALE" \
  > "$PHASE6_RETRY_DIR/topology-monitor.log" 2>&1 &

TOPOLOGY_PID=$!

sleep 2

if ! kill -0 "$TOPOLOGY_PID" 2>/dev/null
then
  echo "STOP: topology monitor failed to start"
  exit 3
fi

cd "$TMP_DIR"

WARMUP_START="$(date +%s)"

printf 'warmup_start_epoch=%s\n' "$WARMUP_START" |
tee "$PHASE6_RETRY_DIR/warmup-time-window.txt"

set +e

"$FORMAL_WRK" \
  -D exp \
  -t4 \
  -c328 \
  -d90s \
  -L \
  -r \
  -R2550 \
  -s "$DSB_DIR/socialNetwork/wrk2/scripts/social-network/read-home-timeline.lua" \
  "http://${NGINX_CLUSTER_IP}:8080/wrk2-api/home-timeline/read" \
  2>&1 |
tee "$PHASE6_RETRY_DIR/warmup-r2550-c328-90s.txt"

WARMUP_EXIT=${PIPESTATUS[0]}

set -e

WARMUP_END="$(date +%s)"

printf 'warmup_end_epoch=%s\nwarmup_exit_code=%s\n' \
  "$WARMUP_END" \
  "$WARMUP_EXIT" |
tee -a "$PHASE6_RETRY_DIR/warmup-time-window.txt"

if test "$WARMUP_EXIT" -ne 0
then
  echo "STOP: warm-up workload failed"
  exit 4
fi

curl -sSG \
  http://127.0.0.1:19092/api/v1/query \
  --data-urlencode "query=$FOXTROT_CPU_QUERY" |
jq -r '
  .data.result[] |
  select(.metric.pod | test("^redis-cluster-[0-2]$")) |
  [.metric.pod, (.value[1] | tonumber)] |
  @tsv
' |
sort -V \
  > "$PHASE6_RETRY_DIR/cpu-after-warmup.tsv"

cat "$PHASE6_RETRY_DIR/cpu-after-warmup.tsv"

ACTIVE_SAMPLES="$(
  awk 'END {print NR + 0}' \
    "$PHASE6_RETRY_DIR/cpu-after-warmup.tsv"
)"

ABOVE_HIGH="$(
  awk '$2 > 10 {count++} END {print count + 0}' \
    "$PHASE6_RETRY_DIR/cpu-after-warmup.tsv"
)"

BELOW_LOW="$(
  awk '$2 < 1 {count++} END {print count + 0}' \
    "$PHASE6_RETRY_DIR/cpu-after-warmup.tsv"
)"

printf 'active_samples=%s above_10=%s below_1=%s\n' \
  "$ACTIVE_SAMPLES" \
  "$ABOVE_HIGH" \
  "$BELOW_LOW" |
tee "$PHASE6_RETRY_DIR/warmup-cpu-gate.txt"

if test "$ACTIVE_SAMPLES" -ne 3 ||
   test "$ABOVE_HIGH" -lt 1 ||
   test "$BELOW_LOW" -ne 0
then
  echo "STOP: CPU gate failed; autoscaling was not enabled"
  exit 5
fi

echo "PASS: CPU gate satisfied; enabling autoscaling"

python3 -u \
  "$PHASE5_DIR/timestamp-p99.py" \
  "$RAW_P99" \
  "$P99_FILE" \
  > "$P99_LOG" 2>&1 &

P99_PID=$!

sleep 1

if ! kill -0 "$P99_PID" 2>/dev/null
then
  echo "STOP: p99 watcher failed to start"
  exit 6
fi

START_EPOCH="$(date +%s)"
START_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

printf 'start_epoch=%s\nstart_iso=%s\n' \
  "$START_EPOCH" \
  "$START_ISO" |
tee "$PHASE6_RETRY_DIR/autoscaling-r2550-time-window.txt"

kubectl patch rediscluster redis-cluster \
  -n foxtrot \
  --type=merge \
  -p '{"spec":{"autoScaleEnabled":true}}' |
tee "$PHASE6_RETRY_DIR/enable-autoscaling.txt"

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
tee "$PHASE6_RETRY_DIR/autoscaling-r2550-c328-600s.txt"

WRK_EXIT=${PIPESTATUS[0]}

set -e

END_EPOCH="$(date +%s)"
END_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

printf 'end_epoch=%s\nend_iso=%s\nwrk_exit_code=%s\n' \
  "$END_EPOCH" \
  "$END_ISO" \
  "$WRK_EXIT" |
tee -a "$PHASE6_RETRY_DIR/autoscaling-r2550-time-window.txt"

sleep 2

until rg -q $'\tfreeze_completed_scale_at_four$' "$TOPOLOGY_FILE"
do
  if ! kill -0 "$TOPOLOGY_PID" 2>/dev/null
  then
    echo "STOP: topology monitor exited before convergence"
    exit 7
  fi

  echo "Waiting for complete four-master and standby convergence..."
  sleep 5
done

if test -s "$RAW_P99"
then
  cp -p \
    "$RAW_P99" \
    "$PHASE6_RETRY_DIR/autoscaling-r2550-raw-worker-p99-us.txt"
else
  echo "WARNING: raw p99 file is missing or empty"
fi

cleanup
trap - EXIT INT TERM

printf 'wrk_exit_code=%s\n' "$WRK_EXIT" |
tee "$PHASE6_RETRY_DIR/RUN-COMPLETE.txt"

if test "$WRK_EXIT" -ne 0
then
  exit "$WRK_EXIT"
fi
