# New-cluster setup and recovery notes

This is the minimal recovery guide for recreating the FoxTrot SocialNetwork
experiment on a new Kubernetes node. It records the configuration that produced
the normal-latency runs without changing the existing manifests in this
repository.

Do not commit a kubeconfig, Kubernetes Secret, Helm Secret, shell history, or a
raw cluster dump. Set the kubeconfig for the new cluster locally.

## Known-good reference

The last verified environment used:

- Kubernetes/k3s `v1.34.10+k3s1`
- Helm `v3.21.3`
- `kube-prometheus-stack` chart `88.2.0`
- DeathStarBench commit `6ecb09706140f8730b5385c08f1386c654c3c526`
- FoxTrot commit `6e85a07a2fc4434d1e7f3b2cc74a85e641242668`
- Fixed application capacity: nginx `12`, post-storage `5`, home-timeline `2`
- Runtime CPU limits: nginx `4` cores; other SocialNetwork containers inherit
  the experiment-wide `2`-core limit
- SocialNetwork HPAs disabled
- Formal workload: read-home-timeline, `R2550`, `c328`, four threads
- FoxTrot baseline: three slot-bearing masters, one replica per master, standby
  pair `redis-cluster-6`/`redis-cluster-7`, eight known nodes, all 16,384 slots,
  and 962 logical keys

The normal-latency fixed-three references had approximately 27--30 ms p99.
Runs with a 2-core nginx limit experienced CPU throttling and were not
comparable.

## 1. Define local paths

Run the setup from one shell and adjust `KUBECONFIG` for the new cluster:

```bash
set -Eeuo pipefail

: "${KUBECONFIG:?Set KUBECONFIG to the new cluster first}"

export STATEFUL_ROOT="$HOME/stateful-scaling"
export REPRO_ROOT="$HOME/foxtrot-socialnetwork-experiments"
export DSB_DIR="$STATEFUL_ROOT/DeathStarBench"
export FOXTROT_DIR="$HOME/redis-foxtrot-autoscaler"
export CHART_DIR="$DSB_DIR/socialNetwork/helm-chart/socialnetwork"
export SETUP_LOG_DIR="$STATEFUL_ROOT/results/new-cluster-setup-$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$STATEFUL_ROOT/results" "$SETUP_LOG_DIR"

for tool in git kubectl helm curl jq rg python3; do
  command -v "$tool"
done

kubectl config current-context
kubectl get --raw='/readyz?verbose'
kubectl get nodes -o wide
```

## 2. Restore the pinned source trees

Clone the repositories if they are not already present, then select the known
revisions:

```bash
git clone https://github.com/delimitrou/DeathStarBench.git "$DSB_DIR"
git -C "$DSB_DIR" checkout 6ecb09706140f8730b5385c08f1386c654c3c526

git clone https://github.com/SatyamS17/redis-foxtrot-autoscaler.git "$FOXTROT_DIR"
git -C "$FOXTROT_DIR" checkout 6e85a07a2fc4434d1e7f3b2cc74a85e641242668
```

If a directory already exists, do not reclone it; verify it instead:

```bash
git -C "$DSB_DIR" rev-parse HEAD
git -C "$FOXTROT_DIR" rev-parse HEAD
```

## 3. Apply the DeathStarBench integration files

The stock DeathStarBench chart cannot select an external Redis Cluster only for
HomeTimeline. Copy both tracked integration files into the pinned checkout:

```bash
cp -p "$REPRO_ROOT/socialNetwork_changes/values.yaml" \
  "$CHART_DIR/values.yaml"

cp -p "$REPRO_ROOT/socialNetwork_changes/service-config.tpl" \
  "$CHART_DIR/templates/configs/other/service-config.tpl"

git -C "$DSB_DIR" diff --check -- \
  socialNetwork/helm-chart/socialnetwork/values.yaml \
  socialNetwork/helm-chart/socialnetwork/templates/configs/other/service-config.tpl

helm lint "$CHART_DIR"
```

Pods use an init container that clones DeathStarBench content from GitHub, so
the Kubernetes node must have outbound network and DNS access during startup.

## 4. Install Prometheus

FoxTrot expects Prometheus and the experiment expects the Helm release name
`kps`:

```bash
helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts
helm repo update

helm upgrade --install kps \
  prometheus-community/kube-prometheus-stack \
  --version 88.2.0 \
  --namespace monitoring \
  --create-namespace \
  -f "$REPRO_ROOT/manifests/kps-values.yaml" \
  --wait \
  --timeout 20m

kubectl get pods -n monitoring
kubectl get service \
  kps-kube-prometheus-stack-prometheus \
  -n monitoring
kubectl get service prometheus-operated -n monitoring
```

## 5. Install FoxTrot and create Redis

Use the operator manifest from the pinned local checkout rather than an
unpinned `main` URL:

```bash
kubectl apply -f "$REPRO_ROOT/manifests/foxtrot-namespace.yaml"
kubectl apply -f "$FOXTROT_DIR/operator.yaml"

kubectl rollout status \
  deployment/redis-operator-controller-manager \
  -n redis-operator-system \
  --timeout=5m

kubectl get crd redisclusters.cache.example.com

kubectl apply -f "$REPRO_ROOT/manifests/redis-cluster-cloudlab.yaml"
```

Wait for the operator to create the StatefulSet and for all eight Redis pods:

```bash
until kubectl get statefulset redis-cluster -n foxtrot >/dev/null 2>&1; do
  sleep 2
done

kubectl rollout status statefulset/redis-cluster \
  -n foxtrot \
  --timeout=15m

kubectl get pods,pvc -n foxtrot -o wide

kubectl exec -n foxtrot redis-cluster-0 -c redis -- \
  redis-cli --raw CLUSTER INFO
```

Before installing SocialNetwork, require:

```text
cluster_state:ok
cluster_size:3
cluster_known_nodes:8
cluster_slots_ok:16384
cluster_slots_fail:0
```

Keep FoxTrot autoscaling disabled while loading and validating the data.

## 6. Install SocialNetwork with the known-good capacity

The tracked `manifests/socialnetwork-foxtrot-values.yaml` currently records the
external Redis connection and the experiment-wide 2-core limit. The explicit
Helm arguments below additionally reproduce the normal-latency nginx and
fixed-replica configuration without modifying that file.

```bash
helm upgrade --install sn-baseline \
  "$CHART_DIR" \
  --namespace social-network \
  --create-namespace \
  -f "$REPRO_ROOT/manifests/socialnetwork-foxtrot-values.yaml" \
  --set global.hpa.enabled=false \
  --set nginx-thrift.hpa.enabled=false \
  --set post-storage-service.hpa.enabled=false \
  --set home-timeline-service.hpa.enabled=false \
  --set nginx-thrift.replicas=12 \
  --set post-storage-service.replicas=5 \
  --set home-timeline-service.replicas=2 \
  --set-string nginx-thrift.container.resources.limits.cpu=4 \
  --set-string nginx-thrift.container.resources.limits.memory=2Gi \
  --set-string nginx-thrift.container.resources.requests.cpu=100m \
  --set-string nginx-thrift.container.resources.requests.memory=128Mi \
  --set-string nginx-thrift.initContainer.resources.limits.cpu=4 \
  --set-string nginx-thrift.initContainer.resources.limits.memory=2Gi \
  --set-string nginx-thrift.initContainer.resources.requests.cpu=100m \
  --set-string nginx-thrift.initContainer.resources.requests.memory=128Mi \
  --wait \
  --timeout 20m \
  2>&1 | tee "$SETUP_LOG_DIR/socialnetwork-install.txt"
```

Verify the effective configuration rather than assuming Helm applied it:

```bash
kubectl get deployment \
  nginx-thrift post-storage-service home-timeline-service \
  -n social-network \
  -o custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas,CPU_LIMIT:.spec.template.spec.containers[0].resources.limits.cpu

kubectl get hpa -n social-network

kubectl get deployment nginx-thrift -n social-network \
  -o jsonpath='nginx-limit={.spec.template.spec.containers[?(@.name=="nginx-thrift")].resources.limits.cpu}{"\n"}'
```

Expected fixed capacity is `12/5/2`, with no HPAs and nginx limit `4`.

## 7. Initialize Reed98 through a port-forward

The initializer is at `socialNetwork/scripts/init_social_graph.py`, not
`DeathStarBench/scripts/init_social_graph.py`. Its `--limit` option controls
concurrency; Reed98 still registers all 962 users.

