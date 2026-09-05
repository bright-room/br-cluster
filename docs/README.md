# docs/

br-cluster のドキュメント目次。読みたい目的から該当ファイルへ。

## どれを読むか

| 目的 | doc |
|------|-----|
| 物理ノードの構成・ディスクレイアウト・RTL9210 quirk          | [`hardware.md`](hardware.md) |
| サブネット / VIP / DHCP / DNS / nftables                      | [`network.md`](network.md) (詳細解説: [`network-nftables-guide.md`](network-nftables-guide.md)) |
| Packer / Ansible / cluster-forge でゼロから組み上げる         | [`provisioning.md`](provisioning.md) |
| `cluster-forge` CLI の仕様 / 拡張ガイド                       | [`cli.md`](cli.md) |
| k3s クラスタ全体像、ブート順、cluster-settings              | [`kubernetes.md`](kubernetes.md) |
| 設計判断の "なぜ" を一望、外部公開フロー、認証 2 層、管理境界 | [`architecture.md`](architecture.md) |
| 運用の手順 (シャットダウン / 起動 / k3s リセット 等)         | [`operations.md`](operations.md) |
| トピック別の長文 runbook (k3s アップグレード / Renovate 等)   | [`runbooks/`](runbooks/) |
| プラットフォームコンポーネントの詳細 (グループ別)             | [`platform/`](platform/) |
| 過去のインシデント記録                                        | [`incidents/`](incidents/) |
| 検討中・未実装の改善案                                        | [`proposals/`](proposals/) |

## プラットフォームコンポーネント (グループ別)

`manifests/platform/` 配下の全コンポーネントを 6 グループに整理。`metrics-server` / `system-upgrade-controller` は単機能コンポーネントのため専用 doc は持たず、[`kubernetes.md`](kubernetes.md) と [`runbooks/k3s-upgrade.md`](runbooks/k3s-upgrade.md) でそれぞれ扱う。

| グループ                  | 対象リソース                                                                                               | doc |
|---------------------------|------------------------------------------------------------------------------------------------------------|-----|
| Networking                | Cilium / CoreDNS / Envoy Gateway / cloudflared / external-dns-cloudflare                                   | [`platform/networking.md`](platform/networking.md) |
| Identity                  | Zitadel / zitadel-terraform-app                                                                            | [`platform/identity.md`](platform/identity.md) |
| Certificate Management    | cert-manager                                                                                               | [`platform/certificate.md`](platform/certificate.md) |
| Secrets                   | 1Password Connect / External Secrets Operator                                                              | [`platform/secrets.md`](platform/secrets.md) |
| GitOps                    | Flux Operator / Flux CD / tofu-controller                                                                  | [`platform/gitops.md`](platform/gitops.md) |
| Workflow Automation       | Argo Workflows                                                                                             | [`platform/workflows.md`](platform/workflows.md) |
| Policy as Code            | Conftest + Rego (`policies/`) — CI で manifests を検査                                                     | [`platform/policy.md`](platform/policy.md) |

## 書き方の前提

- **コードが SoT**。doc とコードが食い違ったらコードが正
- 図はすべて mermaid (drawio / PNG は持たない)
- 設計判断の "なぜ" は `architecture.md` または各 `platform/*.md` の冒頭に集約
- 過去のインシデントは `incidents/<date>-<topic>.md`、検討中の提案は `proposals/<topic>.md`
