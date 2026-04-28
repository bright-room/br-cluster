# CLAUDE.md

このリポジトリで作業する Claude (および他の AI エージェント) 向けの規約と注意事項。

## まず読む

- 全体像: [`docs/architecture.md`](docs/architecture.md)
- 該当領域の詳細 doc: [`docs/README.md`](docs/README.md) のインデックスから辿る
- **コードが SoT**。docs と manifests / playbook / コードが食い違ったら、コードを正として doc を更新する

## ディレクトリの責務

| パス | 責務 | 編集方針 |
|------|------|----------|
| `cli/cluster_forge/` | Packer / Ansible / 1Password を束ねる Python CLI ([`docs/cli.md`](docs/cli.md)) | コード変更可、`make test` を通す |
| `imager/`            | Packer (ARM in Docker) | `packer fmt -check` を通す |
| `provisioner/`       | Ansible (playbooks / roles / inventories) | `make {env}/provision/lint` を通す |
| `manifests/`         | Flux で適用される k8s YAML | 追加時は `clusters/prod/platform/kustomization.yaml` に登録 |
| `docker/`            | Compose 定義 + ssh keys 出力先 (`docker/ssh/` は build 時生成、commit しない) | `compose.yaml` 直接編集 |
| `scripts/`           | 補助シェルスクリプト | 追加時は `Makefile` から呼ぶか docs で言及 |
| `docs/`              | ドキュメント | コードと食い違ったらコード優先で更新 |
| `.secret/{env}/`     | 1Password credentials (`gitignore` 済) | 触らない、commit しない |
| `.generated/`        | Packer 出力 / cloud-init 生成物 (`gitignore` 済) | 触らない |

## 触らないもの

- `garage/setup.sh` (`br-external1` 上のローカル運用スクリプト、リポジトリには含めない)
- `.secret/` 配下 (1Password credentials)
- `.generated/` 配下 (中間生成物)
- 関連リポジトリ (このリポからは触らない):
  - `bright-room/br-cloudflare-terraform` — Cloudflare Tunnel / Access / DNS
  - `bright-room/br-cluster-zitadel-terraform` — Zitadel リソース
  - `*.cluster-internal.bright-room.net` の非 k3s インフラ全般

## 環境前提

- 学習目的の homelab。**PVC は ephemeral 扱い** (Longhorn のオフクラスタバックアップは 2026-04-13 commit `41f3782` で意図的に撤去)
- Pi のリソース制約があるため、複数の OSS を並走させない設計選択が多い (例: kube-proxy → Cilium、Helm Controller → Flux)
- ネットワーク系の説明は **平易に噛み砕く** (運用者はネットワーク非専門)

## ツール

- `mise` で Python / `uv` / packer 等のバージョンを揃える (`mise install`)
- Python プロジェクトは `cli/` 配下 (`cli/pyproject.toml` / `cli/uv.lock`)。リポルートから叩くときは `uv sync --project cli` / `uv run --project cli cluster-forge ...` または `make {env}/...`
- Make ターゲットが CLI のラッパー。**通常運用は Make を叩く** ([`docs/provisioning.md`](docs/provisioning.md))
- Flux / kubectl / cmctl 等 k8s 系ツールは前提

## コミット / PR

- ブランチ命名: `feat/...` / `fix/...` / `docs/...` / `revert/...` (`git log` のパターン参照)
- コミットメッセージ: 既存の Conventional Commits 風 (`feat(scope): ...` / `fix(scope): ...`)。日本語可
- **PR 単位で 1 トピック**。bug fix と refactor を混ぜない
- 大きな変更は **proposal** を `docs/proposals/` に書いて先に合意形成 (例: [`docs/proposals/etcd-defrag.md`](docs/proposals/etcd-defrag.md))

## Policy as Code (Conftest)

`manifests/platform/` は CI で Conftest により Rego ポリシー検査される ([`policies/`](policies/) 配下)。Phase 1 のルール:

| 対象 | 要求 |
|------|------|
| `HelmRelease` | `chart.spec.version` (HelmRepository style) または `chartRef` 先 OCIRepository の `ref.tag/digest` を pin。floating (`*`, `x.x`, `^/~/>/<` 始まり) 禁止 |
| `HelmRelease` | sourceRef / chartRef は同一リポ内に定義された HelmRepository / OCIRepository のみ参照可 |
| `Secret` | `data` / `stringData` 直書き禁止。ExternalSecret (1Password) / cert-manager 経由で生成 |
| `Service` (LoadBalancer) | `lb-ipam.cilium.io/ips` annotation で IP 固定 (詳細 → [`docs/network.md`](docs/network.md)) |

