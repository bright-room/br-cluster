# 物理構成

br-cluster の物理ハードウェアと、各ノードが担う役割をまとめる。

## ノード一覧

サーバー定義は [`servers.yaml`](../servers.yaml) が唯一の情報源。ここに無いノードは存在しない。

| ホスト名       | 役割     | k3s role  | storage_mode | データ用マウント       | 用途 |
|----------------|----------|-----------|--------------|------------------------|------|
| `br-gateway1`  | gateway  | —         | `none`       | —                      | LAN の DHCP / DNS / NTP、WAN ↔ LAN ルーティング (nftables) |
| `br-external1` | external | —         | `ext4`       | `/storage`             | Garage S3 (loki/tempo バックエンド)、Caddy、Let's Encrypt |
| `br-node1`     | node     | primary   | `none`       | —                      | k3s 制御プレーン (プライマリ)、etcd |
| `br-node2`     | node     | secondary | `none`       | —                      | k3s 制御プレーン、etcd |
| `br-node3`     | node     | secondary | `none`       | —                      | k3s 制御プレーン、etcd |
| `br-node4`     | node     | worker    | `ext4`       | `/var/lib/longhorn`    | k3s ワーカー、Longhorn レプリカ |
| `br-node5`     | node     | worker    | `ext4`       | `/var/lib/longhorn`    | k3s ワーカー、Longhorn レプリカ |
| `br-node6`     | node     | worker    | `ext4`       | `/var/lib/longhorn`    | k3s ワーカー、Longhorn レプリカ |

## ネットワークトポロジ

クラスタは自宅 LAN とは別の **専用サブネット (172.22.10.0/24)** を持ち、`br-gateway1` がその境界ルーターを兼ねる。日常使いの端末 (MacBook / Windows PC / スマホ) は自宅ルーター配下の通常 LAN に居て、クラスタへは `br-gateway1` 経由で到達する。

![ネットワークトポロジ](assets/hardware-topology.svg)

`br-gateway1` だけが 2 系統 (LAN: `eth0` / WAN: `wlan0`) を持ち、残りのノードは `eth0` のみでクラスタ LAN に参加する。

## 共通ハードウェア

全ノードで共通の前提。

| 項目         | 値                                                     | 備考 |
|--------------|--------------------------------------------------------|------|
| SBC          | Raspberry Pi (ARM64)                                   | Ubuntu 24.04 preinstalled-server `arm64+raspi` ベースの Packer イメージで起動 ([`imager/source.pkr.hcl`](../imager/source.pkr.hcl)) |
| OS ストレージ | USB3 → NVMe アダプタ (Realtek RTL9210) + SSD            | UAS 無効化 quirk **必須** (下記参照) |
| ネットワーク | 有線 Ethernet `eth0` をクラスタ LAN に接続              | `br-gateway1` のみ追加で Wi-Fi `wlan0` を WAN 側に使用 |

### RTL9210 UAS quirk

RTL9210 は UAS (USB Attached SCSI) で random hang する既知問題があり、quirk 未設定のノードは高負荷時にフリーズしてクラスタごと巻き込む。**全ノードで例外なく適用する**。

| 項目     | 内容 |
|----------|------|
| 設定先   | `/boot/firmware/cmdline.txt` |
| 設定値   | `usb-storage.quirks=0bda:9210:u` |
| 設定箇所 | [`provisioner/roles/common/tasks/system.yaml`](../provisioner/roles/common/tasks/system.yaml) |

## ディスクレイアウト

`storage_mode` は [`provisioner/inventories/base/group_vars/all/main.yaml`](../provisioner/inventories/base/group_vars/all/main.yaml) のデフォルトを各ホストの `host_vars/*.yaml` で上書きする。

