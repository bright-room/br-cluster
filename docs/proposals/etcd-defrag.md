# 提案: k3s embedded etcd の定期 defrag 導入

> **この提案の位置づけ (重要)**
>
> 本提案は **「CP メモリ圧迫の主因が etcd db の mmap 膨張であると計測で確認できた場合」
> の対処法** である。初版ではこの前提を k9s の pod メモリ差分から推定だけで決め打ちして
> いたが、2 回目のレビューで **kube-apiserver の watch cache / informer が真犯人である
> 可能性**が指摘され、再検証が必要と判明した。
>
> したがって **Phase 0 (計測・犯人特定) を完了するまで実装には進まない**。計測結果に
> よっては本提案は優先度を下げ、apiserver チューニングを先行する可能性がある。

## 背景

- CP ノード (br-node1〜3, Pi 3GB allocatable) のメモリ使用率が高い
  - br-node1: 71% (2201 / 3084 MiB) ← leader と思われる
  - br-node2: 54%
  - br-node3: 50%
- CPU は 11-14% で余裕あり。**メモリが単独のボトルネック**
- CP 上で動いている pod の合計は ~200 MiB 程度
  - node-exporter / cilium / cilium-envoy / cilium-operator(node1 のみ) / coredns / kube-vip
- 残り ~2 GB は **k3s server プロセス (apiserver + controller-manager + scheduler + kubelet) と embedded etcd 本体** — **ただしこの内訳は未計測**。
  kube-apiserver の watch cache / informer が支配的である可能性も高く、
  Phase 0 で切り分けが必要
- `kube-prometheus-stack` は既に `node_type: worker` で CP から退避済み (incident-2026-04-13 対応)。Prometheus 系の退避はもう効かない
- 全ノード SSD。IO は問題なし

## Phase 0: 計測・犯人特定 (実装前必須)

Phase 0 を飛ばして defrag に進むと、真犯人が apiserver だった場合に効果ゼロで終わる。
必ず以下を実行し、結果を記録してから Phase 1 (実装) の採否を決める。

### 0-1. プロセス別 RSS (全 CP ノード)

```bash
# 各 CP で実行
ssh br-node1 'sudo ps -eo pid,rss,comm --sort=-rss | head -20'
ssh br-node2 'sudo ps -eo pid,rss,comm --sort=-rss | head -20'
ssh br-node3 'sudo ps -eo pid,rss,comm --sort=-rss | head -20'
```

期待される主要プロセス:
- `k3s-server` — 中で kube-apiserver / controller-manager / scheduler / kubelet /
  embedded etcd が動く。k3s はこれらを単一プロセスに同居させるので、プロセス RSS
  だけでは内訳が取れない点に注意
- `containerd`
- `kubelet` (k3s では k3s-server 配下)

### 0-2. k3s-server 内部の内訳取得

k3s の単一プロセス構造を越えて内訳を見るには以下のいずれか:

```bash
# k3s 内の etcd メトリクス (プロセス RSS ではなく etcd が持つ内部 stats)
ssh br-node1 'sudo /usr/local/bin/etcdctl \
  --cacert=/var/lib/rancher/k3s/server/tls/etcd/server-ca.crt \
  --cert=/var/lib/rancher/k3s/server/tls/etcd/server-client.crt \
  --key=/var/lib/rancher/k3s/server/tls/etcd/server-client.key \
  --endpoints=https://127.0.0.1:2379 \
  endpoint status --write-out=table'
```

上記の DB SIZE と DB SIZE IN USE の差分が defrag で回収できる上限値。

```bash
# db ファイル実サイズ
ssh br-node1 'sudo ls -lh /var/lib/rancher/k3s/server/db/etcd/member/snap/db'
```

```bash
# kube-apiserver の /metrics から watch cache サイズを見る
# (kubectl get --raw で /metrics を取得し、apiserver_cache_list_total,
#  etcd_db_total_size_in_bytes 等を確認)
kubectl get --raw /metrics | grep -E 'etcd_db_total_size|apiserver_cache'
```

### 0-3. 判定基準

