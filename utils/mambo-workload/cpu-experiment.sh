# Source after shell-helpers.sh. Sourcing only defines functions.
# Inspection functions read the cluster and save local evidence.
# cpu_trial sends application traffic, updating caches/traces.

mongo_local_read() {
  k -n mambo-socialnetwork exec "sn-mongodb-sharded-shard${1}-data-0" \
    -c mongodb -- bash -ec '
    exec /opt/bitnami/mongodb/bin/mongosh \
      --host 127.0.0.1 --port 27017 --username root \
      --password "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet --eval "$1"
  ' -- "$2"
}

cpu_counters() {
  k -n mambo-socialnetwork exec "sn-mongodb-sharded-shard${1}-data-0" \
    -c mongodb -- sh -ec '
      for file in /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/cpu/cpu.stat /sys/fs/cgroup/cpu,cpuacct/cpu.stat; do
        if test -r "$file"; then cat "$file"; exit 0; fi
      done
      echo "CPU cgroup counters unavailable" >&2
      exit 1
    '
}

# Returns nonzero for incomplete scaling, missing metrics, or failed reads.
# A failed check must never be interpreted as completion.
cpu_ready() {
  local expected="$1" evidence="$2" index
  mkdir -p "$evidence" || return 1
  k -n mambo-socialnetwork get mongodautoscaler socialnetwork-sanity -o json \
    > "$evidence/policy.json" || return 1
  k -n mambo-socialnetwork get pods -o json > "$evidence/pods.json" || return 1
  mongo_read '
    const shards=db.adminCommand({listShards:1});
    const balance=db.adminCommand({balancerCollectionStatus:"post.post"});
    if(shards.ok!==1 || balance.ok!==1) throw Error("Metadata query failed");
    const distribution=db.getSiblingDB("admin").aggregate([
      {$shardedDataDistribution:{}}, {$match:{ns:"post.post"}}
    ], {maxTimeMS:10000}).toArray();
    print(JSON.stringify({shards:shards.shards,balance,distribution}));
  ' > "$evidence/mongo.json" || return 1
  for ((index=0; index<expected; index++)); do
    mongo_local_read "$index" '
      const n=db.getSiblingDB("config").rangeDeletions.countDocuments({}, {maxTimeMS:5000});
      print(JSON.stringify({pendingCleanup:n}));
    ' > "$evidence/cleanup-$index.json" || return 1
  done
  observe > "$evidence/metrics.json" || return 1
  python3 utils/mambo-workload/cpu-results.py ready "$evidence" "$expected"
}

# Capture both shards when present; preserve pod identities to detect resets.
cpu_boundary() {
  local destination="$1" pod index
  mkdir -p "$destination"
  k -n mambo-socialnetwork get pods -o json > "$destination/pods.json"
  while read -r pod; do
    index="${pod#sn-mongodb-sharded-shard}"
    index="${index%-data-0}"
    cpu_counters "$index" > "$destination/cgroup-$index.txt"
  done < <(jq -r '.items[] | select(any(.status.containerStatuses[]?; .name=="mongodb" and .state.running!=null)) | .metadata.name | select(test("^sn-mongodb-sharded-shard[0-9]+-data-0$"))' "$destination/pods.json")
}

cpu_trial() {
  local before
  before="$(mktemp -d "$CPU_DIR/boundary.XXXXXX")"
  cpu_boundary "$before"
  measure "$1" "$2"
  mv "$before" "$TRIAL/cpu.before"
  cpu_boundary "$TRIAL/cpu.after"
  k -n mongodboperator-system logs deployment/mongodboperator-controller-manager \
    -c manager --since-time="$(cat "$TRIAL/start.utc")" > "$TRIAL/mambo.log"
  python3 utils/mambo-workload/cpu-results.py summary "$TRIAL"
}
