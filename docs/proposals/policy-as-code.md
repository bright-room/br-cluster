# 提案: Policy as Code (Conftest → Gatekeeper) の段階導入

> **この提案の位置づけ**
>
> AI / 人間問わず、`manifests/` への変更が CLAUDE.md の暗黙ルールから外れた
> ときに **CI で機械的に弾く** ためのガードレールを敷く。Pi クラスタ負荷を
> 増やさないため、まずは Conftest (CI 専用) から始め、policy が安定したら
> Gatekeeper (admission) に昇格させる段階導入とする。

## 背景・動機

CLAUDE.md と各 docs に「暗黙の規約」が積み上がっているが、現状はレビュー時
に人間が気付くかどうかに依存している:

- LB Service の IP は **annotation で固定** ([`docs/network.md`](../network.md))
- `*.b8m.app` の HTTPRoute は **Zitadel OIDC SecurityPolicy が必須**
  ([`docs/platform/identity.md`](../platform/identity.md))
- Secret は **1Password 経由 (ExternalSecret)**、平文 `kind: Secret` 禁止
- PVC の `storageClassName` は **Longhorn 前提**
  ([`docs/platform/storage.md`](../platform/storage.md))
- Pi リソース制約下で `resources.requests/limits` 未指定は OOM 連鎖リスク

これらは Claude Code (AI) が新しい manifest を提案する場面でも踏みやすい
地雷で、レビュー疲弊の主因になりつつある。**規約を実行可能な形に落として
PR で機械的に止める** ことで、レビュー負荷と運用事故の両方を減らす。

## ゴール / 非ゴール

| | 内容 |
|---|------|
| ゴール | (1) `manifests/` 配下の規約違反を CI で検出する。(2) 将来 Gatekeeper に同じ policy を移植できる構造にしておく。(3) 既存 manifest が違反していた場合は exception or 修正で全 green にする |
| 非ゴール | (1) 全規約の網羅。最小 5 本から始める。(2) GitOps 経由縛り (kubectl 直接 apply 禁止) は Phase 4 以降。break-glass 設計とセットでないと自分が詰む |

## 採用 / 不採用 / 理由

| 論点 | 採用 | 理由 |
|------|------|------|
| ツール選定 | **Conftest (Phase 1) → Gatekeeper (Phase 2)** | Conftest は CI のみで Pi 負荷ゼロ。Gatekeeper は常駐 Pod (controller + audit) で Pi に負担。policy が安定する前に常駐させるのは早い |
| Rego 配置 | `policies/` を新設 (リポジトリ root) | Conftest と Gatekeeper ConstraintTemplate の両方から参照可能にする。`manifests/` 配下に置くと Flux が apply を試みる |
| 最初の policy セット | 下記「最初の 5 本」 | 違反時の影響が大きい / 既に過去に踏んだ / AI が踏みやすい、を基準に最小選定 |
| GitOps 経由縛り | Phase 4 で `enforcementAction: warn` から | 障害時に kubectl で緊急修正できなくなる。break-glass admin の allowlist と監査ログ整備が前提 |
| CI workflow | 既存 `.github/workflows/ci.yaml` に job 追加 | 新規ファイル作らず、ruff / packer と並列で実行 |
| violation 既存 manifest | exception ではなく修正を優先 | exception を増やすと policy が形骸化する。修正コストが大きいものだけ exception |

## このリポの実態 — Phase 1 で何が検査できるか

事前調査の結果、`manifests/` 配下の workload は **ほぼ全て HelmRelease 経由**:

- raw `Deployment` / `StatefulSet` / `DaemonSet` / `CronJob` / `Job`: **2 ファイル**
  (`hubble-flow-exporter`, `cloudflared`)
- `HelmRelease`: **26 ファイル**

Conftest は **静的検査** で、HelmRelease を見ても render 後の Pod spec
は見えない。つまり「`:latest` タグ禁止」「`resources` 必須」のような
**Pod spec ベースの policy は Phase 1 では事実上カバーできない**
(2 ファイルにしか効かない)。

選択肢は 2 つ:

