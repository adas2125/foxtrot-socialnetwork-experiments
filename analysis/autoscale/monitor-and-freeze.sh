#!/usr/bin/env bash

set -u

OUTPUT="$1"
INITIAL_LAST_SCALE="$2"

printf 'epoch_ns\tiso_time\tmasters\tautoscaling\tstandby\tlast_scale\tsts_desired\tsts_ready\tcluster_state\tcluster_size\tknown_nodes\tslots_ok\tslots_fail\taction\n' \
  > "$OUTPUT"

while true
do
  EPOCH_NS="$(date +%s%N)"
  ISO_TIME="$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)"

  CR_JSON="$(
    kubectl get rediscluster redis-cluster \
      -n foxtrot \
      -o json \
      2>/dev/null ||
    true
  )"

  MASTERS="$(printf '%s' "$CR_JSON" | jq -r '.spec.masters // "NA"')"
  AUTOSCALING="$(printf '%s' "$CR_JSON" | jq -r '.spec.autoScaleEnabled // "NA"')"
  STANDBY="$(printf '%s' "$CR_JSON" | jq -r '.status.standbyPod // "NA"')"
  LAST_SCALE="$(printf '%s' "$CR_JSON" | jq -r '.status.lastScaleTime // "NA"')"
  RESHARDING="$(printf '%s' "$CR_JSON" | jq -r '.status.isResharding // false')"
  PROVISIONING="$(printf '%s' "$CR_JSON" | jq -r '.status.isProvisioningStandby // false')"

  STS_VALUES="$(
    kubectl get statefulset redis-cluster \
      -n foxtrot \
      -o jsonpath='{.spec.replicas}{"\t"}{.status.readyReplicas}' \
      2>/dev/null ||
    printf 'NA\tNA'
  )"

  IFS=$'\t' read -r STS_DESIRED STS_READY <<< "$STS_VALUES"

  CLUSTER_INFO="$(
    kubectl exec -n foxtrot redis-cluster-0 -c redis \
      -- redis-cli --raw CLUSTER INFO \
      2>/dev/null |
    tr -d '\r' ||
    true
  )"

  field() {
    printf '%s\n' "$CLUSTER_INFO" |
    sed -n "s/^$1://p"
  }

  CLUSTER_STATE="$(field cluster_state)"
  CLUSTER_SIZE="$(field cluster_size)"
  KNOWN_NODES="$(field cluster_known_nodes)"
  SLOTS_OK="$(field cluster_slots_ok)"
  SLOTS_FAIL="$(field cluster_slots_fail)"

  ACTION="none"

  if [[ "$MASTERS" =~ ^[0-9]+$ ]] &&
     (( MASTERS < 3 )) &&
     test "$AUTOSCALING" = "true"
  then
    if kubectl patch rediscluster redis-cluster \
      -n foxtrot \
      --type=merge \
      -p '{"spec":{"autoScaleEnabled":false}}' \
      > /dev/null
    then
      ACTION="emergency_freeze_below_three"
    else
      ACTION="emergency_freeze_failed"
    fi

  elif [[ "$MASTERS" =~ ^[0-9]+$ ]] &&
       (( MASTERS >= 4 )) &&
       test "$AUTOSCALING" = "true" &&
       test "$RESHARDING" = "false" &&
       test "$PROVISIONING" = "false" &&
       test "$STANDBY" = "redis-cluster-8" &&
       test "$LAST_SCALE" != "NA" &&
       test "$LAST_SCALE" != "$INITIAL_LAST_SCALE" &&
       test "$STS_DESIRED" = "10" &&
       test "$STS_READY" = "10" &&
       test "$CLUSTER_STATE" = "ok" &&
       test "$CLUSTER_SIZE" = "4" &&
       test "$KNOWN_NODES" = "10" &&
       test "$SLOTS_OK" = "16384" &&
       test "$SLOTS_FAIL" = "0"
  then
    if kubectl patch rediscluster redis-cluster \
      -n foxtrot \
      --type=merge \
      -p '{"spec":{"autoScaleEnabled":false}}' \
      > /dev/null
    then
      ACTION="freeze_completed_scale_at_four"
    else
      ACTION="freeze_at_four_failed"
    fi
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$EPOCH_NS" \
    "$ISO_TIME" \
    "${MASTERS:-NA}" \
    "${AUTOSCALING:-NA}" \
    "${STANDBY:-NA}" \
    "${LAST_SCALE:-NA}" \
    "${STS_DESIRED:-NA}" \
    "${STS_READY:-NA}" \
    "${CLUSTER_STATE:-NA}" \
    "${CLUSTER_SIZE:-NA}" \
    "${KNOWN_NODES:-NA}" \
    "${SLOTS_OK:-NA}" \
    "${SLOTS_FAIL:-NA}" \
    "$ACTION" \
    >> "$OUTPUT"

  sleep 5
done
