# Source this file from Bash. Loading it only defines functions.
# k/h use KCFG and CTX supplied by the caller.
# mongo_read executes the supplied JavaScript: keep it read-only for inspection.
# run_load/measure send application traffic and can change caches.
# No seeding helper is included.

k() { kubectl --kubeconfig "$KCFG" --context "$CTX" --request-timeout=30s "$@"; }
h() { helm --kubeconfig "$KCFG" --kube-context "$CTX" "$@"; }

mongo_read() {
  k -n mambo-socialnetwork exec deployment/sn-mongodb-sharded-mongos -- bash -ec '
    exec /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
      --username root --password "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet --eval "$1"
  ' -- "$1"
}

run_load() {
  k --request-timeout="$(( $1 + 120 ))s" -n social-network exec sn-wrk2 -c wrk2 -- \
    env READER_BASE="$BASE" READERS="$READERS" POSTS="$POSTS" PAGE_SIZE=10 SEED="${LOAD_SEED:-42}" \
    /work/DeathStarBench/wrk2/wrk -D exp -t2 -c32 -d"${1}s" -R"$2" -L \
    -s /work/mambo-workload/read-home-timeline.lua \
    http://nginx-thrift.social-network.svc.cluster.local:8080
}

observe() {
  k --request-timeout=1m -n social-network exec sn-wrk2 -c wrk2 -- \
    python3 -B /work/mambo-workload/observe.py
}

measure() {
  local label="$1" seconds="$2" load_pid sample=0
  TRIAL="$(mktemp -d "$DATA_DIR/${label}-r${RPS}.XXXXXX")"
  cp "$DATA_DIR/dataset.env" "$TRIAL/"
  printf 'RPS=%s\nTHREADS=2\nCONNECTIONS=32\nDURATION_SECONDS=%s\n' \
    "$RPS" "$seconds" > "$TRIAL/run.env"
  printf 'LOAD_SEED=%s\n' "${LOAD_SEED:-42}" >> "$TRIAL/run.env"
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
