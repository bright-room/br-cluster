# observability-plan 残課題 (検証期間中)

元: `docs/observability-plan.md` (commit `ecf4a91` で削除)
2026-04-23 時点のスナップショットから、**まだ着地していない部分のみ**を抜粋。
全項目が着地したら本ファイルごと削除する。

---

## P2-B phase 2: 3 CP 全台展開 (観察期間中)

### Phase 1 観察結果 (2026-04-23 〜 2026-05-01)

br-node2 1 台限定の影モード (#170) を ~7 日稼働させた結果:

| 項目 | 結果 |
|---|---|
| restart | 0 (起動直後の config reload 1 回のみ) |
| memory | 70Mi / limit 192Mi (36%) |
| CPU | 38m / req 50m, limit 300m |
| Alloy ログ error/warn | 直近 1000 行で 0 件 |
| br-node2 MemoryPressure | False 継続 |

br-node1 / br-node3 もメモリ余裕あり (8%/55%, 4%/51%)、+1 pod で押し出すリスクは低い。

### Phase 2 (#261, 2026-05-01 merged)

- overlay の `nodeAffinity` (hostname pin) を削除し、`nodeSelector: node-role.kubernetes.io/control-plane=true` のみで 3 CP 全台に DaemonSet 展開
- rollout 直後の確認:
  - 3 pod 全て `Running 2/2` / restart 0
  - memory: br-node1=47Mi, br-node2=102Mi, br-node3=47Mi (全て limit 192Mi の 53% 以下)
  - 3 ノードとも `MemoryPressure=False` 継続
- rollback: overlay に hostname pin を戻すのみ

### 観察期間中に確認すること (〜2026-05-15)

- 3 CP すべてで restart 0 が継続しているか
- memory 使用量が limit 192Mi の 70% を超えていないか (特に br-node2 は履歴ノードで startup 直後 102Mi だったので注視)
- Loki に各 CP 由来のログが平坦に到達しているか (`{node="br-node1"}` / `{node="br-node3"}` の rate が安定)
- CP の `MemoryPressure` / etcd 健全性に劣化が出ていないか

問題なければ本セクションを削除 (この proposal doc は **全項目着地でファイルごと削除**)。問題が出たら overlay に hostname pin を戻して phase 1 に戻す。

---

## 着手条件待ち

### P2-F phase 3: Hubble drop flow の dashboard + PrometheusRule

- 着手条件: **NetworkPolicy 導入後**
- 理由: 現状の drop は `UNSUPPORTED_L3_PROTOCOL` (ICMPv6 RS) / `UNSUPPORTED_L2_PROTOCOL` (ARP) のノイズ主体
- NetworkPolicy 導入後に `policy-denied` verdict が主成分になったタイミングで:
  - src/dst namespace 別の denied rate パネル
  - 急増 alert

---

## 観測系 follow-up (未消化)

| 項目 | 内容 |
|---|---|
| Cilium socketLB で LB IP hairpin 解決 | `bpf.hostRouting: true` or `loadBalancer.acceleration` 調整で lease holder 自身から LB IP 到達可能にする。成功すれば P2-C (6) の per-group push URL 分岐 (k3s=localhost NodePort / 非 k3s=ドメイン) を削除できる |
| Provisioner playbook に `serial: 1` | #154 で `setup_monitoring_agent` のみ対応済。他 playbook は未対応 |

---

## トポロジー情報の SSoT 化 (P2-D follow-up)

> 2026-04-25 PR #185 で P2-D の kubeEtcd endpoints 生成 (cluster-forge generate-manifests 経由) は revert 済。現状は `components/k3s/values.yaml` に IP ハードコードに戻っている。

**方針**: per-consumer に YAML fragment 生成を増やすと「新 consumer ごとに cluster-forge Python 側に生成関数追加」コストが嵩むため、**個数固定のスカラー値は `cluster-settings.yaml` + Flux postBuild `${VAR}` に寄せる**路線で follow-up。

想定する移行順:

1. **kubeEtcd endpoints を postBuild 化**
   - `cluster-settings.yaml` に `BR_NODE{1,2,3}_IP` を追加し、`components/k3s/values.yaml` の IP 直書きを `${BR_NODE1_IP}` 等に置換
   - CP 台数変更時は cluster-settings.yaml と values.yaml のリストを両方手で触ることになるが、変更頻度が低いので許容
2. **`cluster-settings.yaml` を `cluster-forge generate-manifests` 生成に寄せる**
   - servers.yaml + 1Password から `BR_*_IP` / `CLOUDFLARED_TUNNEL_ID` 等を自動生成
   - 既存の固定値 (`CLUSTER_DOMAIN` / `TRUSTED_INTERNAL_POD_CIDR` 等) をどこに書くかは要設計 (servers.yaml か専用の `cluster-config.yaml` か)

---

## Phase 3: 将来検討 (必要性が出てから)

| 項目 | 始める条件 |
|---|---|
| Thanos Sidecar (Prometheus 長期保存) | 14 日制限で困るトラブル分析が発生したとき |
| Tempo metrics-generator (span metrics のみ) | トレース上でサービスマップを見たくなったとき |
| kube-apiserver tracing | apiserver の遅延を深掘りするインシデントが起きたとき (デバッグ時限定 on) |
| Pyroscope (Continuous Profiling) | メモリ/CPU の特定アプリ深掘りが必要になったとき |
| Blackbox exporter (外形監視) | cloudflared が止まっても気付かないケースが発生したとき |
| Loki ruler (ログベースアラート) | メトリクスでは捉えきれないログパターン (under-voltage in dmesg 等) を鳴らしたいとき |

---

## 次回着手時の推奨順序

1. **P2-B phase 2 観察期間明け** — 3 CP 展開後 1〜2 週間問題なければ本ファイルから phase 1/2 セクションを削除
2. **P2-F phase 3 (dashboard + PrometheusRule)** — NetworkPolicy を書き始めた後