ローカル検査: `make policy/test`。新しいポリシーや例外の追加は [`docs/proposals/policy-as-code.md`](docs/proposals/policy-as-code.md) の段階導入計画に沿う。

**例外を追加する場合**: [`policies/exceptions.rego`](policies/exceptions.rego) に `Kind/namespace/name` の形式で entry 追加 + コミットメッセージで理由を必ず明記。例外なしで通せるなら manifest 側を直す方が筋。

## chicken-and-egg な依存 (ブートストラップ時要注意)

| 依存 | なぜ問題か | 対処 |
|------|-----------|------|
| Cilium → 全 Pod ネットワーク | CNI なしでは何も動かない | Helm CLI で primary に手動先入れ ([`docs/kubernetes.md#ブートストラップ順序`](docs/kubernetes.md#ブートストラップ順序)) |
| CoreDNS → Helm install の名前解決 | install 中に `*.svc.cluster.local` が引けず止まる | 同上 |
| kube-vip → secondary control-plane の join | API VIP が無いと `:6443` 経由の join が失敗 | 同上 |
| 1Password Connect 起動 → External Secrets が動く | Connect が `op-credentials` Secret を要求 | Ansible bootstrap が事前投入 |
| Flux GitHub App credential | Flux 自身が `flux-system` Secret を読む | Ansible bootstrap が事前投入 |
| Zitadel `auth.b8m.app` の解決 | Pod から CF Tunnel 一周すると CF Access が token endpoint を 403 する | CoreDNS で `auth.b8m.app` → Envoy VIP に rewrite |

## 非自明な設計判断 (動かす前に知っておく)

| 領域 | 判断 | 詳細 |
|------|------|------|
| ノード OS ストレージ | RTL9210 の UAS 無効化 quirk が **全ノード必須** (未設定だと高負荷でフリーズ) | [`docs/hardware.md#rtl9210-uas-quirk`](docs/hardware.md#rtl9210-uas-quirk) |
| LB IP 払い出し | Cilium LB-IPAM プールから **annotation で固定**、自動採番ではない | [`docs/network.md#lb-ip-の払い出し方式-重要`](docs/network.md#lb-ip-の払い出し方式-重要) |
| ARP 広告 | Cilium L2 + kube-vip svc_enable の **二重で有効** | 同上 |
| Pod ログ収集 | Alloy が **`/var/log/pods/` を直接 tail** (apiserver log-follow を使わない) | [`docs/platform/observability.md#alloy-3-リリース`](docs/platform/observability.md#alloy-3-リリース) |
| Loki / Tempo | `br-external1` の Garage S3 (cluster-external) に保存 | [`docs/platform/observability.md`](docs/platform/observability.md) |
| Longhorn nodeDownPodDeletionPolicy | `do-nothing` (rebuild storm 回避) | [`docs/platform/storage.md`](docs/platform/storage.md) |
| `auth.b8m.app` 解決 | クラスタ内向けに **CoreDNS で Envoy VIP に rewrite** | [`docs/platform/identity.md`](docs/platform/identity.md) |

## ドキュメントの書き方

- 図の使い分け:
  - **構造化された図 (シーケンス / フロー / 依存関係) は mermaid**
  - **リッチなアーキ図 (ネットワーク全体図、物理配線、アイコン多用) は `*.drawio.svg`** を `docs/assets/` 配下に置き、`![](assets/foo.drawio.svg)` で埋め込む
    - `.drawio.svg` 形式で保存すると GitHub では SVG として表示され、draw.io / VS Code の `hediet.vscode-drawio` で再編集可能
    - `architecture-beta` + iconify は GitHub プレビューでアイコンが解決されないので使わない
- Mermaid 図でエラーが出やすいパターン:
  - ノードラベルに `(loki/tempo)` のような括弧 + `/` → 引用符 `"..."` で囲む or 平文化
  - subgraph タイトルの括弧 → 平文化
  - edge label `|S3 (chunks)|` の括弧 → 短い英数字に簡素化
- 各 doc は **テーブル中心** で構造化 (箇条書きの入れ子は避ける)
- 設計判断の "なぜ" は **採用 / 不採用 / 理由** の表で書く
- 詳細解説は別ファイルに切り出してリンク (例: nftables → `network-nftables-guide.md`)