| 計測結果                                    | Phase 1 の進め方                     |
| ------------------------------------------- | ------------------------------------ |
| DB SIZE と DB SIZE IN USE の差が **500MB+** | 本提案 (defrag) を予定通り実装       |
| 差が **100MB 未満**                         | defrag の効果は小さい。apiserver チューニング (下記 "代替/併用候補") を先行 |
| DB SIZE 自体が **数十 MB レベル**           | etcd は無関係。apiserver / informer 側が真犯人。本提案は保留 |

## 提案: etcd defrag を systemd timer で定期実行

### なぜ CronJob ではなく systemd timer か

- CronJob だと hostNetwork + cert hostPath mount + 3 ノード順次実行の調整が必要で複雑
- この repo は CP を Ansible で provisioning しているので、ロールとして自然に収まる
- etcd バイナリ (etcdctl) もノードに置いておけば systemd unit から直接叩ける

### compaction と defrag の関係

k3s embedded etcd はデフォルトで auto-compaction を有効化している
(`--auto-compaction-mode=periodic`, `--auto-compaction-retention=5m` 相当)。
しかし **compaction は revision の論理削除であり、mmap 上の物理ファイルサイズは縮まない**。
物理的にファイルを縮めるには `etcdctl defrag` が別途必要。
Pi の限られたメモリでは etcd db の mmap 膨張が CP メモリ圧迫の一因になりやすい。

## 構成要素

### (A) k3s config.yaml への etcd-arg 追記

**初版から変更**: `quota-backend-bytes` と `auto-compaction-retention=1h` を削除した。

- `quota-backend-bytes=2147483648` は etcd のデフォルト値そのものなので明示する
  意味がない (混乱の元になる)
- `auto-compaction-retention` を 5m (k3s デフォルト) から 1h に伸ばすと、
  **compaction まで保持する履歴が増え db が肥大化する** — メモリ削減の目的に逆行する

結果として Phase 1 時点では k3s config.yaml の変更は **不要**。
(現状の k3s デフォルトで auto-compaction は効いているので、defrag を足すだけでよい)

計測の結果、compaction 頻度を上げたい特別な理由が出てきた場合のみ、
個別に検討する。

### (B) defrag 実行スクリプト

`/usr/local/sbin/k3s-etcd-defrag.sh` を Ansible で配布:

```bash
#!/usr/bin/env bash
set -euo pipefail

CERT_DIR=/var/lib/rancher/k3s/server/tls/etcd
ENDPOINTS=https://127.0.0.1:2379
ETCDCTL=/usr/local/bin/etcdctl

etcdctl_local() {
  "${ETCDCTL}" \
    --cacert="${CERT_DIR}/server-ca.crt" \
    --cert="${CERT_DIR}/server-client.crt" \
    --key="${CERT_DIR}/server-client.key" \
    --endpoints="${ENDPOINTS}" \
    --command-timeout=30s \
    "$@"
}

# health チェック。落ちてる etcd を defrag しようとして事故るのを避ける
if ! etcdctl_local endpoint health >/dev/null 2>&1; then
  echo "local etcd endpoint unhealthy; skipping defrag" >&2
  exit 0
fi

# defrag 実行。leader/follower 問わず全ノードで回す (後述の設計根拠を参照)
etcdctl_local defrag --command-timeout=120s
```

設計上のポイント:

- **ローカル endpoint のみ** (`127.0.0.1:2379`) — 各 CP は自分の etcd だけを
  defrag する。systemd timer の `RandomizedDelaySec` と合わせて、3 ノード同時
  defrag によるクラスタ停止を回避
- **leader もスキップしない** — 初版では leader をスキップする設計にしていたが、
  以下の理由で撤回:
  - k3s の raft leader は一度決まるとめったに交代しない。現状 br-node1 が
    71% と突出しているのは、恐らく長時間 leader を務めて etcd db mmap が
    最大に膨らんでいるため。**一番 defrag したいノードが永久にスキップされる**
    設計になってしまう
  - HomeLab + 深夜 4 時実行の前提なら、leader defrag による書き込み数秒停止は
    実害なし (OnCalendar=Sun 04:00 で稼働していない時間帯)
  - step-down で leader 移動させる案もあるが、`etcdctl move-leader` の
    実装コストと raft 再選挙中の不安定化リスクに見合わない
