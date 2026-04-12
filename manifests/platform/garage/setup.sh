#!/usr/bin/env bash
set -euo pipefail

# Garage cluster provisioning script (v2 admin API for Garage v2.x)
#
# Prerequisites:
#   kubectl port-forward svc/garage-admin 3903:3903 -n garage
#
# Usage:
#   # 1) Layout assign + apply (capacity defaults to 20GB)
#   ./setup.sh layout
#   ./setup.sh layout --capacity 50
#
#   # 2) Create buckets & import keys (keys from 1Password)
#   ./setup.sh provision --bucket k3s-loki --key-name loki \
#                        --bucket k3s-tempo --key-name tempo
#
#   # 2') Or specify keys manually
#   ./setup.sh provision --bucket k3s-loki --key-name loki \
#                        --access-key <AK> --secret-key <SK>
#
#   # 3) Check current state
#   ./setup.sh status
#
# 1Password item "garage-cluster-credentials" field naming:
#   {key_name}_access_key_id / {key_name}_secret_access_key

GARAGE_ADDR="${GARAGE_ADDR:-http://localhost:3903}"
ADMIN_TOKEN="${GARAGE_ADMIN_TOKEN:-}"
OP_ITEM="${GARAGE_OP_ITEM:-garage-cluster-credentials}"

# ---------- helpers ----------

die()  { echo "ERROR: $*" >&2; exit 1; }

api() {
  local method=$1 endpoint=$2
  shift 2
  local response http_code
  response=$(curl -s -w "\n%{http_code}" -X "$method" "${GARAGE_ADDR}${endpoint}" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    "$@") || die "Failed to connect to ${GARAGE_ADDR}${endpoint}"
  http_code=$(echo "$response" | tail -1)
  response=$(echo "$response" | sed '$d')
  if [[ "$http_code" -ge 400 ]]; then
    echo "API error (HTTP ${http_code}): ${response}" >&2
    return 1
  fi
  echo "$response"
}

op_read() {
  local field=$1
  op item get "$OP_ITEM" --fields "$field" --reveal 2>/dev/null \
    || die "Failed to read field '${field}' from 1Password item '${OP_ITEM}'"
}

require_admin_token() {
  if [[ -z "$ADMIN_TOKEN" ]]; then
    # Try k8s secret first, then 1Password
    ADMIN_TOKEN=$(kubectl get secret garage-admin-secret -n garage \
      -o jsonpath='{.data.admin_token}' 2>/dev/null | base64 -d) || true
    if [[ -z "$ADMIN_TOKEN" ]]; then
      echo "==> Reading admin_token from 1Password..."
      ADMIN_TOKEN=$(op_read admin_token)
    fi
    [[ -n "$ADMIN_TOKEN" ]] || die "Could not obtain admin token from k8s secret or 1Password"
  fi
}

# ---------- layout ----------

cmd_layout() {
  local capacity_gb=20 zone="dc1"
  while [[ $# -gt 0 ]]; do
    case $1 in
      --capacity) capacity_gb=$2; shift 2 ;;
      --zone)     zone=$2;       shift 2 ;;
      *) die "Unknown layout option: $1" ;;
    esac
  done

  local capacity_bytes=$(( capacity_gb * 1073741824 ))

  echo "==> Fetching cluster status..."
  local status
  status=$(api GET /v2/GetClusterStatus)

  local node_ids
  node_ids=$(echo "$status" | jq -r '.nodes[] | select(.isUp == true) | .id')
  [[ -n "$node_ids" ]] || die "No online nodes found"

  local i=0
  while IFS= read -r node_id; do
    echo "==> Assigning layout: node=${node_id:0:12}... zone=${zone} capacity=${capacity_gb}GB"
    api POST /v2/UpdateClusterLayout -d "$(jq -n \
      --arg id "$node_id" \
      --arg zone "$zone" \
      --argjson cap "$capacity_bytes" \
      --arg tag "garage-${i}" \
      '{($id): {zone: $zone, capacity: $cap, tags: [$tag]}}'
    )" > /dev/null
    i=$((i + 1))
  done <<< "$node_ids"

  echo "==> Fetching current layout version..."
  local layout_info
  layout_info=$(api GET /v2/GetClusterLayout)
  local version
  version=$(echo "$layout_info" | jq -r '.version + 1')

  echo "==> Applying layout (version=${version})..."
  api POST /v2/ApplyClusterLayout -d "{\"version\": ${version}}" > /dev/null
  echo "Layout applied."
}