Create an untracked virtual environment outside the reproduction repository:

```bash
export DSB_INIT_VENV="$STATEFUL_ROOT/.venvs/dsb-init"
python3 -m venv "$DSB_INIT_VENV"
"$DSB_INIT_VENV/bin/pip" install aiohttp
```

Start and verify the port-forward:

```bash
export NGINX_PF_LOG="$SETUP_LOG_DIR/nginx-port-forward.log"

kubectl port-forward \
  -n social-network \
  service/nginx-thrift \
  18080:8080 \
  >"$NGINX_PF_LOG" 2>&1 &

export NGINX_PF_PID=$!

cleanup_nginx_pf() {
  kill "$NGINX_PF_PID" 2>/dev/null || true
  wait "$NGINX_PF_PID" 2>/dev/null || true
}
trap cleanup_nginx_pf EXIT

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if rg -q 'Forwarding from' "$NGINX_PF_LOG"; then
    break
  fi
  kill -0 "$NGINX_PF_PID"
  sleep 1
done

rg -q 'Forwarding from' "$NGINX_PF_LOG"
```

For a formal experiment, use `--compose` so HomeTimeline data is written to
FoxTrot. Without it, registering users and edges alone does not create the
required timeline dataset.

```bash
cd "$DSB_DIR/socialNetwork"

set +e
"$DSB_INIT_VENV/bin/python" scripts/init_social_graph.py \
  --graph=socfb-Reed98 \
  --ip=127.0.0.1 \
  --port=18080 \
  --limit=50 \
  --compose \
  2>&1 | tee "$SETUP_LOG_DIR/init-social-graph.txt"
INIT_EXIT=${PIPESTATUS[0]}
set -e

test "$INIT_EXIT" -eq 0
if rg -q '^Failed:' "$SETUP_LOG_DIR/init-social-graph.txt"; then
  echo 'STOP: SocialNetwork initialization reported failed requests' >&2
  exit 1
fi
```

Smoke-test the read path:

```bash
curl -fsS -G \
  http://127.0.0.1:18080/wrk2-api/home-timeline/read \
  --data-urlencode 'user_id=0' \
  --data-urlencode 'start=0' \
  --data-urlencode 'stop=10' \
  -o "$SETUP_LOG_DIR/home-timeline-user0.json"

test -s "$SETUP_LOG_DIR/home-timeline-user0.json"
```

Stop the port-forward when initialization and smoke testing are complete:

```bash
cleanup_nginx_pf
trap - EXIT
```

## 8. Formal experiment preflight

Before running `experimental_workflow.md`, verify all of the following:

- Redis pods `0`, `1`, and `2` are slot-bearing masters.
- Redis pod `3` is a replica. The treatment CPU gate expects pods `0--2` to be
  the active masters.
- `redis-cluster-6` is the standby master with no slots and pod `7` is its
  replica.
- The RedisCluster reports three current masters, autoscaling disabled, no
  resharding, and no standby provisioning.
- Redis reports eight known nodes and all 16,384 slots healthy.
- The sum of keys on slot-bearing masters is exactly 962.
- SocialNetwork is fixed at `12/5/2`, nginx has a 4-core limit, and HPAs are
  absent.
- Prometheus is ready and all three metric queries from
  `experimental_workflow.md` return data.
- DeathStarBench `wrk2/wrk` is built and LuaSocket exists at the paths expected
  by the workflow.

The workflow patches FoxTrot thresholds to `10/1/1` immediately before the
formal treatment. The base Redis manifest intentionally uses safer installation
defaults and keeps autoscaling disabled.

Run all workflow sections from the same shell so its exported variables,
functions, port-forward PID, and cleanup trap remain available. Choose exactly
one application-capacity policy. The known-good reference uses fixed capacity,
so skip the HPA-existence branch.

## 9. Files that must remain private

Never add these to Git:

- `~/.kube` or any kubeconfig
- Kubernetes or Helm Secrets
- `.bash_history`
- cloud-provider credentials, tokens, or client certificates
- raw `kubectl get ... -o yaml` cluster dumps
- Python virtual environments, compiled `wrk`, or complete result directories

The repository already contains the small reference dataset under `analysis/`;
copying the complete `stateful-scaling/results` directory is unnecessary.