| storage_mode | 対象                  | OS パーティション | データパーティション | マウントオプション |
|--------------|-----------------------|-------------------|----------------------|--------------------|
| `none`       | gateway1, node1-3     | ディスク全体を `/` | なし                 | —                  |
| `ext4`       | external1, node4-6    | 先頭 64 GiB を `/` | 残り領域を ext4      | `defaults,noatime,discard,nofail` |

`storage_mode: none` の場合、制御プレーンはデータを持たない (etcd は `/var/lib/rancher/k3s` 配下で OS と同居する) ため、追加パーティションを切らない。

`storage_mode: ext4` のマウント先はホストごとに異なる。

| ホスト         | mount_point          | 用途 |
|----------------|----------------------|------|
| `br-external1` | `/storage`           | Garage の `data_dir` / `meta_dir` |
| `br-node4-6`   | `/var/lib/longhorn`  | Longhorn レプリカ |

初回 provision 時、[`provisioner/tasks/init_disk.yaml`](../provisioner/tasks/init_disk.yaml) が root を 64 GiB に縮めてから残りを ext4 で切る。`apt upgrade` の前段で実行する (順序を逆にすると dist-upgrade がディスク配置の都合で失敗する)。

## 役割別の詳細

### br-gateway1 (Gateway)

| 項目       | 内容 |
|------------|------|
| NIC        | `eth0` → クラスタ LAN (172.22.10.0/24) / `wlan0` → 自宅 Wi-Fi (WAN 側) |
| サービス   | DHCP / DNS / NTP / nftables (INPUT・FORWARD・NAT) |
| DHCP 配布  | `172.22.10.100-200` |
| DNS        | 内部ゾーン `cluster-internal.bright-room.net` を権威、他は `8.8.8.8` / `8.8.4.4` にフォワード |
| NTP        | 上流 `ntp.nict.jp` |
| 主要 NAT   | API VIP 向け DNAT + LAN → WAN Masquerade (ルール詳細は [`network.md#nat`](network.md#nat)、宛先 API VIP の意味は [`kubernetes.md#トポロジ`](kubernetes.md#トポロジ)) |
| Ansible role | [`provisioner/roles/gateway`](../provisioner/roles/gateway) + `ipr-cnrs.nftables` |
| 詳細       | [`docs/network.md`](network.md) |

### br-external1 (External)

| 項目         | 内容 |
|--------------|------|
| 役割         | クラスタ外部のオブジェクトストア + リバースプロキシ + TLS 発行 |
| Garage v2    | S3 互換、`loki` / `tempo` バケットを提供 |
| Caddy        | リバースプロキシ |
| Certbot      | Let's Encrypt (DNS01, Cloudflare)、deploy-hook で Caddy / Garage をリロード |
| 用途         | クラスタ外 Loki / Tempo のオブジェクトストア (Longhorn のバックアップ送り先ではない) |
| Ansible role | [`provisioner/roles/external`](../provisioner/roles/external) |

### br-node1-6 (k3s ノード)

物理ホストとしては全 6 台が同一構成 (Pi + RTL9210 + SSD)。違いはディスクレイアウト ([`### ディスクレイアウト`](#ディスクレイアウト)) のみ:

- `br-node1-3`: `storage_mode: none` (OS パーティションのみ、etcd は `/var/lib/rancher/k3s` 配下で OS と同居)
- `br-node4-6`: `storage_mode: ext4` で `/var/lib/longhorn` を ext4 マウント

k3s レイヤの役割 (control-plane / worker / etcd メンバー / 起動方法) は [`docs/kubernetes.md#ノード別-k3s-役割`](kubernetes.md#ノード別-k3s-役割) を参照。

## 関連

- [`docs/network.md`](network.md) — IP 設計・ファイアウォール・DNS
- [`docs/provisioning.md`](provisioning.md) — Packer / Ansible でノードを構築する流れ
- [`servers.yaml`](../servers.yaml) — サーバー定義 (SoT)
- [`provisioner/inventories/base/host_vars/`](../provisioner/inventories/base/host_vars) — ホスト別の上書き設定
