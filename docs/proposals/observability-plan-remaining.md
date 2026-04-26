# observability-plan 残課題 (検証期間中)

元: `docs/observability-plan.md` (commit `ecf4a91` で削除)
2026-04-23 時点のスナップショットから、**まだ着地していない部分のみ**を抜粋。
全項目が着地したら本ファイルごと削除する。

---

## 影モード稼働中 (検証期間中)

### P2-B phase 1: CP ノードに Alloy DaemonSet 展開

- 現状: PR #170 で **br-node2 1 台限定** の影モード稼働中
- 設計: 独立 HelmRelease `alloy-cp` (worker DS には一切手を入れず削除で完全 rollback 可能)
  - resource: `64Mi req / 192Mi limit`
  - `nodeAffinity` で br-node2 hostname 固定
  - OTLP receiver は持たず pod log file tailing のみ
- ノード選定理由:
  - br-node1 (primary CP / etcd cluster-init) を避ける
  - br-node3 (P2-C で NotReady 実績) を避ける
- 観察項目: OOM / CP etcd 安定性 / Loki ログ到達
- 観察期間: 1〜2 週間

### → P2-B phase 2 (未着手)

- 影モードが安定したら `nodeAffinity` を外して **3 CP 全台に展開**
- apply は他クラスタ変更と重ねず静かなタイミングで (2026-04-13 cascade 教訓)
- リスク: 高 (Pi 4B 4GB CP のメモリ余裕が薄い)

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

P2-D で kubeEtcd endpoints は `cluster-forge generate-manifests` 経由で servers.yaml + 1Password に寄せたが、他 manifest には依然として IP / ホスト情報のハードコードあり。

**方針**: per-consumer に YAML fragment 生成を増やすと「新 consumer ごとに cluster-forge Python 側に生成関数追加」コストが嵩むため、**個数固定のスカラー値は `cluster-settings.yaml` + Flux postBuild `${VAR}` に寄せる**路線で follow-up。

想定する移行順:

1. **kubeEtcd endpoints を postBuild 化**
   - P2-D で per-consumer 生成にした `etcd-endpoints.yaml` を、`cluster-settings.yaml` の `BR_NODE{1,2,3}_IP` + `components/k3s/values.yaml` 内 `${BR_NODE1_IP}` に置換
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

1. **P2-B phase 2 (3 CP 全台展開)** — br-node2 影モード (#170) が 1〜2 週間安定したら nodeAffinity を外す。他クラスタ変更と重ねない
2. **P2-F phase 3 (dashboard + PrometheusRule)** — NetworkPolicy を書き始めた後
