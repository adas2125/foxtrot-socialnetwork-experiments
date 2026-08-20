export KUBECONFIG=/users/adas2125/.kube/cloudlab-k3s.yaml
export DAY8_DIR=/users/adas2125/stateful-scaling/results/socialnetwork-day8-formal-20260815-091919
export DSB_DIR=/users/adas2125/stateful-scaling/DeathStarBench
export WRK2_DIR=/users/adas2125/stateful-scaling/DeathStarBench/wrk2
export FORMAL_WRK=/users/adas2125/stateful-scaling/DeathStarBench/wrk2/wrk
export FOXTROT_REPO=/users/adas2125/redis-foxtrot-autoscaler
export LUA_PATH=/usr/share/lua/5.1/\?.lua\;/usr/share/lua/5.1/\?/init.lua\;\;
export LUA_CPATH=/usr/lib/x86_64-linux-gnu/lua/5.1/\?.so\;\;
export NGINX_CLUSTER_IP=10.43.68.170
export FORMAL_RATE=2550
export FORMAL_CONNECTIONS=328
export FORMAL_THREADS=4
export FORMAL_DURATION_SECONDS=600
export FORMAL_CPU_THRESHOLD=9
export FORMAL_SCALE_COOLDOWN=900
export FOXTROT_CPU_QUERY=sum\ by\ \(pod\)\ \(rate\(container_cpu_usage_seconds_total\{container=\"redis\"\,pod=\~\"\^redis-cluster-.\*\"\,namespace=\"foxtrot\"\,service=\"kps-kube-prometheus-stack-kubelet\"\}\[1m\]\)\)\ \*\ 100
export DAY8_SERVICE_CPU_QUERY=sum\ by\ \(pod\,container\)\ \(rate\(container_cpu_usage_seconds_total\{namespace=\"social-network\"\,container=\~\"nginx-thrift\|home-timeline-service\|post-storage-service\|post-storage-memcached\"\}\[1m\]\)\)\ \*\ 100
export DAY8_NODE_CPU_QUERY=100\ -\ avg\(rate\(node_cpu_seconds_total\{mode=\"idle\"\}\[1m\]\)\)\ \*\ 100