| 選択肢 | コスト | 効果 |
|-------|-------|------|
| (a) CI で `helm template` を回して render 結果を Conftest に食わせる | 高 (values 解決、ExternalSecret stub、各 chart 固有の設定) | Phase 1 で Pod spec policy が効く |
| (b) Phase 1 は **raw manifest で意味のある policy** に絞り、Pod spec policy は Phase 2 (Gatekeeper) に持ち越す | 低 | Phase 1 は確実に green、Pod spec policy は admission で本番リソースに対して効かせる |

**(b) を採用**。理由:

- Phase 2 で Gatekeeper が入れば、HelmRelease が render した実際の Pod に
  対して admission が効くので、(a) の helm template render は二度手間になる
- (a) は ExternalSecret や 1Password Connect の不在を chart 側に通知するの
  が現実的に難しい (CI で render が落ちる chart が出る)

## 最初の 4 本 policy (Phase 1)

raw manifest (HelmRelease, Service, Secret 等) で意味のあるものに絞る。
Pod spec ベースの policy は Phase 2 に回す。

| # | policy | 検査対象 | 違反時 deny メッセージ例 |
|---|--------|---------|------------------------|
| 1 | `HelmRelease` の `chart.spec.version` 必須 (floating 禁止) | `kind: HelmRelease` | `HelmRelease 'foo' must pin chart.spec.version (no floating versions)` |
| 2 | `HelmRelease` の `sourceRef` は allowlist の `HelmRepository` のみ | `kind: HelmRelease` | `HelmRelease 'foo' references unlisted HelmRepository 'bar'` |
| 3 | 平文 `kind: Secret` で `data` / `stringData` 直書き禁止 | `kind: Secret` (ただし `type: kubernetes.io/tls` 等は除外) | `Secret 'foo' must be provisioned via ExternalSecret (1Password)` |
| 4 | LoadBalancer Service は `lb-ipam.cilium.io/ips` annotation 必須 | `kind: Service`, `spec.type: LoadBalancer` | `LoadBalancer Service 'foo' must pin IP via lb-ipam.cilium.io/ips annotation` |

### 検討したが Phase 1 から外したもの

- **`*.b8m.app` HTTPRoute に SecurityPolicy 必須**: 一時的に認証なし
  公開したいケースがあるため除外。将来的に annotation ベースの opt-out
  (例: `policy.b8m.app/skip-auth: "<reason>"`) を設計してから再検討

### Phase 2 に回す policy (Gatekeeper 投入時に追加)

| policy | 理由 |
|--------|------|
| container image に `:latest` タグ禁止 | Pod spec ベース。HelmRelease render 後にしか見えない |
| `resources.requests/limits.memory` 必須 | 同上 |
| `securityContext.runAsNonRoot` | 同上 |
| `hostNetwork` / `privileged` の allowlist | 同上 |

### 既存 manifest の事前スキャン

policy 1〜4 については現時点で明確な違反は見当たらず、修正なしで通る見込み
(詳細は Phase 1 実装時に `conftest test` を流して再確認)。

## 段階導入計画

| Phase | 内容 | 完了条件 |
|-------|------|---------|
| **Phase 0** | この proposal で合意 | レビュー approval |
| **Phase 1** | Conftest + 最小 5 policy + CI 組み込み + 既存違反対応 | `ci.yaml` の `policy-test` job が main で green |
| **Phase 2** | Gatekeeper を `manifests/platform/gatekeeper-app.yaml` で導入。Phase 1 の policy を ConstraintTemplate 化、`enforcementAction: warn` で投入 | Gatekeeper Pod が Healthy、audit ログに想定外 violation が出ないことを 2 週間観察 |
| **Phase 3** | warn → deny 昇格。Phase 1 の Conftest と二重チェック体制に | deny 化後 1 週間で誤検知ゼロ |
| **Phase 4** | GitOps 経由縛り (Deployment/Pod/Job の create/update を Flux SA に限定)。break-glass 用 admin の allowlist + 監査 | 別 proposal を切る |

Phase 4 は本 proposal のスコープ外。Phase 3 まで進んだ時点で別途
`docs/proposals/gitops-only-enforcement.md` を起こす。

## Phase 1 の運用フォロー (2026-05-10 目安)

