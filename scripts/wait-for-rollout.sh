#!/usr/bin/env bash
set -euo pipefail

context=""
namespace=""
resource=""
selector=""
timeout="300"
poll_interval="10"
report_interval="30"
start_delay="0"

usage() {
  printf 'Usage: %s --context <context> --namespace <namespace> --resource <type/name> [--selector <selector>] [--timeout <seconds>] [--poll-interval <seconds>] [--report-interval <seconds>] [--start-delay <seconds>]\n' "$0" >&2
}

print_rollout_pending() {
  local output="$1"

  if [[ "$output" == *"timed out waiting for the condition"* ]]; then
    return
  fi

  printf '%s\n' "$output"
}

pods_ready() {
  if [[ -z "$selector" ]]; then
    return 0
  fi

  kubectl --context "$context" -n "$namespace" wait pod -l "$selector" --for=condition=Ready --timeout=1s >/dev/null 2>&1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      context="${2:?missing value for --context}"
      shift 2
      ;;
    --namespace)
      namespace="${2:?missing value for --namespace}"
      shift 2
      ;;
    --resource)
      resource="${2:?missing value for --resource}"
      shift 2
      ;;
    --selector)
      selector="${2:?missing value for --selector}"
      shift 2
      ;;
    --timeout)
      timeout="${2:?missing value for --timeout}"
      shift 2
      ;;
    --poll-interval)
      poll_interval="${2:?missing value for --poll-interval}"
      shift 2
      ;;
    --report-interval)
      report_interval="${2:?missing value for --report-interval}"
      shift 2
      ;;
    --start-delay)
      start_delay="${2:?missing value for --start-delay}"
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

if [[ -z "$context" || -z "$namespace" || -z "$resource" ]]; then
  usage
  exit 1
fi

if [[ ! "$timeout" =~ ^[0-9]+$ || ! "$poll_interval" =~ ^[0-9]+$ || ! "$report_interval" =~ ^[0-9]+$ || ! "$start_delay" =~ ^[0-9]+$ || "$timeout" -le 0 || "$poll_interval" -le 0 || "$report_interval" -le 0 ]]; then
  printf 'error: --timeout, --poll-interval, and --report-interval must be positive integer seconds; --start-delay must be a non-negative integer second value\n' >&2
  exit 1
fi

if [[ "$start_delay" -gt 0 ]]; then
  sleep "$start_delay"
fi

deadline=$((SECONDS + timeout))
next_report=0
printf 'Waiting up to %ss for %s in namespace %s\n' "$timeout" "$resource" "$namespace"

while true; do
  status_output="$(kubectl --context "$context" -n "$namespace" rollout status "$resource" --timeout=1s 2>&1)" && status=0 || status=$?

  if [[ "$status" -eq 0 ]]; then
    if pods_ready; then
      printf '%s\n' "$status_output"
      if [[ -n "$selector" ]]; then
        printf 'Pods matching selector "%s" are ready.\n' "$selector"
      fi
      exit 0
    fi

    status_output="Rollout complete; waiting for pods matching selector \"$selector\" to be Ready."
  fi

  if [[ "$SECONDS" -ge "$deadline" ]]; then
    printf 'Timed out waiting for %s in namespace %s\n' "$resource" "$namespace" >&2
    kubectl --context "$context" -n "$namespace" get "$resource" -o wide >&2 || true
    if [[ -n "$selector" ]]; then
      kubectl --context "$context" -n "$namespace" get pods -l "$selector" -o wide >&2 || true
    fi
    exit 1
  fi

  if [[ "$SECONDS" -ge "$next_report" ]]; then
    printf '[%s] Rollout still pending: %s\n' "$(date --iso-8601=seconds)" "$resource"
    print_rollout_pending "$status_output"
    kubectl --context "$context" -n "$namespace" get "$resource" -o wide || true
    if [[ -n "$selector" ]]; then
      kubectl --context "$context" -n "$namespace" get pods -l "$selector" -o wide || true
    fi
    next_report=$((SECONDS + report_interval))
  fi
  sleep "$poll_interval"
done
