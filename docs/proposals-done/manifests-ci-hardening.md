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
| #247 | `CronWorkflow.spec.schedule` (v3 syntax) → v4 で `schedules` 配列に変わっていた | **不可 (実装後に判明)** — catalog 上の CRD schema は v3/v4 両対応で `schedule` も valid 扱い。runtime smoke test (Phase 2) で受ける |
| #249 | Workflow Pod の SA + RBAC 欠落で `workflowtaskresults` create 失敗 | **不可** (chart default が SA を作らない、Workflow 投入で発覚) |
| #250 | EventBus `metricsExporterImage` 欠落で StatefulSet `containers[2].image: Required` | **不可** (chart は受理、controller 側生成で発覚) |
| #251 | Discord substituteFrom Secret が同 Kustomization 内で chicken-and-egg | **可能** (postBuild Secret 解決) |
| #252 / #253 | substituteFrom は `flux-system` ns でしか Secret を引かない | 同上 |

当初は **6 件中 2 件 (#247, #251-253) は静的解析で防げる** と見立てたが、実装後に
#247 は catalog schema が緩く検出不可と判明。実際に Phase 1 で確実に防げるのは
**postBuild substituteFrom 系 (#252 / #253)** が中心。残りは chart の値マージ後の挙動
/ controller の reconcile ロジック依存で、manifest だけ眺めても発覚しない種類。

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

## Phase 1 で実装したもの (2026-05-01)

> 実装着手時に proposal の前提と現状の差分が見つかったため、軽量化した形で
> 着地。差分の詳細は本節末尾の「実装と当初案の差分」を参照。

### (A) CRD schema を kubeconform で strict 検証

`scripts/manifests-build.sh` は次の通り:

```bash
kubeconform \
  -strict \
  -summary \
  -skip CustomResourceDefinition \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  -
```

ポイント:

- **`-ignore-missing-schemas` は付けない** → catalog に schema が無い CRD は fail。
- **`-skip CustomResourceDefinition`** だけ例外。CRD 定義そのものは vendor の
  chart から来る前提で本リポでは新設しない。yannh の standalone-strict にも
  含まれていない (例: SUC `plans.upgrade.cattle.io`)。
- **build 範囲を `manifests/clusters/**` から `manifests/platform/**/overlays/prod`
  まで拡張**。CRD インスタンスはほとんど `platform/` 配下にあり、
  cluster wrapper だけ build しても schema 検証が空振りしていた。
- **Flux postBuild の `${VAR}`** は `scripts/manifests-postbuild-fixtures.env` を
  envsubst の allow-list として渡し、dummy 値で展開してから kubeconform に流す。
  Argo Workflow 内のシェル変数 (`${WORKFLOW_NAME}` 等) や Grafana テンプレ変数は
  allow-list 外なので素通し。

期待される効果: CRD schema 違反 (未知プロパティ、必須欠落、型不一致) が PR で fail。

ただし **catalog 上の schema が緩い** ケースは検出できない。例えば PR #247 の
`CronWorkflow.spec.schedule` は v3 で deprecated、v4 でも catalog schema に残っており
「文法的には valid」と判定される。runtime smoke test (Phase 2) で受ける必要がある。

### (B) postBuild substituteFrom Secret の存在チェック

`scripts/manifests-substitute-check.sh` (bash + yq) を追加:

1. リポ全体の `ConfigMap` / `Secret` / `ExternalSecret` を集めて
   `<NS>/<KIND>/<NAME>` の集合 (= providers) を作る。ExternalSecret は
   `target.name` (or `metadata.name`) を Secret として登録。
2. 全 Flux Kustomization の `spec.postBuild.substituteFrom[*]` を抽出し、
   `(Kustomization.metadata.namespace, sf.kind, sf.name)` が providers にあるか確認。
3. 無ければ `::error file=...` で fail。

期待される効果: #252 / #253 のような「Secret が想定 namespace (flux-system) に
存在しない」を事前検出。

**注意**: #251 (chicken-and-egg、Secret は同 Kustomization 内で作られるが
substitute 時点ではまだ存在しない) は静的解析の射程外。Secret manifest は
リポに存在しているため、`<NS>/<KIND>/<NAME>` cross-ref では検出できない。
順序問題は dependsOn の組み方や別 Kustomization への切り出しで対処する
必要があり、Phase 2 の runtime smoke test を待つ。

### (C) Makefile / CI に組込

- `Makefile`: `manifests/substitute-check` ターゲット追加、`check` に組込。
- `.github/workflows/manifests-ci.yaml`: 既存 `kustomize-build` job に
  `Check Flux postBuild substituteFrom references` ステップ追加。
- `mise.toml`: `yq` を pin して mise install 配下に揃える。

### 実装と当初案の差分

| 当初案 | 実装 | 理由 |
|---|---|---|
| `scripts/fetch-crd-schemas.sh` で CRD YAML を取得 → openapi2jsonschema で .json 変換 → `.generated/crds/` に置く | `datreeio/CRDs-catalog` の URL を `-schema-location` で直参照 | 既存の `manifests-build.sh` が既に catalog URL を引いていた。catalog の coverage は本リポの CRD で十分 (62 platform overlay 中、URL 参照だけで全 valid) |
| `.generated/crds/` を gitignore | (不要) | `.generated/` 全体が既に ignore 済 |
| Renovate に CRD schema fetch ルール | (不要) | catalog 直参照で同期対象が無い |
| build 範囲: `manifests/clusters/**` のみ | `manifests/clusters/**` + `manifests/platform/**/overlays/prod` | clusters 配下は Flux Kustomization wrapper のみで CRD インスタンスがほぼ無い。検査効果ゼロだった |
| Flux postBuild `${VAR}` の扱い | envsubst + allow-list fixture | 当初案では言及なし。strict 化で HTTPRoute hostname 等が落ちるため必要になった |

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
| **Phase 0** | この proposal で合意 | 完了 (initial review) |
| **Phase 1** | CRD schema + postBuild Secret check を CI に追加 | **完了 (2026-05-01)** — strict kubeconform + substitute-check を main に merge |
| **Phase 2** | kind smoke test 別 proposal で詳細化 | **保留** (下記参照) |
| **Phase 3** | smoke test を Renovate PR にも適用 | (Phase 2 後に判断) |

**Phase 2 以降は当面保留 (2026-05-01 判断)**。6 連発 hot-fix の主因 (argo-workflows
段階導入) は一回性のイベントで、平常運用での再発頻度は低い見立て。kind 上で
CF Tunnel / Garage / 1Password / Zitadel を mock する複雑度に対して、homelab で
Flux reconcile が 30 分待たされる程度の failure cost は割に合わない。同種の
hot-fix burst が再発したら proposal を起こし直す。

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

## 作業範囲 (Phase 1) ※完了

- `scripts/manifests-build.sh` を strict kubeconform + envsubst 化、build 範囲を platform overlay まで拡張
- `scripts/manifests-postbuild-fixtures.env` 新規作成 (envsubst allow-list)
- `scripts/manifests-substitute-check.sh` 新規作成 (bash + yq)
- `Makefile` に `manifests/substitute-check` ターゲット追加、`check` に組込
- `.github/workflows/manifests-ci.yaml` の `kustomize-build` job に substitute-check ステップ追加
- `mise.toml` に `yq` を追加

## 未決事項 (Phase 2 以降)

- Phase 2 の kind 上で Garage / 1Password / Zitadel をどう mock するかの方針
- argo-workflows の WorkflowTaskResult のような **動的に生成される CRD インスタンス** をどこまで schema check するか
- catalog 上で緩い CRD schema (CronWorkflow.spec.schedule 等) を runtime smoke test で受ける線引き

## 更新履歴

- 2026-04-30 初版
- 2026-05-01 Phase 1 実装完了。当初案の重い fetch 機構を URL 直参照に置換、build 範囲を platform overlay まで拡張、envsubst による postBuild 変数展開を追加