Phase 1 を merge してから 2 週間後に運用実績を振り返り、Phase 2 着手要否を
判断する。**`/schedule` で remote agent を仕込むことも可能** だが、初回は
手動で振り返る方が判断材料を直接見られて良いので、ここではタスクとして
記録するに留める。

### 振り返り項目

| 項目 | 確認方法 |
|------|---------|
| policy-test job が CI で fire した PR 数  | `gh pr list --state all --search "label:policy" --limit 50` または `gh run list --workflow ci.yaml --json conclusion,event` から `policy-test` step の fail 履歴 |
| `policies/exceptions.rego` への追加件数   | `git log --since=2026-04-26 -- policies/exceptions.rego` |
| 偽陽性 (本来通るべき manifest が deny された) の件数 | exception 追加 PR のコミットメッセージから読む |
| 真の違反捕捉 (規約違反を機械的に弾けた) の件数      | 同上 |

### Phase 2 着手判断の閾値 (目安)

| 状況 | 判断 |
|------|------|
| 偽陽性ゼロ + 真の違反捕捉 1 件以上 | Phase 2 (Gatekeeper) proposal 着手 |
| 偽陽性が複数発生 / policy 設計に問題が見えた | Phase 1 の policy を見直し、Phase 2 は延期 |
| 該当 PR ゼロ (CI が一度も発火していない)    | 観察期間延長。さらに 2 週間後に再判断 |

### 備考

- もし手動振り返りの忘却リスクがあるなら、`/schedule` で 2026-05-10 に
  GitHub Issue を起票する agent を仕込む選択肢もある (本 proposal merge 時点
  では未実施)
- 振り返り結果は本 proposal の「更新履歴」に追記する形で残す

## 構成要素 (Phase 1)

### (A) ディレクトリ構造

```text
policies/
├── README.md                       # policy 一覧と Rego の書き方
├── kubernetes/
│   ├── helmrelease_version_pinned.rego
│   ├── helmrelease_version_pinned_test.rego
│   ├── helmrelease_repo_allowlist.rego
│   ├── helmrelease_repo_allowlist_test.rego
│   ├── secret_no_plaintext.rego
│   ├── secret_no_plaintext_test.rego
│   ├── lb_service_pinned.rego
│   └── lb_service_pinned_test.rego
└── exceptions.rego                 # exception 一元管理
```

### (B) `policies/exceptions.rego` 設計

policy ごとに散らさず、1 ファイルで exception を一覧管理する:

```rego
package exceptions

# policy 2 (HelmRelease は allowlisted な HelmRepository のみ) の許可リスト
helmrelease_repo_allowlist := {
  "flux-system/<repo-name>",   # 実際の HelmRepository 名は Phase 1 着手時に列挙
}
```

各 policy の Rego から `data.exceptions.*` を参照して allow する。
**例外を追加する PR では理由をコミットメッセージに含める** ことを
ルール化 (CLAUDE.md に追記)。

### (C) CI 組み込み

`instrumenta/conftest-action` は archive 済みなので、`mise` 経由で conftest
を直接インストールする方針 (CI とローカルで同一バージョン保証):

`.github/workflows/ci.yaml` に job 追加:

```yaml
policy-test:
  name: Policy Test
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v6
    - uses: jdx/mise-action@v2
      with:
        install: true
        cache: true
    - run: conftest verify --policy policies/
    - run: conftest test --combine --policy policies/ manifests/platform/
```

注意:

- 対象を `manifests/platform/` に絞る (`manifests/clusters/` の Flux
  Kustomization は別の policy 体系になるので Phase 1 では対象外)
- Phase 1 の 4 policy は cross-resource 検査を必要としないが、`--combine`
  を有効にしておけば将来 cross-check policy を追加するときに変更不要

### (D) ローカル実行

`Makefile` に追加:

```make
policy/test:
 conftest verify --policy policies/
 conftest test --combine --policy policies/ manifests/

policy/install:
 # mise.toml に conftest を追加してバージョン管理
 mise install
```

`mise.toml` に conftest を追加して、CI とローカルでバージョンを揃える。

## 期待効果

- **AI が暗黙ルールを破った PR が CI で止まる** — レビュー前にフィードバック
  ループが回る
