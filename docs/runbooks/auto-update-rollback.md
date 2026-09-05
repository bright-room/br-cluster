# Runbook: Ubuntu auto-update regression からの rollback

`unattended-upgrades` が security pocket から取り込んだパッケージで regression を踏んだ
ときの rollback 手順。

設計の経緯と Phase 1 着地状況は [`docs/proposals/ubuntu-auto-update.md`](../proposals/ubuntu-auto-update.md)
を参照。本 runbook は **「壊れた状態を観測してから安定状態に戻すまで」** に絞る。

## 前提

| 項目 | 値 |
|------|-----|
| 自動更新の仕組み | `unattended-upgrades` (security pocket only) |
| 適用対象 | 全 host (`br-gateway1` / `br-db1` / `br-storage1` / `br-observability1` / `br-ai1` / `br-cluster1-3`) |
| reboot オーケストレーション | standalone ホスト (`br-db1` / `br-storage1` / `br-observability1` / `br-ai1`) と `br-gateway1` は uu 自身が自動再起動 (gateway 03:00 / standalone 04:00 JST)。**k3s ノード (`br-cluster1-3`) は自動再起動しない** (kured 撤去済み) — 再起動待ちのノードは手動で順番に再起動する運用 |
| 通知 | Discord `#br-cluster-prod-maintenance` (uu hook) |
| ログ場所 | `/var/log/unattended-upgrades/unattended-upgrades.log` (各ホスト) |

## 0. regression 検知の入口

| 入口 | 何を見るか |
|------|----------|
| Discord 通知 | uu hook の embed 色 (赤 = エラー終了) |
| 手動気付き | サービスが動かない、`kubectl get nodes` で `NotReady`、特定コマンドが失敗するなど |

## 1. 影響範囲の特定

regression を踏んでいるホストとパッケージを切り分ける。

```sh
# 該当ホストにログイン (例: br-cluster2)
ssh br-cluster2

# 当日 / 直近の uu 適用ログ
sudo less /var/log/unattended-upgrades/unattended-upgrades.log

# どのパッケージがいつ上がったかの履歴
grep " upgrade " /var/log/dpkg.log /var/log/dpkg.log.1 | tail -50

# 直近 24h 以内のアップグレード一覧
zgrep -h " upgrade " /var/log/dpkg.log* \
  | awk -v d="$(date -d '24 hours ago' '+%Y-%m-%d %H:%M:%S')" '$1" "$2 >= d' \
  | awk '{print $4, $5, "->", $6}'
```

特定したパッケージ名 / 旧バージョン / 新バージョンを **メモ**。複数ホストに同じ問題が出ているなら、全ホストで同じ rollback を打つ。

## 2. 緊急停止: 自動更新を一時的に止める

rollback 中に同じパッケージが再投入されないよう、まず自動更新を止める。

### 全ホストで止める (Ansible 経由)

ad-hoc で timer を止める。`make` ターゲットは無いので直接 ansible-playbook か `-m systemd` で叩く。

```sh
# inventory は provisioner/inventories/prod を使う
cd provisioner

uv run ansible all -i inventories/prod -m systemd -b \
  -a 'name=apt-daily-upgrade.timer state=stopped enabled=no'
uv run ansible all -i inventories/prod -m systemd -b \
  -a 'name=apt-daily.timer state=stopped enabled=no'
```

### 1 ホストだけ止める

```sh
ssh br-cluster2 sudo systemctl stop apt-daily-upgrade.timer apt-daily.timer
ssh br-cluster2 sudo systemctl disable apt-daily-upgrade.timer apt-daily.timer
```

k3s ノードは自動再起動しない設定 (`unattended_upgrades_automatic_reboot: false`) のため、kured のような reboot オーケストレーションを別途止める手順は不要。

## 3. パッケージを旧バージョンに戻す

`/var/log/dpkg.log` で確認した旧バージョンを `apt install pkg=ver` で固定する。

```sh
# 例: openssl が 3.0.2-0ubuntu1.18 → 3.0.2-0ubuntu1.19 で壊れた場合
ssh br-cluster2
sudo apt install --allow-downgrades openssl=3.0.2-0ubuntu1.18

# 以後 unattended-upgrades が再度上書きしないように hold
sudo apt-mark hold openssl
```

複数パッケージなら `apt install --allow-downgrades pkg1=ver1 pkg2=ver2 ...` で同時指定。

ホストキャッシュに旧 .deb が残っていない場合は archive から取得:

```sh
# Launchpad の package archive 例 (Ubuntu)
# https://launchpad.net/ubuntu/+source/<source-pkg>/<version>
# 該当 .deb を wget して dpkg -i <file>.deb
```

複数ホストに同じ rollback を打つときは Ansible ad-hoc:

```sh
uv run ansible all -i inventories/prod -m apt -b \
  -a 'name=openssl=3.0.2-0ubuntu1.18 allow_downgrade=yes state=present'
uv run ansible all -i inventories/prod -m dpkg_selections -b \
  -a 'name=openssl selection=hold'
```

## 4. 動作確認

| 対象 | 確認 |
|------|------|
| パッケージバージョン | `dpkg -l <pkg>` または `apt-cache policy <pkg>` で固定済を確認 |
| サービス | regression を踏んだサービスを restart して挙動確認 |
| k3s ノード | `kubectl get nodes` で Ready / `kubectl get pods -A` で異常 Pod を確認 |

## 5. 自動更新の再開

regression パッケージは hold したまま、timer は再開する。

```sh
uv run ansible all -i inventories/prod -m systemd -b \
  -a 'name=apt-daily.timer state=started enabled=yes'
uv run ansible all -i inventories/prod -m systemd -b \
  -a 'name=apt-daily-upgrade.timer state=started enabled=yes'
```

## 6. 恒久対応

| 状況 | 対応 |
|------|------|
| 同じ regression が upstream で fix された | hold を解除 (`apt-mark unhold <pkg>`) して通常運用に戻す |
| upstream で fix が出ない / 長引く | hold を維持。proposal の Phase 2 タスクに「特定パッケージの hold 一覧管理」を追加 |
| `unattended-upgrades` の挙動自体に不満 | `provisioner/roles/common/tasks/unattended_upgrades.yaml` の `Unattended-Upgrade::Allowed-Origins` から該当 origin を一時的に外す。**長期的には outage のレポートを upstream に上げる** |

## 7. 事後

- regression の概要と踏んだホスト / パッケージを `docs/proposals/ubuntu-auto-update.md` の更新履歴に追記 (Phase 2 観察結果として参考になる)
- 影響が大きかった場合は postmortem を別 doc に切る
- Phase 2 タスク #3 (uu 通知の頻度見直し) や、tasks/update.yaml の整理判断材料として残す

## 関連 doc

- 設計: [`docs/proposals/ubuntu-auto-update.md`](../proposals/ubuntu-auto-update.md)
- uu Ansible role: [`provisioner/roles/common/tasks/unattended_upgrades.yaml`](../../provisioner/roles/common/tasks/unattended_upgrades.yaml)
