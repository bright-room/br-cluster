# ネットワーク設計

IP 設計、ファイアウォール、DNS、VIP、外部公開の経路をまとめる。物理構成は [`docs/hardware.md`](hardware.md) を参照。

## サブネット

数値ソース: [`provisioner/inventories/base/group_vars/all/network.yaml`](../provisioner/inventories/base/group_vars/all/network.yaml) (`cluster_network` / `dhcp` / `service_records`)、Pod/Service CIDR は k3s デフォルト。

| 範囲                          | 用途                                  | 出典 |
|-------------------------------|---------------------------------------|------|
| `172.22.52.0/24`              | クラスタ LAN                          | `cluster_network.cidr` |
| `172.22.52.1`                 | `br-gateway1` (静的)                  | `cluster_hosts[br-gateway1].ip` |
| `172.22.52.10`                | `br-db1`                              | `cluster_hosts[br-db1].ip` |
| `172.22.52.20`                | `br-storage1`                         | `cluster_hosts[br-storage1].ip` |
| `172.22.52.30`                | `br-observability1`                   | `cluster_hosts[br-observability1].ip` |
| `172.22.52.70`                | `br-ai1`                              | `cluster_hosts[br-ai1].ip` |
| `172.22.52.100–102`           | `br-cluster1-3` (Kea DHCP の MAC reservation で固定) | `cluster_hosts` |
| `172.22.52.150–190`           | DHCP 動的レンジ (予備、reservation 範囲外) | `dhcp.range_*` |
| `172.22.52.192/26` (.192–.254) | LoadBalancer IP プール (Cilium LB-IPAM) | `manifests/platform/cilium/config/base/` |
| `172.22.52.200`               | cluster-gateway (Envoy, 外部公開)     | `CLUSTER_GATEWAY_IP` |
| `10.42.0.0/16`                | k3s Pod CIDR (Cilium)                 | nftables `pod_cidr` define |
| `10.43.0.0/16`                | k3s Service CIDR (k3s デフォルト)     | `coredns_cluster_ip: 10.43.0.10` |

`172.60.52.0/24` は当初案だったが採らない。RFC1918 のプライベート範囲は `172.16.0.0/12` (`172.16.x`〜`172.31.x`) までで、`172.60.x` は実在の組織に割り当てられたグローバルアドレスにあたるため。

## ホスト IP

