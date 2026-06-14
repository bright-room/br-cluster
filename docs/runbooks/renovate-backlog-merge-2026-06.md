# Runbook: Renovate バックログ一括解消 (2026-06)

溜まった Renovate PR (起票時点 38 件) を **1 PR ずつ確認しながら** 最新化する
**一時的な作業計画**。常設の Renovate 運用ガイドは
[`renovate.md`](renovate.md) を参照。

> **前提**: 本リポジトリは Flux GitOps。**`main` へのマージ = 実クラスタへ即デプロイ**。
> このため Flux HelmRelease の更新は「マージ前に変更内容と values 影響を精査し、
> マージ後にクラスタ健全性を検証する」サイクルで 1 件ずつ進める。

## 方針 (2026-06-14 合意)

- **1 リソース (1 PR) ずつ** 処理する。バッチマージはしない。
- minor 以上が多く **values / 設定変更が要る可能性** があるため、各件で upstream の
  changelog / release notes を確認し、values への影響を精査する。
- **低リスク → 高リスク** の順で進める (順序は下記)。
- 各件のマージ後、Flux デプロイ対象は **kubectl / flux で健全性を検証** してから次へ。
- 異常時は即停止して調査。

### values 修正が必要になった場合の進め方

**Renovate ブランチには触らない** (Renovate が `rebaseWhen: behind-base-branch` で
勝手に rebase し競合する恐れがあるため)。

1. `main` から新しいブランチを切る
2. **バージョンアップ + values 修正をまとめて** そのブランチで実施
3. CI green を確認してマージ
4. 元の Renovate PR は **クローズ** (依存はマージ済みなので次回 run で消える)

values 変更が **不要** な PR は、Renovate PR をそのままマージしてよい。

## 各 PR の処理サイクル

各 PR で以下を 1 サイクル回す:

1. **差分確認** — `gh pr view <n> --json files` / `gh pr diff <n>` で対象バージョン・ファイルを見る
2. **changelog 確認** — 現行→新バージョン間の breaking change、**values スキーマ変更**
   (必須値の追加・削除・リネーム、デフォルト変更) を upstream で確認
3. **影響照合** — 該当 `helm.yaml` / values / ansible 変数が影響を受けるか自リポジトリと照合
4. **必要なら設定修正** — 影響あれば上記「values 修正が必要になった場合」の手順で対応
5. **CI 確認** — `gh pr checks <n>` が全 pass
6. **マージ** — `gh pr merge <n> --squash` (リポジトリの既定方式に合わせる)
7. **クラスタ検証** (Flux デプロイ対象のみ):
   - `flux get hr -A` で対象 HelmRelease が `Ready=True`
   - `kubectl get pods -n <ns>` で対象 Pod が Running/Ready
   - 必要なら `kubectl logs` / イベント確認
8. 問題なければ次へ。異常なら停止。

> クラスタアクセスは WARP 接続 + kubeconfig が前提
> ([`cloudflare-tunnel-warp-access.md`](cloudflare-tunnel-warp-access.md))。

## 処理順序

### Phase 0 — 重複 PR のクローズ

新しい方を残し、古い方をクローズ (上書き関係):

| クローズ | 残す (上位) |
|---|---|
| #291 k3s 1.35.5 | #313 k3s 1.36.1 |
| #292 external-secrets 2.4.1 | #323 2.5.0 |
| #309 longhorn 1.11.2 | #333 1.12.0 |
| #312 zitadel 9.34.1 | #331 v10 |
| #317 envoy gateway-helm 1.7.4 | #322 1.8.1 |
| #325 community.general 12.6.1 | #330 v13 |

> #306 / #328 はどちらも github-actions だが触るワークフローファイルが**別**なので
> 両方マージ (重複ではない)。

### Phase 1 — クラスタ無影響 (values 確認ほぼ不要・サッと)

CI のみが gate。Flux デプロイ対象外。

| 順 | PR | 内容 |
|---|---|---|
| 1 | #305 | mise tools (patch) |
| 2 | #315 | mise tools (minor) |
| 3 | #328 | github-actions (CI workflow 群) |
| 4 | #306 | github-actions v2.3.8 (security-scan) |
| 5 | #282 | lock file maintenance (uv.lock) |
| 6 | #302 | debian:bookworm-slim digest |
| 7 | #303 | python:3.14.4 digest |
| 8 | #326 | python docker tag v3.14.5 |

