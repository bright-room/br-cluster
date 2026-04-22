#!/usr/bin/env bash
# Fetch community Grafana dashboards from grafana.com and write them out as
# ConfigMap sources for the Grafana sidecar-based provisioning.
#
# Each dashboard is downloaded at its latest revision, stripped of import-dialog
# metadata (__inputs / __requires / __elements), and has `${DS_*}` placeholders
# replaced with the actual datasource UIDs used in
# manifests/platform/grafana/app/base/values.yaml (prometheus / loki / tempo).
#
# Re-run this script to refresh dashboards:
#
#     ./scripts/fetch-grafana-dashboards.sh
#
# Requires: bash, curl, jq.
set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
TARGET_JSON_DIR="$REPO_ROOT/manifests/platform/grafana/dashboards/base/json"
KUSTOMIZATION="$REPO_ROOT/manifests/platform/grafana/dashboards/base/kustomization.yaml"

mkdir -p "$TARGET_JSON_DIR"

# format: "<slug>|<grafana.com id>|<Grafana folder>"
DASHBOARDS=(
  "node-exporter-full|1860|Host"
  "kubernetes-views-global|15757|Kubernetes"
  "loki|13407|Observability"
  "tempo|17969|Observability"
  "alertmanager|9578|Observability"
  "cloudnative-pg|20417|Database"
  "longhorn|22705|Storage"
)

for spec in "${DASHBOARDS[@]}"; do
  IFS='|' read -r name id _folder <<< "$spec"
  echo "Fetching ${name} (id=${id})..."
  revision=$(curl -fsSL "https://grafana.com/api/dashboards/${id}" | jq -r '.revision')
  curl -fsSL "https://grafana.com/api/dashboards/${id}/revisions/${revision}/download" \
    | jq 'del(.__inputs, .__requires, .__elements)' \
    | sed -e 's/\${DS_PROMETHEUS}/prometheus/g' \
          -e 's/\${DS_LOKI}/loki/g' \
          -e 's/\${DS_TEMPO}/tempo/g' \
          -e 's/"datasource": "prometheus"/"datasource": {"type": "prometheus", "uid": "prometheus"}/g' \
          -e 's/"datasource": "loki"/"datasource": {"type": "loki", "uid": "loki"}/g' \
          -e 's/"datasource": "tempo"/"datasource": {"type": "tempo", "uid": "tempo"}/g' \
    > "$TARGET_JSON_DIR/${name}.json"
  echo "  revision=${revision} bytes=$(wc -c < "$TARGET_JSON_DIR/${name}.json")"
done

# Regenerate kustomization.yaml so the list matches exactly the fetched set.
cat > "$KUSTOMIZATION" <<'HEADER'
# Managed by scripts/fetch-grafana-dashboards.sh — do not edit by hand.
# To refresh, re-run the script.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: grafana

generatorOptions:
  disableNameSuffixHash: true

configMapGenerator:
HEADER

for spec in "${DASHBOARDS[@]}"; do
  IFS='|' read -r name id folder <<< "$spec"
  cat >> "$KUSTOMIZATION" <<EOF
  - name: grafana-dashboard-${name}
    files:
      - json/${name}.json
    options:
      labels:
        grafana_dashboard: "1"
      annotations:
        grafana_folder: ${folder}
EOF
done

echo ""
echo "Done. Review changes with: git diff manifests/platform/grafana/dashboards/base"
