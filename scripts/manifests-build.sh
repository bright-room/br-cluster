#!/usr/bin/env bash
# manifests を kustomize build → Flux postBuild 変数を dummy 展開 → kubeconform で
# 厳密検証する。CI (manifests-ci.yaml) と Make (manifests/build) で共用。
#
# 検査対象:
#   - manifests/clusters/**/kustomization.yaml          (Flux Kustomization wrapper 層)
#   - manifests/platform/**/overlays/prod/kustomization.yaml (CRD インスタンス層)
#
# CRD schema は datreeio/CRDs-catalog (URL 直参照) を引く。catalog に無い場合は
# 個別に -schema-location を足す。
#
# 設計メモ:
#   - `-ignore-missing-schemas` は付けない (= schema 未解決は fail)。
#     例外: -skip CustomResourceDefinition だけは付ける。CRD 定義そのものは
#     vendor から来る前提で、本リポでは新規追加しない。catalog にも
#     standalone schema が無い (yannh の master-standalone-strict 配下)。
#   - Flux postBuild の ${VAR} 展開は scripts/manifests-postbuild-fixtures.env を
#     `set -a; source; set +a` で読み込み、envsubst の allow-list でのみ展開
#     する。Argo Workflow 内のシェル変数や Grafana テンプレ変数は触らない。

set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
fixtures="$repo_root/scripts/manifests-postbuild-fixtures.env"

if [[ ! -f "$fixtures" ]]; then
  echo "::error::fixtures file not found: $fixtures" >&2
  exit 1
fi

# fixtures に列挙された変数だけを envsubst の allow-list として使う
# (= manifest 中の他の ${...} は素通し。Argo Workflow 内のシェル変数を壊さない)。
allowlist=$(grep -E '^[A-Z_][A-Z0-9_]*=' "$fixtures" | cut -d= -f1 | sed 's/^/$/' | tr '\n' ' ')

# shellcheck disable=SC1090
set -a; source "$fixtures"; set +a

CRD_SCHEMA='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

build_dirs=()
while IFS= read -r kfile; do
  build_dirs+=("$(dirname "$kfile")")
done < <(
  {
    find manifests/clusters -name kustomization.yaml -type f
    find manifests/platform -path '*/overlays/prod/kustomization.yaml' -type f
  } | sort -u
)

fail=0
for dir in "${build_dirs[@]}"; do
  echo "::group::kustomize build $dir"

  # kustomize build → envsubst (allow-list) → kubeconform (stdin)。
  # kubeconform は拡張子で判定するため、ファイル経由だと .yaml suffix が必要。
  # `-` 指定で stdin を読ませると素直に YAML としてパースする。
  if ! kustomize build "$dir" \
      | envsubst "$allowlist" \
      | kubeconform \
          -strict \
          -summary \
          -skip CustomResourceDefinition \
          -schema-location default \
          -schema-location "$CRD_SCHEMA" \
          -; then
    fail=1
  fi

  echo "::endgroup::"
done

exit $fail
