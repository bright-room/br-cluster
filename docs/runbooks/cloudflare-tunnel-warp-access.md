# Runbook: kubectl / SSH 外部アクセス (CF WARP private network 経由)

mac から kubectl / SSH を「LAN 内利用時と同じ hostname」で叩けるようにするための
クライアント側セットアップと疎通確認手順。

設計詳細: [`docs/proposals-done/cloudflare-tunnel-warp-private-network.md`](../proposals-done/cloudflare-tunnel-warp-private-network.md)

cloudflared (br-infra Tunnel) は `br-gateway1` 上で稼働し、LAN 全体 (`172.22.52.0/24`) への経路を提供する ([`docs/platform/networking.md#cloudflared`](../platform/networking.md#cloudflared))。

## 前提

- mac の WARP が `bright-room.cloudflareaccess.com` team に enroll 済
- WARP の Custom Profile (`br-cluster admin (WARP private network)`) が
  自端末に適用されている (`warp-cli settings` で Split Tunnel に
  `172.22.52.0/24` が含まれること)
- `/etc/resolver/prod.br-cluster.bright-room.net` は **削除済**
  (これがあると WARP の Local Domain Fallback より優先されてしまう)

## kubectl

### 事前セットアップ

WARP / CF 障害時のフォールバック用に WAN DNAT 直叩きの context を追加する。

```bash
# 既存 default cluster の CA を取り出す
CA=$(kubectl config view --raw -o jsonpath='{.clusters[?(@.name=="default")].cluster.certificate-authority-data}')

# WAN フォールバック cluster を追加
# tls-server-name で SAN 一致を強制 (192.168.2.50 は cert SAN にない)
kubectl config set-cluster default-wan --server=https://192.168.2.50:6443
kubectl config set clusters.default-wan.certificate-authority-data "$CA"
kubectl config set clusters.default-wan.tls-server-name k8s-api.prod.internal-service.bright-room.net

# context 追加 (user は default を流用)
kubectl config set-context default-wan --cluster=default-wan --user=default
```

WAN フォールバックは家 LAN からのみ許可されている (CF/WARP 障害時の想定であり、任意の WAN からは通らない。[`docs/network.md#forward-経路許可`](../network.md#forward-経路許可))。

### 確認手順

| # | 環境 | コマンド | 期待結果 | 経路 |
|---|------|---------|---------|------|
| 1 | 家 Wi-Fi + WARP on | `kubectl get nodes` | 3 nodes 返却 | WARP → gateway1 cloudflared → k8s API (`br-cluster1` 実 IP) |
| 2 | 家 Wi-Fi + WARP off | `kubectl get nodes` | DNS 引けず失敗 (仕様) | — |
| 3 | 家 Wi-Fi + WARP off | `kubectl --context default-wan get nodes` | 3 nodes 返却 | WAN DNAT (192.168.2.50:6443、家 LAN 発のみ) |
| 4 | tether + WARP on | `kubectl get nodes` | 3 nodes 返却 | WARP → gateway1 cloudflared → k8s API (`br-cluster1` 実 IP) |
| 5 | tether + WARP off | `kubectl get nodes` | 失敗 (仕様、諦める) | — |
| 6 | tether + WARP off | `kubectl --context default-wan get nodes` | 失敗 (家 LAN 外からの WAN DNAT は許可されない、仕様) | — |

## SSH

### 事前セットアップ

`~/.ssh/config` を 2 箇所変更:

**(1) 既存 `Host br-gateway1` の HostName を WARP 経由解決の hostname に変更**

```diff
 Host br-gateway1
-  HostName 192.168.2.50
+  HostName gateway1.prod.br-cluster.bright-room.net
   Port 22
   User bradmin
```

これだけで `br-db1` / `br-storage1` / `br-observability1` / `br-ai1` / `br-cluster1-3` も連動して動く
(ProxyJump br-gateway1 経由なので、jump host が WARP 経由になれば全部解決)。

**(2) WAN フォールバック用の gateway1 エントリを追加**

```ssh-config
Host br-gateway1-wan
  HostName 192.168.2.50
  Port 22
  User bradmin
```

他ホストの `*-wan` エントリは作らない (障害時は `ssh -J br-gateway1-wan br-cluster2` のように都度 jump host 上書き。かつ WAN 直の ssh 転送は家 LAN 発のみ許可)。

### 確認手順

| # | 環境 | コマンド | 期待結果 | 経路 |
|---|------|---------|---------|------|
| 1 | 家 Wi-Fi + WARP on | `ssh br-gateway1` | login 成功 | WARP → gateway1 |
| 2 | 家 Wi-Fi + WARP on | `ssh br-cluster2` | login 成功 | WARP → gateway1 → ProxyJump → cluster2 |
| 3 | 家 Wi-Fi + WARP off | `ssh br-gateway1` | DNS 引けず失敗 (仕様) | — |
| 4 | 家 Wi-Fi + WARP off | `ssh br-gateway1-wan` | login 成功 | WAN DNAT (192.168.2.50:22、家 LAN 発のみ) |
| 5 | 家 Wi-Fi + WARP off | `ssh -J br-gateway1-wan br-cluster2` | login 成功 | WAN DNAT 経由で gateway1 → ProxyJump → cluster2 |
| 6 | tether + WARP on | `ssh br-gateway1` | login 成功 | WARP → gateway1 |
| 7 | tether + WARP on | `ssh br-cluster2` | login 成功 | WARP → gateway1 → ProxyJump → cluster2 |
| 8 | tether + WARP off | `ssh br-gateway1` | 失敗 (仕様) | — |
| 9 | tether + WARP off | `ssh br-gateway1-wan` | 失敗 (家 LAN 外からの WAN DNAT は許可されない、仕様) | — |

## 補足: なぜ /etc/resolver を消す必要があったか

macOS の per-domain resolver (`/etc/resolver/<domain>`) は WARP の Local Domain
Fallback より優先順位が高い。`/etc/resolver/prod.br-cluster.bright-room.net` が
nameserver に `192.168.2.50` (gateway1 wlan0) を指していると:

- 家 Wi-Fi: 192.168.2.50 へ DNS query → CoreDNS 応答 → kubectl 接続 (これで動いていた)
- tether: 192.168.2.50 が unreachable → WARP の Fallback Domain にフォールスルーせず timeout

これが「kubectl が `192.168.2.50:6443` に dial して timeout」の正体。
削除したことで WARP private network 経由 (`prod.br-cluster.bright-room.net` →
WARP Local Domain Fallback → `172.22.52.1` CoreDNS → `br-cluster1` 実 IP) が機能するようになる。

## TODO

- mac の `~/.ssh/config` は Ansible で自動生成されるファイルなので、上記 SSH 設定変更は
  生成元 (`provisioner/` 内のテンプレート) を直す PR を別途切る必要がある。
  当面は手で書き換え + 次回 ansible run 後に再書き換え。
- `docker/ssh/config` (ansible-runner 用) も同じ問題を抱えているので
  同様に直す (家 Wi-Fi で ansible 実行している限りは支障なし)