- **health チェック** — defrag 前に `endpoint health` で自ノードの etcd が
  健全か確認。落ちている状態で defrag を叩くと更に状態が悪化する可能性を排除
- **エラー伝搬** — `set -euo pipefail` + `etcdctl_local` 関数化で、証明書や
  endpoint の問題で status/defrag が失敗した場合はスクリプト全体が非ゼロ終了。
  systemd journal にそのまま残る

### (C) systemd unit + timer

```ini
# /etc/systemd/system/k3s-etcd-defrag.service
[Unit]
Description=k3s embedded etcd defrag
After=k3s.service
Requires=k3s.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/k3s-etcd-defrag.sh
```

```ini
# /etc/systemd/system/k3s-etcd-defrag.timer
[Unit]
Description=Weekly k3s etcd defrag

[Timer]
OnCalendar=Sun *-*-* 04:00:00
RandomizedDelaySec=30m        # 3 ノードで実行時刻をバラす
Persistent=true

[Install]
WantedBy=timers.target
```

`RandomizedDelaySec=30m` で 3 ノードの実行時刻が 30 分幅でずれる。
同一ウィンドウで 3 ノード全部 defrag が走るのを避けるためのリスクヘッジ。

### (D) etcdctl バイナリ

k3s は etcdctl をバンドルしない。Ansible で別途配布が必要:

- https://github.com/etcd-io/etcd/releases から arm64 の tar.gz を取得
- `ansible.builtin.unarchive` で `etcdctl` バイナリだけを `/usr/local/bin/` に展開
  (mode 0755)
- バージョンは k3s が同梱する etcd のバージョンに合わせるのが安全
  (`k3s --version` または `kubectl -n kube-system get pod` の etcd イメージタグで確認)
- チェックサム検証を入れる (`get_url` の `checksum` パラメータ)

## 実行順序と検証手順

1. **現状計測** — まず defrag の余地があるかを確認する:
   ```
   ssh br-node1 'sudo ls -lh /var/lib/rancher/k3s/server/db/etcd/member/snap/db'
   ssh br-node2 'sudo ls -lh /var/lib/rancher/k3s/server/db/etcd/member/snap/db'
   ssh br-node3 'sudo ls -lh /var/lib/rancher/k3s/server/db/etcd/member/snap/db'
   ```
   この db ファイルが defrag で縮む対象
2. **1 ノード手動実行** — br-node2 か br-node3 (follower 側) で手動実行:
   ```
   sudo /usr/local/sbin/k3s-etcd-defrag.sh
   ```
3. **前後比較** — db ファイルサイズと、k9s で etcd プロセスの RSS を観察
4. **問題なければ Ansible role 化** — `provisioner/roles/k3s_etcd_defrag/` として
   tasks/files/templates を整備
5. **3 ノード展開** — role 適用後、timer を enable

## 期待効果

- 上限は **Phase 0 で測った `DB SIZE` − `DB SIZE IN USE` の差** に等しい。
  それ以上は絶対に回収できない
- ただし **defrag で db ファイルが縮んでも、プロセス RSS が即同じだけ減るとは
  限らない**:
  - etcd は mmap で db を読んでいる。ファイル truncate 後、該当ページは
    カーネルにより段階的に解放されるが、タイミングは OS 依存
  - RSS として観測できる減少は、ファイル縮小分より遅れて・少なめに出ることがある
  - 真の効果を測るには defrag 前後で `ps -o rss` と `ls -l db` の両方を記録する
- **楽観シナリオ**: db ファイル -500MB, RSS -300MB/node, br-node1 71% → 60% 前後
- **悲観シナリオ**: db ファイルはそれなり縮むが RSS はほぼ動かず、体感改善なし
  → この場合は apiserver チューニングに舵を切る

## 代替 / 併用候補 (Phase 0 の結果次第で優先度が上がる)

Phase 0 で「etcd ではなく apiserver が主因」と判明した場合、こちらを先行する。

### kube-apiserver watch cache チューニング

k3s の config.yaml に以下を追加 (k3s は `kube-apiserver-arg` で透過的に渡せる):

```yaml
kube-apiserver-arg:
  - "target-ram-mb=1500"              # apiserver が内部キャッシュサイズを
                                       # この値を目安に自動調整
  - "default-watch-cache-size=50"     # デフォルト 100 を半減
```

