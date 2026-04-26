#!/usr/bin/env bash
# manifests/clusters/**/kustomization.yaml を kustomize build → kubeconform で検査する。
# CI (manifests-ci.yaml) と Make (manifests/build) で共用。
set -euo pipefail

fail=0
# globstar は macOS の bash 3.2 に無いので find で代替 (CI / ローカル両対応)
while IFS= read -r kfile; do
  dir=$(dirname "$kfile")
  echo "::group::kustomize build $dir"
  if ! kustomize build "$dir" > /tmp/built.yaml; then
    echo "::error::kustomize build failed for $dir"
    fail=1
    echo "::endgroup::"
    continue
  fi
  # CRD 由来の schema は missing-schemas で許容し、core k8s スキーマを strict で検査
  if ! kubeconform -strict -ignore-missing-schemas -summary \
    -schema-location default \
    -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
    /tmp/built.yaml; then
    fail=1
  fi
  echo "::endgroup::"
done < <(find manifests/clusters -name kustomization.yaml -type f | sort)
exit $fail
