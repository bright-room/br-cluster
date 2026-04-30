# 提案: manifests CI の段階強化 (CRD schema / postBuild Secret / runtime smoke)

> **この提案の位置づけ**
>
> argo-workflows Phase 1 の段階導入 (PR #241〜) で **静的検証では捕まえ切れず
> 何度も merge 後に Flux reconcile で死んだ** 事例が連続したため、CI で
> 防げる層と防げない層を仕分け、防げる層は機械化、防げない層は最小コストの
> runtime smoke test で受け止める方針を立てる。

## 背景・動機

argo-workflows Phase 1 の段階導入で 6 件の hot-fix PR が連発した:

| PR | 内容 | 静的解析で防げたか |
|---|---|---|
| #245 | `server.sso.enabled` 未指定で chart が sso block を出さず argo-server crash | **不可** (chart が値を黙って捨てる、空 sso 出力でも YAML valid) |
| #247 | `CronWorkflow.spec.schedule` (v3 syntax) → v4 で `schedules` 配列に変わっていた | **可能** (CRD schema 検証) |
| #249 | Workflow Pod の SA + RBAC 欠落で `workflowtaskresults` create 失敗 | **不可** (chart default が SA を作らない、Workflow 投入で発覚) |
| #250 | EventBus `metricsExporterImage` 欠落で StatefulSet `containers[2].image: Required` | **不可** (chart は受理、controller 側生成で発覚) |
| #251 | Discord substituteFrom Secret が同 Kustomization 内で chicken-and-egg | **可能** (postBuild Secret 解決) |
| #252 / #253 | substituteFrom は `flux-system` ns でしか Secret を引かない | 同上 |

**6 件中 2 件 (約 1/3) は静的解析で防げた**。残り 4 件は chart の値マージ後の挙動 / controller の reconcile ロジック依存で、manifest だけ眺めても発覚しない種類。

CI 強化を放置すると同じパターンの hot-fix が今後も発生する一方、すべてを静的解析で守るのは原理的に無理。**「防げる層は安いコストで全部塞ぐ」「防げない層は単発 runtime smoke で受ける」の二段構え** が必要。

## ゴール / 非ゴール

| | 内容 |
|---|------|
| ゴール | (1) CRD schema 検証を kubeconform に組み込み、`apiVersion / kind / spec.*` レベルの schema 違反を PR で弾く。(2) flux-local の postBuild substituteFrom Secret 解決を有効化、不在の Secret 参照を PR で弾く。(3) ephemeral cluster (kind) で **最小限の smoke test** を回し、Helm chart / controller の値マージ後の挙動を 1 周検証する |
| 非ゴール | (1) 全 OSS の bug を CI で防ぐ (chart 側のバグは upstream で直す)。(2) production 相当の負荷試験。(3) PR ごとの full e2e (cost と時間が見合わない、ナイトリーで十分) |

## 採用 / 不採用 / 理由

| 論点 | 採用 | 理由 |
|------|------|------|
| CRD schema | **kubeconform に CRD schemas を食わせる** | flux-local 内部でも kubeconform 呼ぶが、CRD schemas を渡さないと CRD 由来 (Workflow / Sensor / EventSource / HelmRelease etc.) は素通し。`datreeio/CRDs-catalog` などのリポジトリから .json を取得し `--schema-location` に追加する |
| postBuild Secret 解決 | **flux-local に Secret stub を food でなく実探索させる** | `--no-skip-secrets` + クラスタからダンプした Secret スキーマを stub する。または `--api-versions` を渡して flux Kustomization の `substituteFrom` 解決ステップで「該当 Secret/ConfigMap が flux-system ns 内に手動で定義された Kustomize リソースで作られているか」を厳密にチェック。**最低限**: substituteFrom が参照する Secret 名 / ns が manifest 内のどこかで作られているかをカスタムチェックスクリプトで確認 |
| Helm 値レベルの validation | **不採用 (Phase 1)** | `argo-workflows server.sso.enabled` のような chart 仕様依存の検証は無限に膨らむ。代わりに smoke test で受ける |
| runtime smoke test | **kind cluster で argo-workflows / argo-events を install して 1 Workflow 流す** | Workflow Pod の SA / RBAC、EventBus の StatefulSet 構築、Sensor の SSO config 読み込みなど、controller の reconcile が回って初めて発覚する層を catch。コストは 1 PR あたり 5-10 分程度、cost 妥当 |
| smoke test の実行頻度 | **PR ごと (manifest 変更時のみ)、5 分以内に終わる範囲** | 全 platform 起動は重すぎる。argo-workflows / argo-events に絞った最小 install + 1 sample workflow 実行のみ |
| ephemeral cluster | **kind (Kubernetes-IN-Docker)** | k3d も候補だが kind の方が GitHub Actions での実績多。Pi 環境特有の挙動 (arm64 / Longhorn 等) はカバーしない割り切り — chart の値マージ層で死んでないか だけが目的 |
| 段階導入 | **Phase 1: CRD schema + postBuild → Phase 2: smoke test** | Phase 1 だけで 6 件中 2 件防げる。Phase 2 (smoke test) は時間 / 複雑度が一段上がるので別 proposal で詳細化 |

### 検討したが採らなかった案

| 案 | 不採用理由 |
|---|-----------|
| 全 platform を kind に展開して e2e | 起動だけで 30 分以上、PR ごとには重すぎる。ナイトリーでも維持コストが見合わない |
| Conftest (Rego) で chart values を検査 | `server.sso.enabled` のような値は ConfigMapGenerator で生成された後の helm 出力を見ないと判定できず、Conftest の対象外。Rego は文法的検証に向いていて、semantic 検証には不向き |
| CRD schema を kubeconform 既定の `kubernetes-json-schema` で代替 | 既定セットには argo-workflows / argo-events / Flux / cert-manager の CRD が無い。各 chart リポから抽出する必要があり、結局カスタム手当が要る |
| flux-local を捨てて純 kustomize + helm template | flux-local は HelmRelease の inflate と Kustomize の `dependsOn` 順序を理解する。これを自前で再実装するのは現実的でない |

## Phase 1 で実装するもの

### (A) CRD schema を kubeconform に渡す

#### 配置

```text
scripts/
└── fetch-crd-schemas.sh         # CRD .yaml → .json schema を生成
.generated/crds/                 # gitignore 済 (生成物)
└── *.json
```

#### 取得元 (例)

| chart / source | CRD 群 | 備考 |
|---|---|---|
| argoproj/argo-workflows | Workflow / WorkflowTemplate / CronWorkflow / WorkflowTaskResult | Phase 1 で頻出 |
| argoproj/argo-events | EventBus / EventSource / Sensor | 同上 |
| flux-system | HelmRelease / Kustomization / GitRepository / HelmRepository | reconcile 経路全般 |
| cert-manager | Certificate / Issuer / ClusterIssuer | 既に運用中 |
| longhorn | Volume / Backup 等 | 必要に応じ |
| cnpg | Cluster / Database / ScheduledBackup | 既に運用中 |

`fetch-crd-schemas.sh` は git ref 固定で CRD YAML をダウンロード → `kubeconform/openapi2jsonschema.py` 等で .json に変換 → `.generated/crds/` に出す。Renovate で chart version を bump したら同時に CRD schema もリフレッシュ (renovate.json にルール追加)。

#### kubeconform 呼び出し

`scripts/manifests-build.sh` で:

```bash
kubeconform \
  -strict -ignore-missing-schemas=false \
  -schema-location default \
  -schema-location '.generated/crds/{{ .ResourceKind }}-{{ .ResourceAPIVersion }}.json' \
  -summary -verbose \
  build_output.yaml
```

`-ignore-missing-schemas=false` で **CRD schema が無いリソースは fail** にする。今 `make manifests/build` が「Valid: N」を出してるが内部的に CRD は素通しになっているので、ここを締める。

期待される効果: #247 (`CronWorkflow.spec.schedule` → `schedules`) のような **CRD schema 違反が PR レベルで死ぬ**。

### (B) postBuild substituteFrom Secret の存在チェック

flux-local 単体では `flux-system` ns 内の Secret 存在まで踏み込まない。**カスタムチェックスクリプト** を `scripts/manifests-substitute-check.sh` として追加:

```text
スクリプトロジック (擬似コード):
  1. 全 Flux Kustomization manifest を YAML parse
  2. spec.postBuild.substituteFrom[*] を抽出
  3. 各 entry が { kind: Secret|ConfigMap, name: <X> } を参照
  4. リポジトリ全体で kind: <X> name: <Y> namespace: flux-system が
     ExternalSecret の target.name = X か、ConfigMap (cluster-settings 等)
     として作成されているか確認
  5. なければ fail
```

期待される効果: #251 / #252 / #253 のような「postBuild が flux-system ns で Secret を見つけられない」をすべて事前検出。

### (C) `make manifests/check` を CI 必須に追加

`make check` は既に `manifests/build` + `manifests/flux-local` + `policy/test` を呼んでいるので、(A) (B) はそこに乗せる。CI 側 `manifests-ci.yaml` の `kustomize-build` job を `manifests-build` に rename して同じ内容を実行。

## Phase 2 で実装するもの (別 proposal で詳細化)

### runtime smoke test (kind cluster)

GitHub Actions で:

1. `kindest/node:v1.34` を起動
2. flux2 install
3. `manifests/clusters/prod` を `flux-local` で render → `kubectl apply --server-side`
4. `argo submit --from cronworkflow/hello --watch --serviceaccount argo-workflow` (Phase 1 の sample を使う)
5. Succeeded で exit、Error / Timeout で fail

PR ごとに 5-10 分追加。Renovate での chart bump も含めて全部この門を通す。

期待される効果:
- #245 (`server.sso.enabled` 抜けで argo-server CrashLoopBackOff) → kind 上で CrashLoopBackOff → Action fail
- #249 (Workflow SA / RBAC) → kind 上で Workflow がそのまま死ぬ → Action fail
- #250 (EventBus metricsExporterImage) → kind 上で EventBus NotReady → Action fail

**Phase 2 の懸案**:
- kind の docker network と CF Tunnel の整合 (CF Access が前段にいる前提のリソースをどう mock するか)
- Garage S3 (br-external1) を kind から到達不能にして default の dummy S3 を使う等の override
- 1Password Connect が居ないので ExternalSecret は dummy CRD で stub する
- これらの mock 機構を含めると複雑度が上がるので、別 proposal で運用設計と合わせて議論する

## 段階導入計画

| Phase | 内容 | 完了条件 |
|-------|------|---------|
| **Phase 0** | この proposal で合意 | レビュー approval |
| **Phase 1** | CRD schema + postBuild Secret check を CI に追加 | (A) (B) (C) を main に merge、過去の hot-fix PR を **意図的に revert して再 PR したら CI で死ぬ** ことを確認 |
| **Phase 2** | kind smoke test 別 proposal で詳細化 | 別 proposal 起票 |
| **Phase 3** | smoke test を Renovate PR にも適用 | (Phase 2 後に判断) |

Phase 2 以降は本 proposal のスコープ外。Phase 1 が安定してから別 proposal を起こす。

## 期待効果

- **同じ系統の hot-fix PR が連発しなくなる** — 静的解析で防げる層は機械的に弾く
- **Renovate の chart bump で CRD schema が変わった時に検出できる** — argo-workflows v3 → v4 のような major upgrade で `spec.schedule` が消えた、を CI で検出
- **argo-workflows Phase 1 の段階導入で得た学びを CI 仕様化** して、今後同種の platform 追加時 (Tekton / Knative 等) で同じ罠を踏まない

## リスク・注意

| リスク | 対処 |
|--------|------|
| **CRD schema 取得の維持コスト** | Renovate で chart 同時更新、`fetch-crd-schemas.sh` は単純化して保守軽量に |
| **kubeconform strict 化で既存違反が大量発覚** | Phase 1 着手時に dry-run、既存違反は別 PR で個別修正 (このスコープ外) |
| **postBuild Secret check の偽陰性** | 検出ロジックを単純な YAML grep + cross-ref に留め、edge case は明示的に doc 化 |
| **Phase 2 の kind smoke test が遅い / flaky** | Phase 2 で別 proposal にして実証実験 → 採否判断 |
| **CRD バージョンの drift** | mise / renovate.json に CRD schema fetch を組み込み、chart 更新と同期 |

## 作業範囲 (Phase 1)

- `scripts/fetch-crd-schemas.sh` 新規作成
- `.generated/crds/` を `.gitignore` に追加 (既に `.generated/` 配下は ignore 済の可能性、確認)
- `scripts/manifests-build.sh` に kubeconform 呼び出し追加
- `scripts/manifests-substitute-check.sh` 新規作成
- `Makefile` に `manifests/substitute-check` ターゲット追加、`check` に組み込み
- `.github/workflows/manifests-ci.yaml` に新ジョブ追加 (or 既存 `kustomize-build` job 内に組込)
- `renovate.json` に CRD schema fetch ルール (chart version と同期) 追加
- 既存違反があれば個別 PR で修正 (このスコープ外)

## 未決事項 / 要確認

- CRD schema 取得元の正規化 (chart リポから直接取るか、`datreeio/CRDs-catalog` を使うか)
- substituteFrom check の実装言語 (bash + yq か、Python か)
- Phase 2 の kind 上で Garage / 1Password / Zitadel をどう mock するかの方針 (Phase 2 proposal で詳細)
- argo-workflows の WorkflowTaskResult のような **動的に生成される CRD インスタンス** をどこまで schema check するか

## 更新履歴

- 2026-04-30 初版
