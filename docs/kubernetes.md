# Kubernetes (k3s) クラスタ概要

クラスタ全体のトポロジ、k3s 構成、ブートストラップ順序、プラットフォームコンポーネントの全体像をまとめる。各コンポーネントの詳細は [`docs/platform/`](platform/) 配下の **グループ別 doc** を参照。

物理層は [`docs/hardware.md`](hardware.md)、L2/L3 は [`docs/network.md`](network.md)。

## トポロジ

| 項目                    | 値 |
|-------------------------|----|
| ディストリビューション  | k3s `v1.35.3+k3s1` ([`versions.yaml`](../provisioner/inventories/base/group_vars/all/versions.yaml)) |
| Control Plane           | 1 台 (`br-cluster1`)、datastore は **SQLite** (embedded etcd は撤去) |
| Worker                  | 2 台 (`br-cluster2` / `br-cluster3`) |
| API エンドポイント      | `br-cluster1` 実 IP `172.22.52.100` (VIP なし) |
| Pod CIDR                | `10.42.0.0/16` (Cilium IPAM cluster-pool) |
| Service CIDR            | `10.43.0.0/16` (k3s デフォルト) |
| CoreDNS ClusterIP       | `10.43.0.10` |

control-plane を 1 台に縮小したのは、Raspberry Pi 上で 3 台分の control-plane リソースを維持するコストが homelab の規模に見合わないため。可用性の向上は目的とせず、`br-cluster1` の再起動・故障がクラスタ全停止に直結することを受け入れる選択をしている ([ダウンタイム特性とバックアップ方針](proposals/2026-09-05-single-cp-rearch.md#ダウンタイム特性とバックアップ方針))。**クラスタ状態のバックアップは取らない** — SQLite には etcd snapshot に相当する機構がなく、Flux による GitOps と「PVC は ephemeral」という前提のもと、`br-cluster1` が死んだ場合は再フラッシュして Flux に再構築させるのが復旧手順になる。

### ノード別 k3s 役割

物理ホストの一覧は [`docs/hardware.md`](hardware.md#ノード一覧)。ここでは k3s レイヤの役割のみ示す。

| ホスト         | k3s role  | 起動方法                          | 主な責務 |
|----------------|-----------|-----------------------------------|----------|
| `br-cluster1`  | primary   | `k3s server` (クラスタ起動モード) | ブートストラップ元 (Cilium / CoreDNS / Flux を適用)。control-plane taint 維持 (スケジュール不可) |
| `br-cluster2`  | worker    | `k3s agent`                        | ワークロード Pod |
| `br-cluster3`  | worker    | `k3s agent`                        | ワークロード Pod |

control-plane の taint (`node-role.kubernetes.io/control-plane:NoSchedule`) は維持する。control-plane が 1 台になるとワークロードとのリソース競合の影響がクラスタ全体に及ぶため。Cilium / CoreDNS には既に toleration がある。

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
| `local-storage`          | (現状維持。Longhorn 撤去後も PVC 利用者はゼロ) |

## ブートストラップ順序

Flux が動く前に、**primary ノード上で Helm を直接叩いて 2 つだけ先入れ**する ([`provisioner/playbooks/setup_node.yaml`](../provisioner/playbooks/setup_node.yaml) Play 2)。

| 順 | コンポーネント | これが無いと起きる問題 |
|----|----------------|------------------------|
| 1  | **Cilium**     | Pod ネットワークが開通せず何も動かない |
| 2  | **CoreDNS**    | `*.svc.cluster.local` が引けず Helm install が止まる |

kube-vip は API VIP と Service LB の ARP 広告を担っていたが、control-plane が 1 台になり API VIP 自体が不要になったため撤去した。Service LB の ARP 広告は Cilium L2 Announcement 単独で担う ([`platform/networking.md`](platform/networking.md#arp-広告))。

values は `manifests/platform/{cilium,coredns}/app/{base,overlays/prod}/` の同じものを Helm CLI で apply。以降、Flux が同一 values を `HelmRelease` として再宣言するため差分は発生しない。

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
| `CLUSTER_GATEWAY_IP`            | `172.22.52.200` |
| `TRUSTED_INTERNAL_POD_CIDR`     | `10.42.0.0/16` |
| `CLOUDFLARED_TUNNEL_ID` / `_CNAME` | Cloudflare Tunnel 識別子 |

`CLUSTER_INTERNAL_DOMAIN` / `KUBE_VIP_ADDRESS` / `INTERNAL_CLUSTER_GATEWAY_IP` / `COREDNS_ETCD_URL` は kube-vip / internal-gateway / external-dns-coredns の撤去に伴い廃止。内部 DNS のゾーン設計は [`docs/network.md`](network.md) を参照。

## プラットフォームコンポーネント (グループ別)

`manifests/platform/` 配下のすべてのコンポーネントは Flux で配布される (SoT: [`manifests/clusters/prod/platform/kustomization.yaml`](../manifests/clusters/prod/platform/kustomization.yaml))。グループ別 doc に詳細を集約。

| グループ | 含まれるリソース | doc |
|---|---|---|
| Networking             | Cilium / CoreDNS / Envoy Gateway / cloudflared / external-dns-cloudflare | [`platform/networking.md`](platform/networking.md) |
| Identity               | Zitadel / zitadel-terraform-app | [`platform/identity.md`](platform/identity.md) |
| Certificate Management | cert-manager | [`platform/certificate.md`](platform/certificate.md) |
| Secrets                | 1Password Connect / External Secrets Operator | [`platform/secrets.md`](platform/secrets.md) |
| GitOps                 | Flux Operator / Flux CD | [`platform/gitops.md`](platform/gitops.md) |
| Workflow Automation    | Argo Workflows | [`platform/workflows.md`](platform/workflows.md) |
| Policy as Code         | Conftest + Rego (`policies/`) | [`platform/policy.md`](platform/policy.md) |

`metrics-server` / `system-upgrade-controller` は単機能コンポーネントのため専用の `platform/*.md` を持たない。`system-upgrade-controller` は [`runbooks/k3s-upgrade.md`](runbooks/k3s-upgrade.md) を参照。

## 関連

- [`docs/architecture.md`](architecture.md) — 設計判断の背景 (Cloudflare Tunnel / Zitadel / 認証 2 層など)
- [`docs/network.md`](network.md) — LAN / VIP / ファイアウォール
- [`docs/provisioning.md`](provisioning.md) — Ansible 側のブートストラップ
