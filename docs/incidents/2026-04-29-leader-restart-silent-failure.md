# Incident: 2026-04-29 leader-restart Silent Failure

> **位置づけ**
>
> これは outage の記録ではない。導入 (2026-04-24, [`7f4be68`](https://github.com/bright-room/br-cluster/commit/7f4be68)) 以降 5 日間、`k3s-leader-restart.timer` が **毎日発火していたが SIGPIPE で静かに早期終了**しており、本来想定していた leader ローテーションが 1 度も発生していなかった、という潜在バグの発見と修正の記録。
>
> ユーザー影響なし、外部公開サービス・内部 Pod とも稼働継続。ただし `kube-controller-manager` leader が同一ノード (br-node2) に固定され、累積状態 (workqueue / retry / event) で k3s-server プロセスが約 3.5 cores 食う状態が継続していた。

## TL;DR

- `awk '/^etcd_server_is_leader / {print $2; exit}'` + `set -o pipefail` の組み合わせで awk が早期 `exit` → curl が SIGPIPE で exit 23 → script 全体が早期終了
- 後続の `kubectl get lease` が一度も実行されず、wrapper が常に `SKIP: not a leader` を log
- 結果: 5 日間 leader ローテーションが起きず、br-node2 に `kube-controller-manager` / `kube-scheduler` / `k3s-cloud-controller-manager` / `plndr-cp-lock` が固定、k3s-server が 216% CPU 消費
- 修正 ([`PR #222`](https://github.com/bright-room/br-cluster/pull/222), `fcf2139`) で `{exit}` 削除 + `plndr-cp-lock` を監視対象に追加
- 適用後、br-node2 / br-node3 を順に手動 restart して累積状態をリセット → 全 CP が 12-23% CPU の健全状態に復帰

## 影響

| 項目 | 状態 |
|---|---|
| 外部公開サービス (`*.b8m.app`) | 影響なし |
| 内部 Pod の reconcile | 遅延の兆候はあったが業務影響なし |
| 計画への波及 | Istio ambient 導入計画の Phase 0 ブロッカー扱いになっていた ([`docs/proposals/istio-ambient.md`](../proposals/istio-ambient.md)) |
| 観測可能な兆候 | br-node2 の k3s-server プロセス CPU 216%、ノード CPU 107%、load average 5.20 |

## タイムライン

事実は git log + journal ベース。推定は明示する。

| 日時 | 出来事 |
|---|---|
| 2026-04-24 | `k3s-leader-restart` 導入 ([`7f4be68`](https://github.com/bright-room/br-cluster/commit/7f4be68)) |
| 2026-04-24 | apiserver 健全性チェックを TCP port reachability に変更 ([`0e0b449`](https://github.com/bright-room/br-cluster/commit/0e0b449)) |
| 2026-04-25 〜 04-29 | 毎日 04:00 ± 15min に timer 発火、journal は **すべて `SKIP: not a leader`** (br-node2 で確認) |
| 2026-04-27 | role rename + ansible-lint 対応 ([`585f40b`](https://github.com/bright-room/br-cluster/commit/585f40b))、SIGPIPE バグはこの時点でも残存 |
| 2026-04-29 ~14:00 | 別件 (Istio 導入検討) で br-node2 の CPU 107% に気づき調査開始 |
| 2026-04-29 14:30 頃 | `kubectl get leases` で leader 集中を確認、`kubectl debug node` でホスト負荷を測定 (k3s-server 216%, %iowait=0) |
| 2026-04-29 14:38 | br-node2 で `systemctl restart k3s` → cm leader が br-node3 に転移、CPU も同様に転移 → **「個体問題」ではなく「leader 業務 + 累積状態」と判明** |
| 2026-04-29 14:50 頃 | `bash -x` で check-self を逐行トレースし、`etcd_leader=...` 代入直後に script が静かに終了することを確認、SIGPIPE バグ特定 |
| 2026-04-29 14:57 | 修正 commit ([`fcf2139`](https://github.com/bright-room/br-cluster/commit/fcf2139)) → [`PR #222`](https://github.com/bright-room/br-cluster/pull/222) |
| 2026-04-29 15:00 | merge + `make prod/provision/setup-k3s-leader-restart` で全 CP に配布 |
| 2026-04-29 15:09 | br-node1 で手動 trigger → `ABORT: br-node2 restarted 1901s ago (< 14400s)` で peer-health check が正しく abort することを確認 (safety net 機能) |
| 2026-04-29 15:12 | br-node3 で `systemctl restart k3s` 実行 → cm leader が br-node2 に転移、br-node3 CPU 108% → 12% に解消 |
| 2026-04-29 15:18 | 5 分観察後、br-node1 / br-node2 / br-node3 = 15% / 23% / 12% で定常 |

## 根本原因

### バグの発火メカニズム

該当箇所 (修正前):

```bash
etcd_leader=$(curl -sf --max-time 5 http://127.0.0.1:2381/metrics \
  | awk '/^etcd_server_is_leader / {print $2; exit}')
```

1. curl が etcd の `/metrics` (~50 KB) をパイプに書き始める
2. awk は `etcd_server_is_leader` 行を見つけた瞬間に `exit` し、stdin の読み取りを終了
3. curl は残りのデータをまだ書こうとする → パイプ閉じ → SIGPIPE で curl exit 23
4. `set -o pipefail` により pipeline 全体が non-zero
5. `set -e` により script がそこで終了
6. 後続の `kubectl get lease ...` 行は **一度も実行されない**
7. exit code は curl の 23 → wrapper の `|| skip "not a leader"` で潰され、journal には misleading な `SKIP: not a leader` が残る

### なぜ気づかれにくかったか

- journal を見ても `Starting → peers healthy → SKIP: not a leader → Finished` という **正常完了に見える** sequence が並ぶ
- 「leader でないので skip した」が偽陽性で出続けるが、このメッセージは正常運転時の他ノードでも普通に出る
- `RESTART: k3s` ログが**一度も**出ていなかったことを能動的に確認しないと異常に見えない
- exit code は 0 (wrapper が `skip` 関数で 0 終了に潰す)
- nondeterministic な側面: SIGPIPE の発火タイミングは curl と awk のスケジューリングに依存。条件が揃わない場合 (= 過去 4/29 早朝の br-node1 のように) は後続 kubectl まで到達することもあった

### 監視対象の漏れ

修正ついでに、`plndr-cp-lock` (kube-vip CP lock = API VIP 保持ノード) が判定対象から漏れていたことも対処:

- kube-vip は DaemonSet Pod として動くため、`systemctl restart k3s` では直接動かない
- ただし leader 状態として扱うことで「API VIP holder の固定化」検出には貢献する
- 完全に API VIP を能動的に動かすには別途 kube-vip Pod の delete step が必要 (今回の PR スコープ外)

## 切り分けの過程で消去できた仮説

最初に「br-node2 個体の問題」を疑ったが、計測で順に消去:

| 仮説 | 結果 | 根拠 |
|---|---|---|
| iowait / RTL9210 quirk による I/O 待ち | ❌ | `vmstat` の `%wa=0.25%`、`%iowait=0` |
| カーネル softirq / kthread 暴走 | ❌ | `mpstat` の `%soft=1%`、kworker は ~0.4% |
| Pod が CPU を食っている | ❌ | br-node2 上の Pod 合計 CPU = 197m に対しノード CPU = 3881m。差分 3.7 cores はホストプロセス側 |
| API VIP (`172.22.10.60`) トラフィック集中 | ❌ | kube-vip Pod を delete し VIP を br-node3 へ移動 → CPU 無変化。クラスタ内 Pod は Cilium で `127.0.0.1:6444` 経由 (= `k8sServiceHost`) のため API VIP は外部クライアントしか通らない |
| etcd raft leader としての fsync 負荷 | ❌ | etcd raft leader は br-node3 (`is_leader=1`)、br-node2 は follower |
| **`kube-controller-manager` leader 業務 + 累積状態** | **✅** | br-node2 で k3s 再起動 → cm leader が br-node3 へ転移 → CPU も連動して転移 |

## 対処と残った設計判断

### 採用した対処 (今回 PR)

| 項目 | 内容 |
|---|---|
| `awk '... {exit}'` を `awk '... {print $2}'` に変更 | SIGPIPE 発火を回避。awk は全行読むが該当行のみ出力されるので結果は同じ |
| `plndr-cp-lock` (kube-vip CP lock) を監視対象に追加 | API VIP holder の固定化を検出して rotate 対象に |
| 該当箇所に NOTE コメント追加 | 「pipefail 環境で `awk {exit}` を使うな」を script 内で警告 |

### 採用しなかった案

| 候補 | 不採用理由 |
|---|---|
| `set +o pipefail` で pipeline 単位の失敗を許容 | 他のロバスト性が下がる、局所修正の方が安全 |
| `curl ... \| head -1 \| awk ...` | head も同様の SIGPIPE 問題があり根本解決にならない |
| awk を `grep + cut` に書き換え | 書き換え量が大きい、`{exit}` 削除の方が diff が小さい |
| kube-vip Pod の delete step を script に追加 | 今回 PR のスコープ外、別件として整理 |

### 残った設計判断 / 学び

- **leader 業務は同一ノードで長期間継続すると累積状態で CPU を食う**: 今回 br-node2 上の累積状態 (workqueue retry, event 履歴, openapi v3 aggregation の 1.36M リトライ等) が ~3.5 cores を恒常消費していた。手動 restart 後は 0.5-1 core 程度に落ち着いた。**「3.5 cores が構造的に必要」という当初の推測は誤り**で、実際は「累積状態が膨らんだ leader = 3.5 cores」だった
- **leader-restart の日次ローテーション設計は正しい**: 累積状態が膨らむ前にリセットされるなら、Pi 4 cluster でも CP CPU は健全レンジに収まる。今回のバグ修正で本来の意図が機能するようになった
- **kube-vip / etcd raft leader の能動的移譲は別タスク**: 今回はバグ修正に閉じている。kube-vip Pod delete や `etcdctl move-leader` を組み合わせると更に rotation が綺麗になるが、複雑度とリスクのトレードオフを別途検討する

## 学び (運用上の注意)

| 項目 | 教訓 |
|---|---|
| シェル | `set -o pipefail` 環境で `cmd1 \| awk '... {exit}'` は SIGPIPE 経由の早期終了を起こす。同様に `head -n1` も注意。pipeline の早期消費を避けるか、`pipefail` を局所的に外す |
| systemd unit + wrapper | wrapper で `\|\| skip "..."` のようにエラーを潰す設計は、**真の終了原因を上書き**する。wrapper の skip メッセージには元 script の終了理由を含める方が安全 |
| 切り分けの順序 | 「ノード個体の問題」と「役割集中の問題」は早めに区別する。今回は **lease holder を別ノードに移して症状が転移するか確認**する一手で構造的問題と判明、調査時間を圧縮できた |
| Pi cluster の感覚値 | `kubectl top nodes` のノード CPU と `kubectl top pods` の Pod CPU 合計の差分は **k3s-server / containerd / kernel** が食っている量。今回のように差分が 3+ cores なら明らかな異常 |
| journal の解釈 | "SKIP: ..." は中身を読まないと正常運転時のメッセージと区別できない。**RESTART ログが一度も出ていない**ことを能動的に確認しないと潜在バグが見えない |

## 関連

- 修正 PR: [#222](https://github.com/bright-room/br-cluster/pull/222) (`fcf2139`)
- 関連 proposal: [`docs/proposals/istio-ambient.md`](../proposals/istio-ambient.md) — 本件の解決でこちらの Phase 0 が unblock
- 過去のインシデント: [`docs/incidents/2026-04-13-observability-cascade.md`](2026-04-13-observability-cascade.md) — 別件、観測スタックの cascade 障害
- 役割の元実装: [`provisioner/roles/k3s_leader_restart/`](../../provisioner/roles/k3s_leader_restart/)