注意:

- **`watch-cache=false` は絶対に設定しない**。watch cache を無効化すると
  list 系リクエストが全部 etcd に直撃し、etcd 負荷が爆発する。一部のレビューで
  この助言が見られるが採用しない
- `target-ram-mb` は kube-apiserver の watch cache と event cache サイズを
  この値から自動算出するヒント値。Pi CP ノード (3GB) なら 1500 前後が安全圏
- 効果があれば apiserver のキャッシュ RSS が数百 MB 単位で減ることがある

### Prometheus の apiserver scrape 負荷削減

- apiserver `/metrics` の scrape interval を 30s → 60s に伸ばす
- 高カーディナリティメトリクス (`apiserver_request_duration_seconds_bucket`
  の一部 verb/resource 組み合わせ等) を `metric_relabel_configs` で drop
- apiserver 側の watch cache 消費とは独立に、apiserver のリクエスト処理メモリを削る

## リスク

- **defrag 中の書き込み停止** — leader/follower 問わず、defrag 対象ノードへの
  書き込みは defrag 完了まで (通常数秒、最大で command-timeout=120s) ブロックされる。
  深夜 4:00 + `RandomizedDelaySec=30m` + 3 ノード順次なので実害は出ない想定だが、
  同時間帯に動く CronJob やバックアップ (Longhorn → Garage など) があれば
  時刻をずらす
- **etcdctl バージョン不一致** — k3s 同梱 etcd とバージョンが大きく外れると
  互換性問題が出る可能性。リリースを揃える + checksum 検証
- **Ansible role 適用タイミング** — k3s.service が起動していない状態で
  timer が発火すると service の `Requires=k3s.service` で失敗する
  (OnFailure で騒がない設計にするか確認)
- **3 ノード同時 defrag** — `RandomizedDelaySec=30m` で分散するが、運悪く
  近い時刻に重なる可能性はゼロではない。心配なら `OnCalendar` を
  ノードごとに別曜日にする (例: br-node1=Sun, br-node2=Mon, br-node3=Tue) 案もあり

## 作業範囲

- `provisioner/roles/k3s_etcd_defrag/` 新規作成
  - `tasks/main.yml`
  - `files/k3s-etcd-defrag.sh`
  - `templates/k3s-etcd-defrag.service.j2`
  - `templates/k3s-etcd-defrag.timer.j2`
  - etcdctl バイナリ取得タスク
- k3s config テンプレへの `etcd-arg` 追記 (A の項)
- 既存 playbook への role 組み込み

## 未決事項 / 要確認

- k3s config.yaml を生成している既存テンプレ/ロールの場所
- etcdctl のダウンロード方式 (GitHub release を直接 get_url か、事前ミラーか)
- 同時間帯に動いている CronJob / バックアップジョブの有無
  (Longhorn → Garage バックアップの実行時刻要確認)
- OnCalendar を全ノード共通 (Sun 04:00 + RandomizedDelaySec) にするか、
  ノード別の曜日に分けるか

## 更新履歴

- 2026-04-16 初版
- 2026-04-16 レビュー#1 反映:
  - leader スキップロジックを撤回 (一番膨らんでいる leader が永久に defrag
    されない問題を解消)
  - health チェックとエラーハンドリング強化
  - etcdctl 配布に checksum 検証と unarchive パターン追加
- 2026-04-16 レビュー#2 反映 (構造変更):
  - **Phase 0 (計測・犯人特定) を必須化**。etcd が主因という前提を
    未検証のまま進めていた点を是正
  - 提案冒頭に位置づけ警告を追加 — 本提案は「etcd が主因と判明した場合の一手段」
    であり、メモリ問題の単独解ではない
  - `quota-backend-bytes` (etcd デフォルト値の明示) と
    `auto-compaction-retention=1h` (むしろ db 肥大化する悪手) を削除
  - 期待効果セクションに「RSS 即減少しないケース」の注意を追加
  - 代替/併用候補として apiserver `target-ram-mb` / `default-watch-cache-size`
    チューニングを追記 (危険な `watch-cache=false` は採用しない)
