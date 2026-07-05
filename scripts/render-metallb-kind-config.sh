#!/usr/bin/env bash
set -euo pipefail

network="kind"
namespace="metallb-system"
pool_name="kind-pool"
context=""
apply="false"
template="platform/metallb/kind/l2-config.yaml.tpl"

usage() {
  printf 'Usage: %s [--network <name>] [--namespace <name>] [--pool-name <name>] [--context <context>] [--apply]\n' "$0" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --network)
      network="${2:?missing value for --network}"
      shift 2
      ;;
    --namespace)
      namespace="${2:?missing value for --namespace}"
      shift 2
      ;;
    --pool-name)
      pool_name="${2:?missing value for --pool-name}"
      shift 2
      ;;
    --context)
      context="${2:?missing value for --context}"
      shift 2
      ;;
    --apply)
      apply="true"
      shift
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

if [[ "$apply" == "true" && -z "$context" ]]; then
  printf 'error: --context is required with --apply\n' >&2
  exit 1
fi

if [[ ! -f "$template" ]]; then
  printf 'error: template not found: %s\n' "$template" >&2
  exit 1
fi

ip_to_int() {
  local ip="$1"
  local a b c d
  IFS=. read -r a b c d <<< "$ip"

  for octet in "$a" "$b" "$c" "$d"; do
    if [[ ! "$octet" =~ ^[0-9]+$ || "$octet" -lt 0 || "$octet" -gt 255 ]]; then
      printf 'error: invalid IPv4 address: %s\n' "$ip" >&2
      exit 1
    fi
  done

  printf '%u\n' $(( (a << 24) + (b << 16) + (c << 8) + d ))
}

int_to_ip() {
  local value="$1"
  printf '%d.%d.%d.%d\n' \
    $(( (value >> 24) & 255 )) \
    $(( (value >> 16) & 255 )) \
    $(( (value >> 8) & 255 )) \
    $(( value & 255 ))
}

docker_subnets="$(docker network inspect "$network" --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}')"
ipv4_cidr=""

while IFS= read -r subnet; do
  if [[ -n "$subnet" && "$subnet" != *:* ]]; then
    ipv4_cidr="$subnet"
    break
  fi
done <<< "$docker_subnets"

if [[ -z "$ipv4_cidr" ]]; then
  printf 'error: no IPv4 subnet found for Docker network: %s\n' "$network" >&2
  exit 1
fi

network_ip="${ipv4_cidr%/*}"
prefix="${ipv4_cidr#*/}"

if [[ ! "$prefix" =~ ^[0-9]+$ || "$prefix" -lt 1 || "$prefix" -gt 26 ]]; then
  printf 'error: unsupported Docker network prefix %s from %s; need /1 through /26\n' "$prefix" "$ipv4_cidr" >&2
  exit 1
fi

network_int="$(ip_to_int "$network_ip")"
mask=$(( (0xffffffff << (32 - prefix)) & 0xffffffff ))
network_int=$(( network_int & mask ))
size=$(( 1 << (32 - prefix) ))

pool_start=$(( network_int + size - 56 ))
pool_end=$(( network_int + size - 6 ))

if [[ "$pool_start" -le "$network_int" || "$pool_end" -le "$pool_start" ]]; then
  printf 'error: could not derive a safe MetalLB pool from subnet: %s\n' "$ipv4_cidr" >&2
  exit 1
fi

address_range="$(int_to_ip "$pool_start")-$(int_to_ip "$pool_end")"
rendered="$(<"$template")"
rendered="${rendered//\{\{POOL_NAME\}\}/$pool_name}"
rendered="${rendered//\{\{NAMESPACE\}\}/$namespace}"
rendered="${rendered//\{\{ADDRESS_RANGE\}\}/$address_range}"

if [[ "$apply" == "true" ]]; then
  printf 'Applying MetalLB layer 2 config for Docker network %s (%s): %s\n' "$network" "$ipv4_cidr" "$address_range" >&2
  printf '%s\n' "$rendered" | kubectl --context "$context" apply -f -
else
  printf '%s\n' "$rendered"
fi
