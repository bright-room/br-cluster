#!/usr/bin/env bash
# Flux Kustomization の spec.postBuild.substituteFrom が参照する
# Secret / ConfigMap が、同じ namespace 内に manifest として存在するかを
# 静的にチェックする。
#
# 動機: postBuild は Kustomization と同じ namespace の Secret/ConfigMap しか
# 引けない (PR #251 / #252 / #253 で踏んだ罠)。manifest だけ眺めても発覚
# しないので、PR 時点で referenced ↔ provided の cross-ref を機械化する。
#
# providers として認める形:
#   - ExternalSecret { metadata.namespace == NS, (spec.target.name // metadata.name) == NAME }
#   - ConfigMap      { metadata.namespace == NS, metadata.name == NAME }
#   - Secret         { metadata.namespace == NS, metadata.name == NAME }  (policy で
#                    禁止されているが、念のため受理する。policy 側で別途弾く)
#
# 未対応: kustomize の configMapGenerator / secretGenerator は今のところ
# 該当用途がないため対象外。出てきたら拡張する。

set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root"

# 1) 全 manifest から provider を <NS>/<KIND>/<NAME> 形式で収集。
#    yq の式は xargs 経由で渡るため 1 行にまとめる。
# ExternalSecret は Secret として登録、provider 種類ごとに 3 expr に分けて
# 集合和を取る (yq の if/then で組むより素直)。
# 一部の patch ファイル (helm-patch.yaml 等) は root が array なので
# `(.kind? // "")` でガードしてから select する。
EXT_EXPR='select((.kind? // "") == "ExternalSecret") | (.metadata.namespace // "default") + "/Secret/" + (.spec.target.name // .metadata.name)'
CM_EXPR='select((.kind? // "") == "ConfigMap")     | (.metadata.namespace // "default") + "/ConfigMap/" + .metadata.name'
SEC_EXPR='select((.kind? // "") == "Secret")        | (.metadata.namespace // "default") + "/Secret/"    + .metadata.name'

collect() {
  find manifests -type f \( -name '*.yaml' -o -name '*.yml' \) -print0 \
    | xargs -0 yq -N -o=json "$1" 2>/dev/null \
    | tr -d '"'
}

providers=$( { collect "$EXT_EXPR"; collect "$CM_EXPR"; collect "$SEC_EXPR"; } | sort -u )

# 2) Flux Kustomization の substituteFrom を抽出。
REFERENCE_EXPR='select((.kind? // "") == "Kustomization" and (.apiVersion? // "") == "kustomize.toolkit.fluxcd.io/v1") | .spec.postBuild.substituteFrom[]? | (parent | parent | parent | parent | .metadata.namespace // "flux-system") + "/" + (.kind // "ConfigMap") + "/" + .name'

fail=0
while IFS=$'\t' read -r key src; do
  [[ -z "$key" ]] && continue
  if ! grep -qxF "$key" <<<"$providers"; then
    echo "::error file=$src::substituteFrom が参照する $key を提供する ExternalSecret/ConfigMap/Secret が manifests/ に存在しない" >&2
    fail=1
  fi
done < <(
  while IFS= read -r f; do
    yq -N -o=json "$REFERENCE_EXPR" "$f" 2>/dev/null \
      | tr -d '"' \
      | awk -v src="$f" 'NF { print $0 "\t" src }'
  done < <(find manifests -type f \( -name '*.yaml' -o -name '*.yml' \))
)

if [[ $fail -eq 0 ]]; then
  echo "manifests-substitute-check: all substituteFrom references resolved"
fi
exit $fail
