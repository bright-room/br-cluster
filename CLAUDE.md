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

- `garage/setup.sh` (`br-storage1` 上のローカル運用スクリプト、リポジトリには含めない)
- `.secret/` 配下 (1Password credentials)
- `.generated/` 配下 (中間生成物)
- 関連リポジトリ (このリポからは触らない):
  - `bright-room/br-cloudflare-terraform` — Cloudflare Tunnel / Access / DNS
  - `bright-room/br-cluster-zitadel-terraform` — Zitadel リソース
  - `*.prod.br-cluster.bright-room.net` / `*.prod.internal-service.bright-room.net` の非 k3s インフラ全般

## 環境前提

- 学習目的の homelab。PVC 利用者はゼロ (Longhorn 撤去済み)。**クラスタ状態は Flux から再構築する**前提で、バックアップは取らない
- Pi のリソース制約があるため、複数の OSS を並走させない設計選択が多い (例: kube-proxy → Cilium、Helm Controller → Flux)
- ネットワーク系の説明は **平易に噛み砕く** (運用者はネットワーク非専門)

## ツール

- `mise` で Python / `uv` / packer 等のバージョンを揃える (`mise install`)
- Python は `uv run` 経由で実行 (`uv run cluster-forge ...` または `make {env}/...`)
- Make ターゲットが CLI のラッパー。**通常運用は Make を叩く** ([`docs/provisioning.md`](docs/provisioning.md))
- Flux / kubectl / cmctl 等 k8s 系ツールは前提

## コミット / PR

- ブランチ命名: `feat/...` / `fix/...` / `docs/...` / `revert/...` (`git log` のパターン参照)
- コミットメッセージ: 既存の Conventional Commits 風 (`feat(scope): ...` / `fix(scope): ...`)。日本語可
- **PR 単位で 1 トピック**。bug fix と refactor を混ぜない
- 大きな変更は **proposal** を `docs/proposals/` に書いて先に合意形成 (例: [`docs/proposals/policy-as-code.md`](docs/proposals/policy-as-code.md))
- `docs/proposals/` 直下は **active なものだけ** を置く。着地 / close したら:
  - 仕様 doc に統合済み or 純粋な作業計画 → ファイル削除 (理由はコミットメッセージに残す)
  - "なぜ採用 / 不採用 / 別案を捨てたか" を後から参照したいもの → `docs/proposals-done/` に mv (このディレクトリは最初の done 発生時に作る)

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
| 1Password Connect 起動 → External Secrets が動く | Connect が `op-credentials` Secret を要求 | Ansible bootstrap が事前投入 |
| Flux GitHub App credential | Flux 自身が `flux-system` Secret を読む | Ansible bootstrap が事前投入 |
| Zitadel `auth.b8m.app` の解決 | Pod から CF Tunnel 一周すると CF Access が token endpoint を 403 する | CoreDNS で `auth.b8m.app` → Envoy VIP に rewrite |

## 非自明な設計判断 (動かす前に知っておく)

| 領域 | 判断 | 詳細 |
|------|------|------|
| ノード OS ストレージ | RTL9210 の UAS 無効化 quirk が **全ノード必須** (未設定だと高負荷でフリーズ) | [`docs/hardware.md#rtl9210-uas-quirk`](docs/hardware.md#rtl9210-uas-quirk) |
| LB IP 払い出し | Cilium LB-IPAM プールから **annotation で固定**、自動採番ではない | [`docs/network.md#lb-ip-の払い出し方式`](docs/network.md#lb-ip-の払い出し方式) |
| `auth.b8m.app` 解決 | クラスタ内向けに **CoreDNS で Envoy Gateway に rewrite** | [`docs/platform/identity.md`](docs/platform/identity.md) |
| k3s upgrade | **system-upgrade-controller (SUC)** + `Plan` CRD 経由。Phase 1a は `agent-plan` (`br-cluster2` 限定) nodeSelector + `window` 未指定で稼働中。`server-plan` (`br-cluster1`) はクラスタ全停止を伴うため計画的実行のみ | [`docs/runbooks/k3s-upgrade.md`](docs/runbooks/k3s-upgrade.md) |
| 定期ジョブ / ジョブネット | **Argo Workflows を優先**。`CronWorkflow` / `WorkflowTemplate` で書く。素の `CronJob` を新設する場合は理由を commit message と manifest コメントで明記 | [`docs/platform/workflows.md`](docs/platform/workflows.md) |
| k3s datastore | **SQLite** (embedded etcd は撤去)。**クラスタ状態のバックアップは取らない** (snapshot 機構が無いため) | [`docs/kubernetes.md#トポロジ`](docs/kubernetes.md#トポロジ) |

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