- **規約のドキュメント化** — Rego が "実行可能な docs" として CLAUDE.md と
  二重管理になるが、Rego が SoT、CLAUDE.md は概要、と整理する
- **Phase 2 Gatekeeper 投入時のリスクが下がる** — Conftest で違反パターンを
  洗い出してから admission に上げるので、いきなり deny で詰まる事故を回避

## リスク・注意

| リスク | 対処 |
|--------|------|
| **policy が形骸化** (例外だらけになる) | exception 追加 PR は理由必須 + 定期棚卸し (半年ごと proposal 起票) |
| **Conftest の `--combine` が遅い / 落ちる** | 対象を `manifests/platform/` に限定。HelmRelease values は除外 |
| **既存違反が大量に出て Phase 1 着手が止まる** | 修正コスト見積もりを Phase 1 着手時にやり、修正不可分は exception で逃がす |
| **Rego の学習コスト** | `policies/README.md` に最低限のチートシート + 既存 policy をテンプレに |
| **conftest バージョン drift (CI と local)** | `mise.toml` で固定 |
| **HelmRelease 経由で生成される Pod は Conftest で見えない** | Conftest は静的検査の限界。これは Phase 2 Gatekeeper に持ち越す。Phase 1 では受容 |

## 作業範囲 (Phase 1)

- `policies/` ディレクトリ新規作成 (上記 (A))
- `policies/README.md` (Rego の書き方ガイド + policy 一覧表)
- 4 本の `.rego` + 各 `_test.rego`
- `policies/exceptions.rego`
- `.github/workflows/ci.yaml` に `policy-test` job 追加
- `Makefile` に `policy/test` ターゲット追加
- `mise.toml` に conftest 追加
- `CLAUDE.md` に「policy 違反時の対応」「exception 追加ルール」セクション追加
- **ドキュメント (実装後にまとめて作る)**:
  - `docs/platform/policy.md` (新規 — Policy as Code グループの解説、設計判断、Rego 開発フロー)
  - `docs/assets/drawio/policy.drawio` + `docs/assets/policy.svg`
    (drawio source + SVG export。CI フロー図、6 アイコン: OPA / GitHub / GitHub Actions / Flux / Helm / Kubernetes)
  - `docs/README.md` の「プラットフォームコンポーネント」表に Policy as Code 行追加
  - 実装 → 動作確認後に書く方が手戻りが少ない (ディレクトリ構造や CI 詳細が
    実装中に変わる可能性があるため)
  - SVG export は VS Code drawio extension で `policy.drawio` を開いて
    「Export As → SVG」で生成 (既存 drawio 群と同じ運用)

## 未決事項 / 要確認

- conftest の mise plugin 確認 (`mise plugins ls-remote | grep conftest`
  で aqua-registry 経由が使える想定。Phase 1 着手時に確認)
- 既存 manifest を `conftest test --combine` で流したときの実行時間
  (1 分以内に収まるか)
- `policies/exceptions.rego` の構造を package 分離するか単一ファイルにするか
  (Phase 1 では単一ファイルで開始、policy が増えたら分離検討)
- policy 2 の HelmRepository allowlist の初期リスト
  (`manifests/platform/**/helmrepository*.yaml` から自動抽出する想定)

## 更新履歴

- 2026-04-26 初版
- 2026-04-26 レビュー#2 反映:
  - policy 5 (`*.b8m.app` HTTPRoute は SecurityPolicy 必須) を Phase 1 から
    除外。一時的な認証なし公開ケースを許容するため、annotation ベースの
    opt-out 設計を待ってから再検討
  - Phase 1 を 5 本 → 4 本に縮小
- 2026-04-26 レビュー#1 反映:
  - 事前調査で raw Deployment 2 / HelmRelease 26 と判明。Phase 1 の
    policy 1 (latest 禁止) と 2 (resources 必須) は事実上 dead code に
    なるので外し、HelmRelease 自体に効く policy (chart version pinning,
    HelmRepository allowlist) に差し替え
  - Pod spec ベースの policy は Phase 2 (Gatekeeper, render 後の Pod に
    admission) に明示的に持ち越し
  - CI workflow は archived な `instrumenta/conftest-action` ではなく
    `jdx/mise-action` + 直接 conftest 実行に確定
