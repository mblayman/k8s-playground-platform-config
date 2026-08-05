#!/usr/bin/env bash
set -euo pipefail

context=""
timeout="180"
renew_before_seconds="43200"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  printf 'Usage: %s --context <context> [--timeout <seconds>] [--renew-before-seconds <seconds>]\n' "$0" >&2
}

controller_for_pod() {
  local namespace="$1"
  local pod="$2"
  local owner owner_kind owner_name parent parent_kind parent_name

  owner="$(kubectl --context "$context" -n "$namespace" get pod "$pod" -o json | jq -r 'first(.metadata.ownerReferences[]? | select(.controller == true) | [.kind, .name] | @tsv) // empty')"
  if [[ -z "$owner" ]]; then
    return 1
  fi

  IFS=$'\t' read -r owner_kind owner_name <<< "$owner"
  case "$owner_kind" in
    ReplicaSet)
      parent="$(kubectl --context "$context" -n "$namespace" get replicaset "$owner_name" -o json | jq -r 'first(.metadata.ownerReferences[]? | select(.controller == true) | [.kind, .name] | @tsv) // empty')"
      if [[ -z "$parent" ]]; then
        return 1
      fi
      IFS=$'\t' read -r parent_kind parent_name <<< "$parent"
      if [[ "$parent_kind" != "Deployment" ]]; then
        return 1
      fi
      printf 'deployment/%s\n' "$parent_name"
      ;;
    DaemonSet)
      printf 'daemonset/%s\n' "$owner_name"
      ;;
    StatefulSet)
      printf 'statefulset/%s\n' "$owner_name"
      ;;
    *)
      return 1
      ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      context="${2:?missing value for --context}"
      shift 2
      ;;
    --timeout)
      timeout="${2:?missing value for --timeout}"
      shift 2
      ;;
    --renew-before-seconds)
      renew_before_seconds="${2:?missing value for --renew-before-seconds}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$context" ]]; then
  usage
  exit 1
fi

if [[ ! "$timeout" =~ ^[0-9]+$ || ! "$renew_before_seconds" =~ ^[0-9]+$ || "$timeout" -le 0 ]]; then
  printf 'error: --timeout must be a positive integer and --renew-before-seconds must be a non-negative integer\n' >&2
  exit 1
fi

for command in kubectl jq date; do
  if ! command -v "$command" >/dev/null; then
    printf 'error: required command not found: %s\n' "$command" >&2
    exit 1
  fi
done

if ! pods_json="$(kubectl --context "$context" get pods --all-namespaces -o json)"; then
  printf 'error: could not list pods from context %s\n' "$context" >&2
  exit 1
fi

mapfile -t mesh_pods < <(
  jq -r '.items[] | select(.status.phase == "Running") | select(any(.spec.containers[]?; .name == "istio-proxy") or any(.spec.initContainers[]?; .name == "istio-proxy")) | [.metadata.namespace, .metadata.name] | @tsv' <<< "$pods_json"
)

if [[ "${#mesh_pods[@]}" -eq 0 ]]; then
  printf 'No running pods with an istio-proxy container were found.\n'
  exit 0
fi

now_epoch="$(date +%s)"
renewal_deadline=$((now_epoch + renew_before_seconds))
inspection_failures=0
repair_candidates=()

for mesh_pod in "${mesh_pods[@]}"; do
  IFS=$'\t' read -r namespace pod <<< "$mesh_pod"

  if ! certs="$(kubectl --context "$context" -n "$namespace" exec "$pod" -c istio-proxy -- pilot-agent request GET certs 2>/dev/null)"; then
    printf 'warning: could not inspect Istio certificates for %s/%s\n' "$namespace" "$pod" >&2
    inspection_failures=$((inspection_failures + 1))
    continue
  fi

  if ! expiration="$(jq -er '[.certificates[]?.cert_chain[]?.expiration_time | select(type == "string" and length > 0)] | min // empty' <<< "$certs" 2>/dev/null)"; then
    printf 'warning: no Istio workload certificate expiration found for %s/%s\n' "$namespace" "$pod" >&2
    inspection_failures=$((inspection_failures + 1))
    continue
  fi

  if ! expiration_epoch="$(date --date "$expiration" +%s 2>/dev/null)"; then
    printf 'warning: could not parse Istio certificate expiration %s for %s/%s\n' "$expiration" "$namespace" "$pod" >&2
    inspection_failures=$((inspection_failures + 1))
    continue
  fi

  if [[ "$expiration_epoch" -gt "$renewal_deadline" ]]; then
    printf 'Istio certificate is healthy for %s/%s through %s.\n' "$namespace" "$pod" "$expiration"
    continue
  fi

  if ! controller="$(controller_for_pod "$namespace" "$pod")"; then
    printf 'warning: %s/%s needs certificate renewal but is not owned by a supported workload controller\n' "$namespace" "$pod" >&2
    inspection_failures=$((inspection_failures + 1))
    continue
  fi

  repair_candidates+=("$namespace"$'\t'"$pod"$'\t'"$controller"$'\t'"$expiration")
done

for candidate in "${repair_candidates[@]}"; do
  IFS=$'\t' read -r namespace pod controller expiration <<< "$candidate"

  if ! kubectl --context "$context" -n "$namespace" get pod "$pod" >/dev/null 2>&1; then
    printf 'Pod %s/%s was already replaced; skipping.\n' "$namespace" "$pod"
    continue
  fi

  printf 'Recycling %s/%s owned by %s; certificate expires at %s.\n' "$namespace" "$pod" "$controller" "$expiration"
  kubectl --context "$context" -n "$namespace" delete pod "$pod" --wait=true --timeout="${timeout}s"
  "$script_dir/wait-for-rollout.sh" \
    --context "$context" \
    --namespace "$namespace" \
    --resource "$controller" \
    --timeout "$timeout"
done

if [[ "${#repair_candidates[@]}" -eq 0 ]]; then
  printf 'No Istio workload certificates expire within the next %ss.\n' "$renew_before_seconds"
else
  printf 'Repaired %s pod(s) with expired or near-expiry Istio workload certificates.\n' "${#repair_candidates[@]}"
fi

if [[ "$inspection_failures" -gt 0 ]]; then
  printf 'error: certificate inspection or repair eligibility failed for %s pod(s)\n' "$inspection_failures" >&2
  exit 1
fi
