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
| Provisioner playbook に `serial: 1` | #154 で `setup_monitoring_agent` のみ対応済。他 playbook は未対応 |

### やらないことに決めたもの

| 項目 | 理由 |
|---|---|
| Cilium socketLB で LB IP hairpin 解決 (`bpf.hostRouting: true` 等) | 現状の per-group push URL 分岐 (k3s=`localhost:30800` NodePort / 非 k3s=`internal-gateway` LB IP) は安定稼働中。`bpf.hostRouting` 等は masquerade や Istio ambient 同居挙動に波及するため、URL 分岐 1 箇所を消すための単独検証としては割に合わない。Istio ambient proposal の Phase 0 で同居検証する際にまとめて触る方が筋が良い |

---

## トポロジー情報の SSoT 化 (P2-D follow-up)

> 2026-04-25 PR #185 で P2-D の kubeEtcd endpoints 生成 (cluster-forge generate-manifests 経由) は revert 済。現状は `components/k3s/values.yaml` に IP ハードコードに戻っている。

### やらないことに決めたもの

| 項目 | 理由 |
|---|---|
| kubeEtcd endpoints を postBuild `${BR_NODE*_IP}` 化 | `components/k3s/values.yaml` は Ansible bootstrap (cluster-forge 経由) でクラスタに反映するパスがあり、Flux postBuild の `${VAR}` はそこでは展開されない。postBuild 化すると bootstrap 経路で値が解決できず壊れる |
| `cluster-settings.yaml` を `cluster-forge generate-manifests` 生成に寄せる | 上記が前提だったため連動して取り下げ。SSoT 化の方針自体は再検討余地あり、必要が出たら別 proposal で再起票 |

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
