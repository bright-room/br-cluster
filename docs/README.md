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
| プラットフォームコンポーネントの詳細 (グループ別)             | [`platform/`](platform/) |
| 過去のインシデント記録                                        | [`incidents/`](incidents/) |
| 検討中・未実装の改善案                                        | [`proposals/`](proposals/) |

## プラットフォームコンポーネント (グループ別)

`manifests/platform/` 配下の全コンポーネントを 8 グループに整理。

| グループ                  | 対象リソース                                                                                               | doc |
|---------------------------|------------------------------------------------------------------------------------------------------------|-----|
| Networking                | Cilium / CoreDNS / kube-vip / Envoy Gateway / cloudflared / external-dns-cloudflare / external-dns-coredns | [`platform/networking.md`](platform/networking.md) |
| Identity                  | Zitadel / zitadel-terraform-app                                                                            | [`platform/identity.md`](platform/identity.md) |
| Certificate Management    | cert-manager                                                                                               | [`platform/certificate.md`](platform/certificate.md) |
| MicroService              | CloudNativePG / platform-pg-cluster                                                                        | [`platform/microservice.md`](platform/microservice.md) |
| Secrets                   | 1Password Connect / External Secrets Operator                                                              | [`platform/secrets.md`](platform/secrets.md) |
| Storage                   | Longhorn / csi-external-snapshotter                                                                        | [`platform/storage.md`](platform/storage.md) |
| Observability             | kube-prometheus-stack / Grafana / Loki / Tempo / OTel Collector / Alloy×3 / hubble-flow-exporter / metrics-server | [`platform/observability.md`](platform/observability.md) |
| GitOps                    | Flux Operator / Flux CD / tofu-controller                                                                  | [`platform/gitops.md`](platform/gitops.md) |
| Policy as Code            | Conftest + Rego (`policies/`) — CI で manifests を検査                                                     | [`platform/policy.md`](platform/policy.md) |

## 書き方の前提

- **コードが SoT**。doc とコードが食い違ったらコードが正
- 図はすべて mermaid (drawio / PNG は持たない)
- 設計判断の "なぜ" は `architecture.md` または各 `platform/*.md` の冒頭に集約
- 過去のインシデントは `incidents/<date>-<topic>.md`、検討中の提案は `proposals/<topic>.md`
