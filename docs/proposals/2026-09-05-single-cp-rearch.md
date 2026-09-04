# シングル control-plane 化とノード役割の再編

3 台の control-plane による HA 構成をやめ、**control-plane 1 台 + worker 2 台の k3s クラスタ**に縮小する。空いた 4 台は k3s から外し、PostgreSQL / オブジェクトストレージ / オブザーバビリティ / 雑務用の単独ホストとして再定義する。あわせて k3s 内のプラットフォームを大幅に削減し、クラスタ LAN のサブネットを引き直す。

対象リポジトリは `br-cluster` のみ。`br-cloudflare-terraform` / `br-cluster-zitadel-terraform` 側の作業は [前提条件](#前提条件-このリポの外) に列挙する。

## 背景と目的

| 項目 | 内容 |
|------|------|
| 課題 | Raspberry Pi 上で control-plane を 3 台維持するリソースコストが、homelab の規模に対して見合わない |
| 目的 | k3s を最小構成に絞り、余った物理ノードを役割の明確な単独サーバーとして再配置する |
| 非目的 | 可用性の向上。**シングル control-plane はクラスタ全停止を伴う運用を受け入れる選択** ([ダウンタイム特性](#ダウンタイム特性とバックアップ方針)) |

## 移行方式

**全 8 台リフラッシュ**。稼働させたままの段階移行 (etcd の 3 → 1 メンバー縮退、ノードの順次 drain / 改名 / 再 join) は採らない。

| 採用 | 不採用 | 理由 |
|------|--------|------|
| 全台リフラッシュ | 稼働中の段階移行 | CLAUDE.md の「PVC は ephemeral」前提と整合する。etcd 縮退・kube-vip 撤去・ホスト名変更・サブネット変更が同時に絡む手順を作らずに済み、手順が「新規構築」と同じ形になる |

ダウンタイムは長いが、手順は単純になる。

## ノード構成

### ノードマップ

| 新ホスト名 | 旧 | `type` | `services` | `k8s_role` | `storage_mode` | データマウント |
|---|---|---|---|---|---|---|
| `br-gateway1` | `br-gateway1` | `gateway` | — | — | `none` | — |
| `br-db1` | `br-external1` | `standalone` | `postgresql` | — | `ext4` | `/var/lib/postgresql` |
| `br-storage1` | `br-node1` | `standalone` | `garage`, `caddy`, `certbot` | — | `ext4` | `/storage` |
| `br-observability1` | `br-node2` | `standalone` | (なし) | — | `ext4` | `/storage` |
| `br-ai1` | `br-node3` | `standalone` | (なし) | — | `ext4` | `/storage` |
| `br-cluster1` | `br-node4` | `node` | — | `primary` | `none` | — |
| `br-cluster2` | `br-node5` | `node` | — | `worker` | `none` | — |
| `br-cluster3` | `br-node6` | `node` | — | `worker` | `none` | — |

補足:

- `br-gateway1` は名前を維持する。将来 gateway を冗長化する案が出たときに `br-gateway2` を足せるようにするため。
- `br-external1` という名前は廃番。`br-node3` の新名として再利用すると、DNS / 1Password / inventory に旧定義が残る期間に取り違えが起きる。
- `br-observability1` / `br-ai1` は本 proposal の範囲では **OS だけ入った空のホスト**。載せるものは別 proposal で決める。
- `ext4` パーティションは後から切り直すのに再フラッシュが必要なため、当面用途のない `br-observability1` / `br-ai1` にも今のうちに切っておく。
- `br-cluster1-3` は Longhorn 撤去により PVC 利用者がゼロになるので `storage_mode: none`。

### 物理ノードの割り当て

物理個体 (MAC) とホスト名の対応は、ディスクを消す前に全 8 台で `free -h` を取得してから決める。搭載メモリに差がある場合、8GB 個体を `br-db1` / `br-cluster1-3` に、少ない個体を `br-observability1` / `br-ai1` に割り当てる。

## サーバー定義のモデル

非 k3s ホストが 1 台から 4 台に増えるため、`ServerType` enum の扱いを変更する。

### 採用: 汎用 `standalone` 型 + `services` リスト

```yaml
# servers.yaml
- name: br-storage1
  type: standalone
  services: [garage, caddy, certbot]
- name: br-db1
  type: standalone
  services: [postgresql, certbot]
- name: br-observability1
  type: standalone
  services: []
- name: br-ai1
  type: standalone
  services: []
```

`services` の各要素から Ansible のグループを 1 つずつ生成し、playbook は `setup-standalone` の 1 本にまとめる。ホストにサービスを足すときは `servers.yaml` に 1 行足して role を書くだけで、Python も Make ターゲットも触らない。

### 不採用

| 案 | 不採用の理由 |
|---|---|
| ホストごとに型を増やす (`db` / `storage` / `observability` / `ai`) | enum に 4 値・playbook 4 本・Make ターゲット 4 つが増え、ホストと役割が 1:1 に固定される。`br-ai1` に何か足すたびに Python の変更が必要になる。今回の再編自体が「役割の付け替え」であり、同じ硬さを作り直すことになる |
| `external` 型のまま 4 台を収容 | 4 台が同一グループに入るため、role 側が `inventory_hostname` で分岐する形になる |

### CLI の変更点

| ファイル | 変更 |
|---|---|
| `cli/cluster_forge/models.py` | `ServerType` に `STANDALONE` 追加、`EXTERNAL` 削除。`Server` に `services: list[str]` を追加 (デフォルト空リスト)。`K8sRole` は `primary` / `secondary` / `worker` のまま変更しない (`secondary` は未使用値として残す。将来 HA に戻す際に必要) |
| `cli/cluster_forge/inventory_generator.py` | `external` グループを廃止し、`services` の値ごとにグループを生成。`cluster_members` の組み立ても `standalone` を含むよう変更。`_build_domains()` から `type == GATEWAY` / `type == EXTERNAL` の分岐を削除し、全ホストに `<short>.<host_domain>` を 1 つ生やすだけにする ([サービスレコードの生成方式](#サービスレコードの生成方式)) |
| `cli/cluster_forge/provisioner.py` | `setup-external` → `setup-standalone` |
| `cli/cluster_forge/secrets.py` | `MOCK_SERVERS` を新ホスト名 / 新 IP に更新 |
| `cli/cluster_forge/bootstrap.py` | ssh config の ProxyJump 判定は `type == GATEWAY` のままで動作する (変更不要) |
| `Makefile` | `provision/setup-external` → `provision/setup-standalone` |
| `cli/tests/` | 期待値をノードマップに合わせて更新 |

## ネットワーク

クラスタ LAN を `172.22.10.0/24` から **`172.22.52.0/24`** に引き直す。

### IP 設計

| 範囲 | 用途 |
|---|---|
| `172.22.52.1` | `br-gateway1` (LAN `eth0` / WAN `wlan0`) |
| `172.22.52.10` | `br-db1` |
| `172.22.52.20` | `br-storage1` (`object-storage.prod.internal-service.bright-room.net`) |
| `172.22.52.30` | `br-observability1` |
| `172.22.52.70` | `br-ai1` |
| `172.22.52.100` / `.101` / `.102` | `br-cluster1` / `br-cluster2` / `br-cluster3` |
| `172.22.52.150–190` | DHCP 動的レンジ (予備) |
| `172.22.52.192/26` (.192–.254) | LoadBalancer IP プール (Cilium LB-IPAM) |
| `172.22.52.200` | cluster-gateway (Envoy, 外部公開) |

Pod CIDR (`10.42.0.0/16`) と Service CIDR (`10.43.0.0/16`) は k3s デフォルトのまま変更しない。

`172.60.52.0/24` は当初案だったが採らない。RFC1918 のプライベート範囲は `172.16.0.0/12` (= `172.16.x` 〜 `172.31.x`) までで、`172.60.x` は実在の組織に割り当てられたグローバルアドレスにあたる。LAN 内で使うと、将来その宛先に実際に到達したいときに原因の分かりにくい疎通不良になる。

### 廃止する IP

| 旧 | 廃止理由 |
|---|---|
| `172.22.10.60` (k8s API VIP) | kube-vip 撤去。旧 `cluster_vips` は廃止し、`k8s-api` は `service_records` の 1 エントリ (`host: br-cluster1`) として `172.22.52.100` を返す A レコードになる ([サービスレコードの生成方式](#サービスレコードの生成方式)) |
| `172.22.10.71` (internal-gateway) | internal-gateway 撤去 ([external-dns-coredns の撤去](#external-dns-coredns--internal-gateway-の撤去)) |

### gateway1 の変更

| 箇所 | 変更 |
|---|---|
| nftables NAT prerouting | DNAT 先を `172.22.52.100:6443` に |
| nftables FORWARD (WAN → LAN) | `tcp/6443` の宛先を `172.22.52.100` に |
| nftables INPUT (LAN `eth0` TCP) | `9100` / `9101` / `12345` を削除 (node-exporter / Alloy の撤去に伴う) |
| nftables INPUT (LAN `eth0` TCP / Pod CIDR TCP) | `2379` を削除 (etcd 撤去に伴う) |
| CoreDNS `Corefile.j2` | 権威ゾーンを `prod.br-cluster.bright-room.net` / `prod.internal-service.bright-room.net` の 2 ブロックに再構成 ([Corefile の構成](#corefile-の構成))。`etcd` プラグインブロックを削除 |
| `roles/gateway` | `etcd.service.j2` とその関連タスクを削除 |
| `roles/gateway` | `cloudflared` (warp-routing / infra トンネル) を `roles/external` から移設 |
| `inventories/base/group_vars/all/network.yaml` | 数値の SoT。`cluster_network.cidr` / `subnet` / `netmask`、`dhcp.range_begin` / `range_end` を新サブネットに更新。`cluster_domain` を `host_domain` / `service_domain` の 2 変数に分割し、`cluster_vips` を `service_records` に置き換える ([ドメイン設計](#ドメイン設計))。`wan_exposed_domains` は `k8s-api.{{ service_domain }}` に |

`cloudflared` を gateway1 に置くのは、LAN 全体への経路を提供する役割であり、境界ルーターに置くのが責務として自然なため。他のノードが全部落ちていてもリモートから入れる。

## ドメイン設計

`cluster-internal.bright-room.net` の 1 ゾーンに、物理ホストとその上で動くサービスを混在させていた。再編後は 8 台中 5 台が k3s の外に出るため「クラスタ内部」という名前が実態と合わなくなる。**ホスト用とサービス用の 2 ゾーンに分割する。**

**両ゾーンとも環境名を 1 階層挟む** (`prod.br-cluster.bright-room.net` / `prod.internal-service.bright-room.net`)。`servers.yaml` は既に `dev` / `prod` の 2 環境を宣言しており、将来 dev クラスタを立てたときにゾーンごと分離できるようにするため。以下の表は `prod` の値で示す。

環境名は `network.yaml` の `cluster_env` に持たせ、`host_domain` / `service_domain` はそこから組み立てる。`provisioner/inventories/{env}/` は `generate-inventory` が生成するディレクトリなので、CLI が `cluster_env` を書き出せばよい。

`b8m.app` (外部公開) は現状維持。Cloudflare のユニバーサル証明書がサブドメイン 1 階層までしかカバーしないため、これ以上深くできない。dev 環境を外部公開する必要が出た場合の命名は本 proposal の範囲外とする。

### ホストゾーン `<env>.br-cluster.bright-room.net`

物理サーバー 1 台につき 1 レコード。ホスト名から `br-` を剥がした short 名を使う (現行の規則を踏襲)。

| ホスト | FQDN | IP |
|---|---|---|
| `br-gateway1` | `gateway1.prod.br-cluster.bright-room.net` | `172.22.52.1` |
| `br-db1` | `db1.prod.br-cluster.bright-room.net` | `172.22.52.10` |
| `br-storage1` | `storage1.prod.br-cluster.bright-room.net` | `172.22.52.20` |
| `br-observability1` | `observability1.prod.br-cluster.bright-room.net` | `172.22.52.30` |
| `br-ai1` | `ai1.prod.br-cluster.bright-room.net` | `172.22.52.70` |
| `br-cluster1` / `br-cluster2` / `br-cluster3` | `cluster1.prod.br-cluster.bright-room.net` 他 | `172.22.52.100`–`.102` |

### サービスゾーン `<env>.internal-service.bright-room.net`

サーバー上で動くアプリケーション。**そのサービスが乗っているホストの IP を返す A レコード** (CNAME にはしない。CoreDNS の `hosts` プラグインで書けるのは A/AAAA だけで、CNAME を使うと `template` / `file` プラグインへの移行が必要になる)。

| FQDN | 実体 | 乗っているホスト |
|---|---|---|
| `dns.prod.internal-service.bright-room.net` | CoreDNS | `br-gateway1` |
| `ntp.prod.internal-service.bright-room.net` | NTP | `br-gateway1` |
| `object-storage.prod.internal-service.bright-room.net` | Garage S3 | `br-storage1` |
| `rdbms.prod.internal-service.bright-room.net` | PostgreSQL | `br-db1` |
| `k8s-api.prod.internal-service.bright-room.net` | k3s API サーバー | `br-cluster1` |

`k8s-api` をサービスゾーンに置くのは、将来 HA に戻して VIP を復活させても名前が変わらないようにするため。

### サービスレコードの生成方式

現行の `_build_domains()` は `type == GATEWAY` なら `dns` / `ntp`、`type == EXTERNAL` なら `object_storage` を生やす、という型分岐でサービス名を持っていた。

DNS のサービス名は Ansible の role 名 (`services` の値) と一致しない (`garage` → `object-storage`、`postgresql` → `rdbms`、`k8s-api` に至っては role ですらない)。無理に対応づけると隠れた変換表ができるため、**`network.yaml` に明示的な対応表を置く**。

```yaml
# provisioner/inventories/base/group_vars/all/network.yaml
cluster_env: prod          # generate-inventory が環境ごとに書き出す
host_domain: "{{ cluster_env }}.br-cluster.bright-room.net"
service_domain: "{{ cluster_env }}.internal-service.bright-room.net"

service_records:
  - name: dns
    host: br-gateway1
  - name: ntp
    host: br-gateway1
  - name: object-storage
    host: br-storage1
  - name: rdbms
    host: br-db1
  - name: k8s-api
    host: br-cluster1
```

これにより:

- `_build_domains()` は「全ホストに `<short>.<host_domain>` を 1 つ生やす」だけになり、`type` 分岐が消える
- 旧 `cluster_vips` は廃止 (VIP という概念自体が kube-vip とともに消える)
- サービスをホスト間で引っ越すときは `service_records` の `host` を書き換えるだけ

### Corefile の構成

権威ゾーンのブロックを 2 つ持つ。

| ゾーン | レコード源 |
|---|---|
| `{{ host_domain }}` | `cluster_hosts[*]` の `ip` と `domains.server` |
| `{{ service_domain }}` | `service_records[*]` の `name` + 対応するホストの `ip` |

`etcd` プラグインは削除する ([external-dns-coredns の撤去](#external-dns-coredns--internal-gateway-の撤去))。`auth.b8m.app` を Envoy VIP に向ける rewrite は k3s 内の CoreDNS 側の設定であり、gateway1 の Corefile とは別なので影響しない。

### 廃止

`cluster-internal.bright-room.net` ゾーンは廃止。旧 `loki-push.cluster-internal.bright-room.net` (`external-dns-coredns` が動的登録していた) も消える。

## k3s

### control-plane とデータストア

| 項目 | 現行 | 新 |
|---|---|---|
| datastore | embedded etcd (`cluster-init: true`) | **SQLite** (`cluster-init` 行を削除し k3s のデフォルトに戻す) |
| control-plane | 3 台 (primary + secondary × 2) | 1 台 (`br-cluster1`) |
| control-plane の taint | `node-role.kubernetes.io/control-plane:NoSchedule` | **維持** (スケジュール可能ノードは `br-cluster2` / `br-cluster3` の 2 台) |
| API エンドポイント | kube-vip VIP `172.22.10.60` | `br-cluster1` 実 IP `172.22.52.100` |
| `tls-san` | VIP + VIP ドメイン + primary IP | `k8s-api.prod.internal-service.bright-room.net` + `172.22.52.100` |
| bootstrap 手動先入れ | Cilium + CoreDNS + kube-vip | **Cilium + CoreDNS のみ** |

control-plane の taint を維持するのは、control-plane が 1 台になるとワークロードとのリソース競合の影響がクラスタ全体に及ぶため。Cilium / CoreDNS には既に toleration がある。

### Ansible role の修正

| 箇所 | 変更 |
|---|---|
| `roles/k3s/tasks/configure.yaml` | `primary_control_node_ip` が `br-node1` を文字列でハードコードしている。`primary` グループから導出する形に変更し、ホスト名変更に追従させる |
| `roles/k3s/templates/config.yaml.master.j2` | `cluster-init: true` を削除。`etcd-expose-metrics: true` も etcd 専用なので削除。`tls-san` から VIP を削除 |
| `roles/k3s/tasks/install_master.yaml` | secondary 分岐はそのまま残置する (未使用パスになるが、将来 HA に戻す際に必要) |
| `playbooks/setup_node.yaml` | Play 2 の bootstrap から kube-vip を除外 |
| `roles/k3s/tasks/post_setup.yaml` | kubeconfig の primary + WAN fallback という構造は維持。host が `172.22.52.100` になる |

### kube-vip 撤去の影響

`kube-vip` は 2 つの役割を持っていた。

| 役割 | 撤去後 |
|---|---|
| API VIP (`cp_enable` / `vip_arp`) | 不要。control-plane が 1 台なので API エンドポイントは実 IP |
| Service LB の ARP 広告 (`svc_enable`) | `CiliumL2AnnouncementPolicy default-l2-announcement-policy` (`loadBalancerIPs: true`, `interfaces: ^eth0$`) と Cilium の `l2announcements.enabled: true` が担う |

Cilium は `k8sServiceHost: 127.0.0.1` / `k8sServicePort: 6444` (k3s 内蔵のロードバランサ経由) で API に到達しているため、VIP の消滅による影響を受けない。

ただし `docs/platform/networking.md` には、2026-04-25 に `svc_enable: false` で LB IP がどこからも ARP されず `grafana.b8m.app` が 502 になった経緯が記録されている。**Cilium L2 Announcement 単独で LB IP が ARP されることは、構築手順の中で明示的に検証する** ([検証](#移行手順) の項番 9)。

### プラットフォーム構成

| 撤去 (16) | 存続 (13) |
|---|---|
| `alloy`, `alloy-cp`, `alloy-events` | `cilium`, `coredns` |
| `opentelemetry-collector`, `hubble-flow-exporter` | `cert-manager`, `external-secrets`, `onepassword-connect` |
| `kube-prometheus-stack`, `grafana`, `loki`, `tempo` | `flux-operator` |
| `longhorn`, `csi-external-snapshotter` | `envoy-gateway`, `cloudflared`, `external-dns-cloudflare` |
| `cloudnative-pg` (+ `platform-pg` クラスタ) | `zitadel` (+ `zitadel-terraform`) |
| `kube-vip`, `kured` | `metrics-server`, `system-upgrade-controller` |
| `external-dns-coredns` | `argo-workflows` (Argo Events を除く) |
| Argo Events (`EventBus` / `EventSource` / `Sensor`) | |

#### オブザーバビリティの全撤去

Loki / Tempo / Prometheus / Grafana と、その収集側 (Alloy 3 種 / OpenTelemetry Collector / Hubble Flow Exporter) を**クラスタ内から全て撤去する**。収集側だけ残して `br-observability1` に送る案は採らない。オブザーバビリティ基盤は一から作り直す方針で、`br-observability1` は本 proposal では OS だけ入った空のホストとする。

#### Argo Workflows は存続、Argo Events は撤去

ジョブネット用途 (DAG による依存関係、並列ファンアウト、cron、リトライ) は継続して必要とする。ただし常駐コストの大半は Argo Events (NATS JetStream × 3 レプリカ + コントローラ群) が占めており、Workflows 本体 (`workflow-controller` + `argo-server`) は軽い。

Argo Events の実利用は 2 つだけで、いずれも代替可能または未使用。

| リソース | 用途 | 撤去後 |
|---|---|---|
| `EventSource(workflow)` + `Sensor(notify-discord)` | Workflow の完了 / 失敗を Discord へ通知 | `WorkflowTemplate notify-discord` は残すので、`workflowDefaults.hooks.exit.templateRef` から呼ぶ形に移行する。全 Workflow への一括適用という当初の狙いはそのまま満たせる。`spec.onExit` は同一 Workflow 内の template 名しか取れず `WorkflowTemplate` を参照できないため、`LifecycleHook` の `templateRef` を使う。**実装時に使用する Argo Workflows のバージョンで `workflowDefaults` 経由の `hooks.exit.templateRef` が効くことを検証する** |
| `EventSource(webhook)` + `Sensor(sample-webhook)` | HTTP トリガのリファレンス実装 | 外部公開しておらず実利用がないため削除。将来 HTTP トリガが必要になった時点で Argo Events を戻すか、別手段を検討する |

これにより Argo Workflows 側の PVC 依存 (`EventBus` の JetStream 永続化) も消える。

代替候補も検討したが、いずれも「軽さ」で Argo Workflows 単体を上回らなかった。

| 候補 | 不採用の理由 |
|---|---|
| 素の `CronJob` / `Job` | ジョブ間の依存関係 (A → B/C 並列 → D) を表現できず、要件を満たさない |
| Tekton | CI 寄りの設計で、コントローラ数・CRD 数ともに Argo より多い |
| Kestra | JVM の常駐メモリが Pi に対して重い |
| Prefect / Dagster | k8s ネイティブでなく、サーバー + ワーカー + PostgreSQL の常駐が Argo より重い |
| Windmill (`br-ai1` に k3s 外で配置) | クラスタのリソースを使わない利点はあるが、Flux の GitOps 管理外に運用対象が増え、既存の `WorkflowTemplate` / `CronWorkflow` を全て書き直すことになる |

#### PostgreSQL のクラスタ外移設

`cloudnative-pg` と `platform-pg` クラスタを撤去し、`br-db1` の PostgreSQL に移す。

| 利用者 | 現行 | 新 |
|---|---|---|
| Zitadel | `platform-pg-rw.platform-pg.svc.cluster.local:5432` | `rdbms.prod.internal-service.bright-room.net:5432` |
| Argo Workflows (workflow archive) | 同上 | 同上 |

##### `br-db1` の TLS と接続許可

接続先には IP ではなく `rdbms.prod.internal-service.bright-room.net` を使う ([ドメイン設計](#ドメイン設計))。証明書の SAN に生の IP を入れずに済み、将来 PostgreSQL を別ホストに移しても `service_records` の 1 行を書き換えるだけで済む。

証明書は `certbot` role を `br-db1` にも適用し、`br-storage1` と同じ DNS-01 (Cloudflare) の経路で発行する。`prod.internal-service.bright-room.net` は gateway1 の CoreDNS が権威で公開 DNS には A レコードが無いが、**DNS-01 チャレンジは `bright-room.net` ゾーンに TXT を置くだけなので発行できる** (環境名を挟んでラベルが深くなっても DNS-01 なら制約を受けない)。これは「案 A の `services` リストがホストをまたいで組み合わせられる」ことの実例でもある。

DB の認証情報は **1Password の `postgresql` アイテム 1 つ**を唯一の出所とする (フィールド `zitadel_password` / `argo_workflows_password`)。Ansible の `postgresql` role がそこからロールを作り、k3s 側の Zitadel / Argo Workflows は同じアイテムを ExternalSecret 経由で読む。CNPG が Secret を自動生成していた経路が無くなるため、生成元と参照元を 1 箇所に揃える。

`pg_hba.conf` は `172.22.52.0/24` からの `hostssl` を許可する。Cilium は Pod の外向き通信をノード IP に masquerade するため、`br-db1` から見た接続元は Pod CIDR (`10.42.0.0/16`) ではなく `br-cluster2` / `br-cluster3` のノード IP (`172.22.52.101` / `.102`) になる。

**既存データは移行せず、両方とも空の DB から作り直す。** Zitadel は初期セットアップからやり直し、`br-cluster-zitadel-terraform` は state を作り直して apply する。ユーザーの再登録・MFA の再設定・1Password 側の client secret 更新が発生する。

#### ストレージ

Longhorn と `csi-external-snapshotter` を撤去する。Argo Events の撤去により PVC 利用者がゼロになるため、代替の StorageClass は導入しない。k3s config の `disable: local-storage` は現状のまま維持する。

Garage (`br-storage1`) のバケットは Argo Workflows の artifact 用 `argo-workflows` の 1 つだけで開始する (Loki / Tempo のバケットは作らない)。

#### external-dns-coredns / internal-gateway の撤去

`external-dns-coredns` は、Gateway API のリソースから LAN 内向けの `*.cluster-internal.bright-room.net` を gateway1 の CoreDNS (etcd backend) に自動登録する役割だった。主な利用者はクラスタ外 → クラスタ内の通信 (Loki への push など) で、外部公開系の DNS は `external-dns-cloudflare` が別途担当している。

外部からのアクセス制限は Cloudflare Access 側で実装されており、LAN 内から直接叩く経路は不要と判断する。`internal-gateway` の HTTPRoute は Loki のもの 1 件だけで、Loki も撤去対象。

| 箇所 | 対応 |
|---|---|
| `manifests/platform/external-dns-coredns/` | ディレクトリごと削除 |
| `envoy-gateway/config/base/internal-gateway.yaml`, `internal-gateway-class.yaml`, `internal-envoy-proxy.yaml` | 削除 |
| `cluster-settings.yaml` の `INTERNAL_CLUSTER_GATEWAY_IP` | 削除 |
| gateway1 の etcd | 削除 ([gateway1 の変更](#gateway1-の変更)) |

`external-dns-cloudflare` は存続させる。cloudflared は Cloudflare Tunnel の経路を作るだけで、`<svc>.b8m.app` → `<tunnel-id>.cfargotunnel.com` の CNAME レコードは作らない (この構成の cloudflared は Envoy への catch-all 1 本で、Tunnel の public hostname 機能を使っていない)。撤去するとサービスを 1 つ公開するたびに `br-cloudflare-terraform` へ手作業で CNAME を追加することになる。

### その他の波及

| 箇所 | 対応 |
|---|---|
| `zitadel/app/base/values.yaml` の DB `Host` | `rdbms.prod.internal-service.bright-room.net` に変更。認証情報は 1Password → ExternalSecret 経由のまま |
| `zitadel/app/base/referencegrant.yaml` の longhorn 参照 | 削除 |
| `grafana/dashboards/`, `kube-prometheus-stack/rules/`, 各コンポーネントの `monitoring/` | ディレクトリごと削除 |
| `longhorn/config/` の HTTPRoute / SecurityPolicy / OIDC ExternalSecret | 削除 |
| `system-upgrade-controller` の `server-plan` / `agent-plan` | `br-node5` 固定の nodeSelector を変更。**`server-plan` は `br-cluster1` を対象にするとクラスタ全停止を伴う**ため、`k3s-upgrade.md` の Phase 1a のテスト運用は `agent-plan` (`br-cluster2` のみ) から再開する |
| `argo-workflows` の artifact エンドポイント | `object-storage.prod.internal-service.bright-room.net:3900` に変更 (ゾーン変更に追従。解決は gateway1 の静的 `hosts` ブロック) |
| `cluster-settings.yaml` | オブザーバビリティ / Argo Events 関連の変数、`INTERNAL_CLUSTER_GATEWAY_IP`、LB IP を新値に整理 |
| `policies/` (Conftest) | 変更不要。ルールは manifest の内容に対するもので、ノード構成には依存しない |

## 新規 / 再編する Ansible role

| role | 対象ホスト | 内容 |
|---|---|---|
| `garage` | `br-storage1` | 現 `roles/external` の `garage.yaml` / `garage_keys.yaml` / `garage_buckets.yaml` とテンプレートを切り出し。バケットは `argo-workflows` のみ |
| `caddy` | `br-storage1` | 現 `roles/external/tasks/caddy.yaml` を切り出し。Garage S3 の TLS 終端。テンプレートの `br-external1` 参照を `br-storage1` に、ドメイン参照を `object-storage.prod.internal-service.bright-room.net` に変更 |
| `certbot` | `br-storage1`, `br-db1` | 現 `roles/external/tasks/certbot.yaml` を切り出し。発行対象は `object-storage.prod.internal-service.bright-room.net` / `rdbms.prod.internal-service.bright-room.net`。deploy-hook の対象は storage1 が Caddy / Garage、db1 が PostgreSQL |
| `postgresql` | `br-db1` | **新規**。apt の PostgreSQL 16、`/var/lib/postgresql` に data_dir、`zitadel` / `argo_workflows` の DB とロールを作成。TLS 証明書は `certbot` role を `br-db1` にも適用して `rdbms.prod.internal-service.bright-room.net` 向けに DNS-01 で発行する ([TLS と接続許可](#br-db1-の-tls-と接続許可)) |
| `cloudflared` | `br-gateway1` | 現 `roles/external/tasks/cloudflared.yaml` を `roles/gateway` に移設 |

`roles/external` は解体して消滅する。`br-observability1` / `br-ai1` は `roles/common` のみ適用する。

## ダウンタイム特性とバックアップ方針

シングル control-plane の帰結を明示する。

| 事象 | 影響 |
|---|---|
| `br-cluster1` の再起動 | クラスタ全停止 (API・ワークロードとも) |
| `br-cluster1` の k3s upgrade | 同上。`system-upgrade-controller` の `server-plan` は計画的に実行する |
| `br-cluster1` のハード故障 | クラスタ全停止。復旧は再フラッシュ |

`kured` の撤去により、OS の再起動を調整する仕組みがクラスタ内から無くなる。`unattended-upgrades` は `unattended_upgrades_automatic_reboot: false` (`roles/common` のデフォルト) で、現在 k3s ノードは自動再起動しない設定のため、**再起動待ちのノードは手動で順番に再起動する**運用になる。`roles/external` の解体に伴い `group_vars/external.yaml` は `group_vars/standalone.yaml` に置き換え、`unattended_upgrades_automatic_reboot: true` (時刻は `04:00`) をそのまま引き継ぐ。k3s ノードは `false` のままとする。


SQLite を選択したため k3s の etcd snapshot 機構は使えない。**クラスタ状態のバックアップは取らない。** Flux による GitOps と「PVC は ephemeral」という前提のもと、`br-cluster1` が死んだ場合は再フラッシュして Flux に再構築させるのが復旧手順となる。

この構成では **git に無いものはクラスタに置かない**という制約が従来以上に強く効く。

## 前提条件 (このリポの外)

着手前に完了している必要がある。

| # | 対象 | 作業 |
|---|---|---|
| 1 | 1Password (`br-cluster-prod` vault) | `br-db1` / `br-storage1` / `br-observability1` / `br-ai1` / `br-cluster1-3` の各アイテムを新ホスト名で作成 (`hostname`, `ip_address`, `mac_address`, `admin_password`, `username`, `password`, `<name>_ssh`)。MAC は現物から引き継ぐ。**名前が変わらない `br-gateway1` も `ip_address` を `172.22.52.1` に更新する** (`generate-inventory` が 1Password から IP を読むため) |
| 2 | `br-cloudflare-terraform` | infra トンネルの private network route を `172.22.10.0/24` → `172.22.52.0/24` に変更。**未対応だと移行後に WARP から LAN に入れなくなる** |
| 3 | `br-cluster-zitadel-terraform` | state を作り直す。Longhorn / Grafana のアプリ定義を削除 (Argo Workflows の SSO 定義は維持) |
| 4 | 物理ノード | 全 8 台で `free -h` と MAC を記録。**ディスクを消す前にしかできない** |

## 移行手順

| # | 作業 | 検証 |
|---|---|---|
| 0 | 前提条件 1–4 を完了 | — |
| 1 | 本リポジトリの変更を全て入れて PR をマージ | `make check` と `make policy/test` が green |
| 2 | 旧クラスタを全台シャットダウン | — |
| 3 | `make prod/build-image` → 全台に書き込み | 各台が新 IP で DHCP を取得し ssh 可能 |
| 4 | `make prod/provision/setup-gateway` | DHCP / DNS / NTP / cloudflared が稼働。WARP から `172.22.52.0/24` に入れる |
| 5 | `make prod/provision/setup-standalone` | `br-db1` に `psql` 接続可。`br-storage1` の Garage S3 エンドポイントが TLS で応答。`br-observability1` / `br-ai1` に ssh 可 |
| 6 | `make prod/provision/setup-node` | `kubectl get nodes` が 3 台 Ready。Cilium / CoreDNS 稼働 |
| 7 | `make prod/provision/bootstrap-cluster` | 全 Kustomization が Ready |
| 8 | **LB IP の ARP 検証** | LAN 内の別ホストから `arping 172.22.52.200` が応答する。応答しない場合は Cilium L2 Announcement の設定を調査する ([kube-vip 撤去の影響](#kube-vip-撤去の影響)) |
| 9 | Zitadel 初期セットアップ → `br-cluster-zitadel-terraform` を apply | `https://auth.b8m.app` でログイン可 |
| 10 | 公開サービスの疎通確認 | `https://*.b8m.app` が Cloudflare Tunnel 経由で応答 |
| 11 | Argo Workflows の動作確認 | `CronWorkflow hello` が実行され、`workflowDefaults.hooks.exit.templateRef` 経由で Discord 通知が飛ぶ。DAG サンプルが並列実行される |

## 後続のサブプロジェクト

本 proposal はノード再編とクラスタ縮小までを範囲とする。以下は別 proposal で扱う。

| # | 範囲 | 依存 |
|---|---|---|
| B | `br-observability1` にオブザーバビリティ基盤を新規構築し、k3s からのメトリクス / ログ収集経路を設計する | 本 proposal |
| C | `br-ai1` の用途確定 (Renovate PR のマージ妥当性確認を `claude -p` で自律実行する構想など) | 本 proposal |

## 更新が必要なドキュメント

実装時に、コードを正として以下を更新する。

`docs/architecture.md`, `docs/hardware.md`, `docs/network.md`, `docs/kubernetes.md`, `docs/provisioning.md`, `docs/cli.md`, `docs/operations.md`, `docs/platform/` 配下 (`networking.md` / `storage.md` / `observability.md` / `workflows.md` / `identity.md`), `docs/runbooks/k3s-upgrade.md`, `README.md`, `CLAUDE.md`, `docs/assets/` の図 (`hardware-topology.svg`, `networking-external.svg`, `networking-internal.svg`, `networking-dependency.svg`)。

`docs/proposals/observability-plan-remaining.md` はオブザーバビリティ全撤去により内容が失効するため、サブプロジェクト B の起点として書き直すか削除する。

ドメイン変更は `br-cloudflare-terraform` の Access アプリ定義には影響しない (`b8m.app` 側は現状維持)。