ノード IP は [`servers.yaml`](../servers.yaml) / 1Password から `generate-inventory` が組み立て、Kea DHCP の host reservation として配布される (動的レンジの `.150–190` は予備)。k3s API は VIP を持たず `br-cluster1` の実 IP がそのままエンドポイントになる ([kube-vip 撤去の影響](proposals/2026-09-05-single-cp-rearch.md#kube-vip-撤去の影響))。

| IP               | 名称              | 提供者                        | ホストゾーン FQDN |
|------------------|-------------------|-------------------------------|-------------|
| `172.22.52.1`    | `br-gateway1`     | 静的 (cloud-init / cluster_hosts) | `gateway1.prod.br-cluster.bright-room.net` |
| `172.22.52.10`   | `br-db1`          | Kea DHCP reservation          | `db1.prod.br-cluster.bright-room.net` |
| `172.22.52.20`   | `br-storage1`     | Kea DHCP reservation          | `storage1.prod.br-cluster.bright-room.net` |
| `172.22.52.30`   | `br-observability1` | Kea DHCP reservation        | `observability1.prod.br-cluster.bright-room.net` |
| `172.22.52.70`   | `br-ai1`          | Kea DHCP reservation          | `ai1.prod.br-cluster.bright-room.net` |
| `172.22.52.100`  | `br-cluster1`     | Kea DHCP reservation          | `cluster1.prod.br-cluster.bright-room.net` (k8s API もここ) |
| `172.22.52.101`  | `br-cluster2`     | Kea DHCP reservation          | `cluster2.prod.br-cluster.bright-room.net` |
| `172.22.52.102`  | `br-cluster3`     | Kea DHCP reservation          | `cluster3.prod.br-cluster.bright-room.net` |
| `172.22.52.200`  | cluster-gateway   | Cilium LB-IPAM (annotation 固定) + L2 announce | `*.b8m.app` 終端 |

サービス (アプリケーション) 用のゾーンとレコードは [DNS ゾーン](#dns-ゾーン) を参照。

### LB IP の払い出し方式

`172.22.52.200` (cluster-gateway) は Cilium LB-IPAM プール `172.22.52.192/26` から annotation で明示固定。kube-vip 撤去により Service LB の ARP 広告は Cilium L2 Announcement 単独になった。annotation 例 / ARP 広告 / 追加手順は [`docs/platform/networking.md#lb-ip-払い出し`](platform/networking.md#lb-ip-払い出し)。

## DHCP / DNS / NTP (gateway1)

| サービス | 実装           | 配布レンジ / 主な役割 | 設定先 |
|----------|----------------|------------------------|--------|
| DHCP     | Kea (`kea-dhcp4-server`) | `172.22.52.150–190` (予備動的)、ホストは MAC reservation で固定 | [`provisioner/roles/gateway/tasks/dhcp.yaml`](../provisioner/roles/gateway/tasks/dhcp.yaml) |
| DNS      | CoreDNS (hosts plugin) | 内部ゾーン権威、外部は `8.8.8.8` / `8.8.4.4` にフォワード | [`provisioner/roles/gateway/templates/Corefile.j2`](../provisioner/roles/gateway/templates/Corefile.j2) |
| NTP      | systemd-timesyncd 等   | 上流 `ntp.nict.jp` (フォールバック `ntp{1,2,3}.jst.mfeed.ad.jp`) | [`provisioner/roles/gateway/tasks/ntp.yaml`](../provisioner/roles/gateway/tasks/ntp.yaml) |

DHCP option で配布する DNS は `172.22.52.1` (gateway1 の CoreDNS)。Pod・ノード問わず全名前解決の入口は gateway1。

## DNS ゾーン

物理ホストとその上で動くサービスを 1 ゾーンに混在させていた旧構成 (`cluster-internal.bright-room.net`) をやめ、**ホスト用とサービス用の 2 ゾーンに分割**した。8 台中 5 台が k3s の外に出た再編後は「クラスタ内部」という名前が実態と合わなくなったため。両ゾーンとも環境名を 1 階層挟む (`prod.br-cluster.bright-room.net` / `prod.internal-service.bright-room.net`)。将来 dev クラスタを立てたときにゾーンごと分離できるようにするため。

### ホストゾーン (`prod.br-cluster.bright-room.net`)

物理サーバー 1 台につき 1 レコード (ホスト名から `br-` を剥がした short 名)。値は [ホスト IP](#ホスト-ip) を参照。

### サービスゾーン (`prod.internal-service.bright-room.net`)

| 項目 | 内容 |
|------|------|
| 権威  | gateway1 の CoreDNS |
| レコード | `network.yaml` の `service_records` (サービス名 → 乗っているホスト) を Corefile の `hosts` ブロックに展開。**そのホストの IP を返す A レコード** (CNAME は使わない。CoreDNS の `hosts` プラグインが書けるのは A/AAAA のみ) |

| FQDN | 実体 | 乗っているホスト |
|---|---|---|
| `dns.prod.internal-service.bright-room.net` | CoreDNS | `br-gateway1` |
| `ntp.prod.internal-service.bright-room.net` | NTP | `br-gateway1` |
| `object-storage.prod.internal-service.bright-room.net` | Garage S3 | `br-storage1` |
| `rdbms.prod.internal-service.bright-room.net` | PostgreSQL | `br-db1` |
| `k8s-api.prod.internal-service.bright-room.net` | k3s API サーバー | `br-cluster1` |

`k8s-api` をサービスゾーンに置くのは、将来 HA に戻して VIP を復活させても名前が変わらないようにするため。旧 `cluster-internal.bright-room.net` ゾーン (と、そこに external-dns-coredns が動的登録していた `loki-push` 等のレコード) は撤去済み。

### 外部ゾーン (`b8m.app`)

| 項目 | 内容 |
|------|------|
| 権威 | Cloudflare DNS (別リポジトリ `br-cloudflare-terraform` で IaC 管理) |
| 動的レコード | k3s 内の **external-dns-cloudflare** が Gateway/HTTPRoute から CNAME を書き込み |
| Gateway target | `external-dns.alpha.kubernetes.io/target` で Tunnel CNAME (`<tunnel-id>.cfargotunnel.com`) を指す (Gateway 側に書く / external-dns v0.21+ の gateway-api source は HTTPRoute からは読まない) |
| CF Proxy | HTTPRoute の `external-dns.alpha.kubernetes.io/cloudflare-proxied: "true"` で有効化 |

### WAN から引ける内部名

`wan_exposed_domains` に列挙した名前のみ、CoreDNS が **gateway1 の WAN IP** を返す (Corefile の WAN 側 bind ブロックで定義)。実体到達は次節 nftables の DNAT。

```yaml
# group_vars/all/network.yaml
wan_exposed_domains:
  - "k8s-api.{{ service_domain }}"
```

## ファイアウォール (nftables on gateway1)

全文は [`provisioner/inventories/base/host_vars/br-gateway1.yaml`](../provisioner/inventories/base/host_vars/br-gateway1.yaml)。要点を抜粋。

kubectl / SSH の日常的なリモートアクセスは Cloudflare WARP の private network 経由 (LAN と同じ hostname で到達) に一本化しており、WAN 直の ssh / k8s-api は **CF/WARP 障害時の家 LAN フォールバック専用**として `home_lan_network` (家庭 Wi-Fi の subnet) からのみ許可している。詳細は [`docs/runbooks/cloudflare-tunnel-warp-access.md`](runbooks/cloudflare-tunnel-warp-access.md)。

### INPUT (gateway1 自身あての着信)

| 入口            | 許可ポート |
|-----------------|-----------|
| LAN `eth0` TCP  | `ssh`, `domain` (53) |
| LAN `eth0` UDP  | `domain`, `ntp`, `bootps` (DHCP) |
| WAN `wlan0` TCP | `ssh` (`home_lan_network` 発のみ)、`domain` (家 LAN クライアントが DNS として使うため open) |
| WAN `wlan0` UDP | `domain` |

node-exporter / Alloy / etcd 向けのポート (`9100` / `9101` / `12345` / `2379`) は、オブザーバビリティ収集経路と kube-vip / gateway1 の etcd の撤去に伴い廃止。

### FORWARD (経路許可)

| 方向         | 許可 |
|--------------|------|
| LAN → WAN TCP | `http`, `https`, `domain`, `7844` (cloudflared QUIC), `submission` (587, Zitadel → Resend SMTP) |
| LAN → WAN UDP | `domain`, `ntp`, `7844` |
| WAN → LAN (`home_lan_network` 発のみ) | `ssh` (任意ノード)、`tcp/6443` (`br-cluster1` 宛のみ) |

### NAT

| チェーン      | ルール |
|---------------|--------|
| `prerouting`  | DNAT: WAN `tcp/6443` → `br-cluster1` の実 IP `172.22.52.100:6443` (k8s API、VIP なし) |
| `postrouting` | LAN → WAN masquerade、WAN → LAN hairpin masquerade (DNAT 戻り経路) |

## 外部公開フロー

`https://<svc>.b8m.app` のリクエストが Cloudflare Edge → Cloudflare Tunnel → cloudflared Pod → Envoy Gateway → App Pod に流れる一気通貫フローは、k8s レイヤの責務として [`docs/platform/networking.md#外部公開フロー-httpssvcb8mapp`](platform/networking.md#外部公開フロー-httpssvcb8mapp) に集約。

物理側で関与するのは「家庭ルーターは inbound 不要 (outbound QUIC のみ)」という点だけで、ポート開放・DNAT は無い (k8s API の DNAT のみ、家 LAN フォールバック専用 → [`#nat`](#nat))。

## 関連

- [`docs/hardware.md`](hardware.md) — 物理構成・NIC 割当
- [`docs/kubernetes.md`](kubernetes.md) — k3s 内部 (Cilium / Gateway 等)
- [`docs/architecture.md`](architecture.md) — 設計判断 (なぜ Cloudflare Tunnel か等)
- [`docs/network-nftables-guide.md`](network-nftables-guide.md) — nftables ルールの初学者向け解説
- [`provisioner/inventories/base/group_vars/all/network.yaml`](../provisioner/inventories/base/group_vars/all/network.yaml) — ホスト IP/MAC は `make {env}/generate-inventory` が 1Password から `inventories/{env}/group_vars/all/cluster_hosts.yaml` (gitignored) に生成する。設定値そのものの SoT はこちら
