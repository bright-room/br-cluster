# Kubernetes (k3s) クラスタ概要

クラスタ全体のトポロジ、k3s 構成、ブートストラップ順序、プラットフォームコンポーネントの全体像をまとめる。各コンポーネントの詳細は [`docs/platform/`](platform/) 配下の **グループ別 doc** を参照。

物理層は [`docs/hardware.md`](hardware.md)、L2/L3 は [`docs/network.md`](network.md)。

## トポロジ

| 項目                    | 値 |
|-------------------------|----|
| ディストリビューション  | k3s `v1.35.3+k3s1` ([`versions.yaml`](../provisioner/inventories/base/group_vars/all/versions.yaml)) |
| Control Plane           | 3 台 (`br-node1` primary / `br-node2` / `br-node3`)、組込み etcd クォーラム |
| Worker                  | 3 台 (`br-node4-6`) |
| API VIP                 | `172.22.10.60` を kube-vip DaemonSet が ARP announce |
| Pod CIDR                | `10.42.0.0/16` (Cilium IPAM cluster-pool) |
| Service CIDR            | `10.43.0.0/16` (k3s デフォルト) |
| CoreDNS ClusterIP       | `10.43.0.10` |

## k3s で無効化している組込みコンポーネント

[`provisioner/roles/k3s/templates/config.yaml.master.j2`](../provisioner/roles/k3s/templates/config.yaml.master.j2) で以下を無効化し、外部実装に差し替えている。

| 無効化対象               | 代替                              |
|--------------------------|-----------------------------------|
| `flannel-backend: none`  | Cilium (CNI)                      |
| `disable-network-policy` | Cilium (NetworkPolicy)            |
| `disable-kube-proxy`     | Cilium eBPF kube-proxy replacement |
| `disable-helm-controller`| Flux の HelmRelease               |
| `servicelb`              | Cilium LB-IPAM + L2 Announcement  |
| `traefik`                | Envoy Gateway                     |

## ブートストラップ順序

Flux が動く前に、**primary ノード上で Helm を直接叩いて 3 つだけ先入れ**する ([`provisioner/playbooks/setup_node.yaml`](../provisioner/playbooks/setup_node.yaml) Play 2)。

| 順 | コンポーネント | これが無いと起きる問題 |
|----|----------------|------------------------|
| 1  | **Cilium**     | Pod ネットワークが開通せず secondary control-plane も上がれない |
| 2  | **CoreDNS**    | `*.svc.cluster.local` が引けず Helm install が止まる |
| 3  | **kube-vip**   | API VIP が announce されず secondary が `:6443` で参加できない |

values は `manifests/platform/{cilium,coredns,kube-vip}/app/{base,overlays/prod}/` の同じものを Helm CLI で apply。以降、Flux が同一 values を `HelmRelease` として再宣言するため差分は発生しない。

その後 [`bootstrap_cluster.yaml`](../provisioner/playbooks/bootstrap_cluster.yaml) が:

1. ノードの Ready 待ち (`bootstrap/verify_nodes`)
2. 初期 Secret 投入 (1Password Connect 認証情報、GitHub App)
3. **Flux Operator** インストール → Flux 本体 → `GitRepository` + 最上位 `Kustomization` 登録

以降、新規コンポーネントは `manifests/platform/<name>/` を作って [`manifests/clusters/prod/platform/kustomization.yaml`](../manifests/clusters/prod/platform/kustomization.yaml) に列挙すれば Flux が反映する。

## クラスタ全体設定変数

[`manifests/clusters/prod/config/cluster-settings.yaml`](../manifests/clusters/prod/config/cluster-settings.yaml) の ConfigMap が、各 manifest から `${VAR}` で参照される (Flux Substitute)。

| 変数                            | 値 |
|---------------------------------|----|
| `CLUSTER_DOMAIN`                | `b8m.app` |
| `CLUSTER_INTERNAL_DOMAIN`       | `cluster-internal.bright-room.net` |
| `KUBE_VIP_ADDRESS`              | `172.22.10.60` |
| `CLUSTER_GATEWAY_IP`            | `172.22.10.70` |
| `INTERNAL_CLUSTER_GATEWAY_IP`   | `172.22.10.71` |
| `COREDNS_ETCD_URL`              | `http://172.22.10.1:2379` |
| `TRUSTED_INTERNAL_POD_CIDR`     | `10.42.0.0/16` |
| `CLOUDFLARED_TUNNEL_ID` / `_CNAME` | Cloudflare Tunnel 識別子 |

## プラットフォームコンポーネント (グループ別)

`manifests/platform/` 配下のすべてのコンポーネントは Flux で配布される (SoT: [`manifests/clusters/prod/platform/kustomization.yaml`](../manifests/clusters/prod/platform/kustomization.yaml))。グループ別 doc に詳細を集約。

| グループ | 含まれるリソース | doc |
|---|---|---|
| Networking             | Cilium / CoreDNS / kube-vip / Envoy Gateway / cloudflared / external-dns-cloudflare / external-dns-coredns | [`platform/networking.md`](platform/networking.md) |
| Identity               | Zitadel / zitadel-terraform-app | [`platform/identity.md`](platform/identity.md) |
| Certificate Management | cert-manager | [`platform/certificate.md`](platform/certificate.md) |
| MicroService           | CloudNativePG / platform-pg-cluster | [`platform/microservice.md`](platform/microservice.md) |
| Secrets                | 1Password Connect / External Secrets Operator | [`platform/secrets.md`](platform/secrets.md) |
| Storage                | Longhorn / csi-external-snapshotter | [`platform/storage.md`](platform/storage.md) |
| Observability          | kube-prometheus-stack / Grafana / Loki / Tempo / OpenTelemetry Collector / Alloy×3 / hubble-flow-exporter / metrics-server | [`platform/observability.md`](platform/observability.md) |
| GitOps                 | Flux Operator / Flux CD | [`platform/gitops.md`](platform/gitops.md) |

## 関連

- [`docs/architecture.md`](architecture.md) — 設計判断の背景 (Cloudflare Tunnel / Zitadel / 認証 2 層など)
- [`docs/network.md`](network.md) — LAN / VIP / ファイアウォール
- [`docs/provisioning.md`](provisioning.md) — Ansible 側のブートストラップ