# ---------- provision ----------

cmd_provision() {
  local buckets=() key_names=() access_keys=() secret_keys=()

  while [[ $# -gt 0 ]]; do
    case $1 in
      --bucket)     buckets+=("$2");     shift 2 ;;
      --key-name)   key_names+=("$2");   shift 2 ;;
      --access-key) access_keys+=("$2"); shift 2 ;;
      --secret-key) secret_keys+=("$2"); shift 2 ;;
      *) die "Unknown provision option: $1" ;;
    esac
  done

  local n=${#buckets[@]}
  [[ $n -gt 0 ]] || die "At least one --bucket is required"
  [[ ${#key_names[@]} -eq $n ]] || die "Each --bucket needs a --key-name"

  # Fill missing keys from 1Password
  for (( i=0; i<n; i++ )); do
    if [[ -z "${access_keys[$i]:-}" ]]; then
      echo "==> Reading ${key_names[$i]} credentials from 1Password..."
      access_keys[$i]=$(op_read "${key_names[$i]}_access_key_id")
      secret_keys[$i]=$(op_read "${key_names[$i]}_secret_access_key")
    fi
  done

  for (( i=0; i<n; i++ )); do
    local bucket="${buckets[$i]}"
    local key_name="${key_names[$i]}"
    local ak="${access_keys[$i]}"
    local sk="${secret_keys[$i]}"

    # Create bucket
    echo "==> Creating bucket: ${bucket}"
    api POST /v2/CreateBucket -d "{\"globalAlias\": \"${bucket}\"}" > /dev/null || true

    # Import key
    echo "==> Importing key: ${key_name}"
    api POST /v2/ImportKey -d "$(jq -n \
      --arg ak "$ak" \
      --arg sk "$sk" \
      --arg name "$key_name" \
      '{accessKeyId: $ak, secretAccessKey: $sk, name: $name}'
    )" > /dev/null || true

    # Get bucket ID
    local bucket_id
    bucket_id=$(api GET /v2/ListBuckets | jq -r \
      --arg alias "$bucket" \
      '.[] | select(.globalAliases[]? == $alias) | .id')
    [[ -n "$bucket_id" ]] || die "Bucket ${bucket} not found after creation"

    # Grant permissions
    echo "==> Granting permissions: ${key_name} -> ${bucket}"
    api POST /v2/AllowBucketKey -d "$(jq -n \
      --arg bid "$bucket_id" \
      --arg ak "$ak" \
      '{bucketId: $bid, accessKeyId: $ak, permissions: {read: true, write: true, owner: true}}'
    )" > /dev/null

    echo ""
  done

  echo "Provisioning complete."
}

# ---------- status ----------

cmd_status() {
  echo "=== Cluster Status ==="
  api GET /v2/GetClusterStatus | jq '{
    nodes: [.nodes[] | {id: .id[:12], isUp, hostname, addr}]
  }'

  echo ""
  echo "=== Layout ==="
  api GET /v2/GetClusterLayout | jq '{
    version,
    roles: [.roles // {} | to_entries[] | {id: .key[:12], zone: .value.zone, capacity: (.value.capacity / 1073741824 | tostring + "GB"), tags: .value.tags}]
  }'

  echo ""
  echo "=== Buckets ==="
  api GET /v2/ListBuckets | jq '[.[] | {
    id: .id[:12],
    aliases: .globalAliases
  }]'

  echo ""
  echo "=== Keys ==="
  api GET /v2/ListKeys | jq '.'
}

# ---------- main ----------

[[ $# -ge 1 ]] || die "Usage: $0 {layout|provision|status} [options]"

require_admin_token

cmd="$1"; shift
case "$cmd" in
  layout)    cmd_layout "$@" ;;
  provision) cmd_provision "$@" ;;
  status)    cmd_status ;;
  *)         die "Unknown command: $cmd (use 'layout', 'provision', or 'status')" ;;
esac