### Phase 2 — ansible / provisioner (Flux デプロイ外、次回プロビジョニング時のみ影響)

`provisioner/**` のみ変更。走行中クラスタには即時影響しない。

| 順 | PR | 内容 |
|---|---|---|
| 1 | #301 | etcd-io/etcd v3.6.12 |
| 2 | #304 | grafana/alloy v1.16.2 (version pin) |
| 3 | #314 | onepassword.connect v2.4.0 |
| 4 | #320 | certbot v5.6.0 |
| 5 | #327 | ansible.posix v2.2.0 |

### Phase 3 — Flux HelmRelease (minor/patch、values 精査を丁寧に・依存順)

各件マージ後に `flux get hr -A` + 対象 Pod を検証。

| 順 | PR | 内容 | グループ |
|---|---|---|---|
| 1 | #332 | coredns v1.46.0 | コア基盤 |
| 2 | #308 | kube-vip v0.9.9 | コア基盤 |
| 3 | #333 | longhorn v1.12.0 | ストレージ |
| 4 | #310 | snapshot-controller v5.0.4 | ストレージ |
| 5 | #323 | external-secrets v2.5.0 | シークレット |
| 6 | #319 | cloudnative-pg v0.28.2 | DB |
| 7 | #297 | tempo v2.2.0 | 可観測性 |
| 8 | #307 | alloy v1.8.2 | 可観測性 |
| 9 | #324 | opentelemetry-collector v0.158.1 | 可観測性 |
| 10 | #318 | argo-workflows v1.0.14 | アプリ/運用 |
| 11 | #329 | kured v5.12.0 | アプリ/運用 |
| 12 | #311 | tofu-controller v0.16.3 | アプリ/運用 |
| 13 | #322 | envoy gateway-helm v1.8.1 | アプリ/運用 |

### Phase 4 — major 級 (特に丁寧に 1 件ずつ)

changelog の breaking change を精読してから対応。

| 順 | PR | 内容 | 注意点 |
|---|---|---|---|
| 1 | #321 | helm/helm v4 (ansible) | Helm 4 は大型。provisioner の helm 利用箇所への影響確認 |
| 2 | #330 | community.general v13 (ansible) | 使用 module の deprecation/削除確認 |
| 3 | #298 | loki v7 (Flux) | values スキーマ・storage 設定の breaking change 確認 |
| 4 | #331 | zitadel v10 (Flux) | 進行中の RBAC 作業との整合を確認。下記参照 |

> **zitadel v10**: 進行中の multi-org RBAC 移行
> ([`docs/proposals/zitadel-multi-org-rbac-plan-3-br-cluster-enforcement.md`](../proposals/zitadel-multi-org-rbac-plan-3-br-cluster-enforcement.md))
> と整合するか確認。OIDC sub 周りの挙動変化にも注意
> (ユーザ再作成で sub が変わる既知の落とし穴あり)。

### Phase 5 — k3s ノードアップグレード #313 (最後・単独)

**専用 runbook [`k3s-upgrade.md`](k3s-upgrade.md) に従う**。本計画では重複させない。

- 1.35 → 1.36 は **minor 跨ぎ** → k3s-upgrade.md の「minor upgrade チェックリスト」を必ず通す。
- 現状 SUC の `Plan` は **`br-node5` (テスト worker) 1 台限定** (`nodeSelector`、Phase 1a)。
  control-plane / 他 worker への展開は別 proposal の決着後。
- マージ後、SUC が対象ノードを drain/reboot。`kubectl get nodes` で対象ノードが
  新バージョンで Ready になることを確認。

## 検証コマンド早見

```sh
# Flux 全体の HelmRelease 状態
flux get hr -A

# 特定 namespace の Pod
kubectl get pods -n <ns>

# ノードバージョン
kubectl get nodes -o wide

# Renovate Open PR 一覧 (進捗確認)
gh pr list --author "app/renovate" --json number,title,createdAt \
  --jq 'sort_by(.createdAt)[] | "\(.number)\t\(.title)"'
```

## 関連

- [`renovate.md`](renovate.md) — Renovate 設定の常設運用ガイド
- [`k3s-upgrade.md`](k3s-upgrade.md) — k3s アップグレード専用 runbook (Phase 5)
- [`cloudflare-tunnel-warp-access.md`](cloudflare-tunnel-warp-access.md) — クラスタアクセス前提
