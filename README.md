# FoxTrot SocialNetwork Experiments

This repository contains the manifests, workload helpers, analysis code, and recorded reference run used to evaluate FoxTrot autoscaling with DeathStarBench SocialNetwork.

The experiment compares three conditions under the same read-home-timeline workload:

1. A fixed three-master Redis Cluster.
2. FoxTrot autoscaling from three to four masters using the standby pod.
3. The resulting fixed four-master Redis Cluster.

## Repository layout

- `experimental_workflow.md`: complete command flow for collecting a new experiment.
- `experiment/`: autoscaling treatment, topology monitor, and p99 timestamp helpers.
- `manifests/`: FoxTrot, Prometheus, and SocialNetwork configuration used by the experiment.
- `socialNetwork_changes/`: DeathStarBench Helm-chart integration for an external Redis Cluster.
- `analysis/simplified-analysis/`: analysis program, requirements, reference inputs, tables, and figures.
- `analysis/fixed3`, `analysis/autoscale`, and `analysis/fixed4`: raw outputs from the recorded reference run.

Python virtual environments are intentionally excluded by `.gitignore`. Create a new environment from `analysis/simplified-analysis/requirements.txt` on each machine.

## Source versions
The reference figures were verified with Python 3.12, Matplotlib 3.11.1, NumPy 2.5.2, and pandas 3.0.5.

A DeathStarBench fork is not required.

```bash
git clone https://github.com/delimitrou/DeathStarBench.git
git -C DeathStarBench checkout 6ecb09706140f8730b5385c08f1386c654c3c526

git clone https://github.com/SatyamS17/redis-foxtrot-autoscaler.git
git -C redis-foxtrot-autoscaler checkout 6e85a07a2fc4434d1e7f3b2cc74a85e641242668
```

## DeathStarBench integration

Copy the two integration files into a DeathStarBench checkout:

```text
socialNetwork_changes/values.yaml
  -> DeathStarBench/socialNetwork/helm-chart/socialnetwork/values.yaml

socialNetwork_changes/service-config.tpl
  -> DeathStarBench/socialNetwork/helm-chart/socialnetwork/templates/configs/other/service-config.tpl
```

These changes let the SocialNetwork chart direct the home-timeline Redis dependency to FoxTrot's external Redis Cluster while leaving the other Redis-backed services on their original deployments.

## Prerequisites

The workflow assumes a Linux VM with the following available:

- A working Kubernetes cluster and `KUBECONFIG`.
- FoxTrot installed at the revision listed above.
- DeathStarBench SocialNetwork deployed with the integration files and `manifests/socialnetwork-foxtrot-values.yaml`.
- DeathStarBench's `wrk2` built successfully.
- Prometheus installed from `manifests/kps-values.yaml`.
- `kubectl`, `helm`, `curl`, `jq`, `rg`, Python 3 with `venv`, and LuaSocket.
- A clean three-master FoxTrot topology with one replica per master, standby pod `redis-cluster-6`, and 962 logical keys.

The workflow currently uses Debian/Ubuntu x86-64 Lua paths:

```text
/usr/share/lua/5.1
/usr/lib/x86_64-linux-gnu/lua/5.1
```

Adjust `LUA_PATH` and `LUA_CPATH` when using a different distribution or architecture.

## Application capacity policy

The workflow contains two mutually exclusive choices:

- Keep the three SocialNetwork HPAs active so application services can scale independently of FoxTrot.
- Run the optional fixed-capacity block, which deletes those HPAs and fixes the deployment replica counts.

Choose one policy and record it for every run. Do not execute the optional fixed-capacity block when testing with SocialNetwork replica autoscaling enabled. The workflow checks that the expected HPAs exist, but their creation and tuning are part of cluster setup.

## Running the experiment

Open `experimental_workflow.md` and execute the sections in order. Before starting, update the path variables and the three helper-source variables marked `TODO` for the new machine.

For the repository layout shown above, replace those helper-source variables with:

```bash
export REPRO_ROOT="$HOME/foxtrot-socialnetwork-experiments"
export TREATMENT_SOURCE="$REPRO_ROOT/experiment"
export P99_SOURCE="$REPRO_ROOT/experiment/timestamp-p99.py"
export ANALYSIS_TEMPLATE="$REPRO_ROOT/analysis/simplified-analysis"
```

The workflow discovers the `nginx-thrift` ClusterIP dynamically. It creates a timestamped result directory containing the fixed-three, autoscaling, fixed-four, and simplified-analysis outputs.

The workload-specific assumptions are intentionally strict: the preflight requires three masters, eight known Redis nodes, standby `redis-cluster-6`, full slot coverage, and 962 logical keys.

## Regenerating the reference figures

The saved reference inputs are already located under `analysis/simplified-analysis/inputs`:

```bash
python3 -m venv analysis/simplified-analysis/.venv
analysis/simplified-analysis/.venv/bin/pip install \
  -r analysis/simplified-analysis/requirements.txt

MPLBACKEND=Agg \
  analysis/simplified-analysis/.venv/bin/python \
  analysis/simplified-analysis/analysis.py
```

The analysis writes five figures to `analysis/simplified-analysis/figures` and summary CSV files to `analysis/simplified-analysis/tables`.
