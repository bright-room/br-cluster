# Incident: 2026-04-13 Observability Cascade

## TL;DR

2026-04-12 夕方からの大量の新規コンポーネント投入 (Istio Ambient / Cilium Hubble / Garage / Longhorn backup / Fluent Bit + Fluentd + OTel Collector) の直後、**複数の k3s ノードで k3s プロセスが落ち、SSH さえ応答しない状態**に陥った。物理的にコンソールアクセスが必要なレベルでノードが沈黙した。

翌 2026-04-13 深夜に緊急対応として大半の新規スタックを revert、重量コンポーネントを worker 固定、swap + node reserve を導入して沈静化。以降、追加の副次的要因 (Alloy の apiserver log-follow / k3s の GOMEMLIMIT 未設定 / NVMe USB-adapter の UAS モード不良) が順次判明し、都度対処している。

本ドキュメントは、以降の観測系アーキテクチャ決定 (overlay に散在する `See incident-2026-04-13-observability-cascade.md.` 参照) の背景を永続化するためのもの。

> **注意**: 障害発生中のリアルタイム記録は残っていない。本文書のタイムラインと RCA は **git log ベースで事後再構築**したもの。厳密な発生時刻・被害範囲・因果順序は不明な点が多く、「少なくともこれは事実」と「推定」を区別して記述している。

## 被害範囲 (記憶ベース)

- **複数の k3s ノードで k3s プロセスが停止**
- **SSH すら応答しなくなった**ノードがあり、kubectl / Ansible での遠隔復旧が不能
- 物理コンソールでの介入が必要なレベル

具体的にどのノードが何台落ちたか、どの公開サービスが何分ダウンしたか、データロスがあったかどうかは記録が残っていない。

## タイムライン

すべて `git log` ベースで事実として残っている commit を時系列に並べたもの。**commit が打たれた時刻 = 障害の発生時刻ではない**点に注意 (事後に revert/fix がまとめて投入された可能性が高い)。

### 2026-04-12 (発生日)

| 時刻 | 出来事 |
|---|---|
| 17:00 | `feat(istio): switch to Ambient mode with ztunnel and istio-cni` (`00ed56e`) |
| 18:25 | `feat(cilium): add Hubble and monitoring components` (`8312d55`) |
| 19:06 | `feat(garage): add S3-compatible object storage (Phase 6)` (`c2b17b1`) |
| 19:46 | `feat(garage): add monitoring component with ServiceMonitor` (`57dee08`) |
| 20:47 | `feat(longhorn): configure S3 backup target to br-external1 Garage` (`f0f15ab`) |
| 20:50 〜 22:52 | Longhorn backup 設定の試行錯誤コミットが 10 件以上連続 (値参照エラー / CR 種別変更 / 1Password シークレット構造変更) |

この間に実運用上の cascade が進行し始めたと推定される (ノード NotReady / Pod 退避 / Longhorn rebuild)。**当時 Fluent Bit + Fluentd + OpenTelemetry Collector の 3 段構成**でログ・トレース収集が走っていた。

### 2026-04-13 (収束作業)

緊急対応として 23:32〜23:58 の**約 30 分間に 10 件以上の fix/revert を一括投入**。

| 時刻 | commit | 意図 |
|---|---|---|
| 23:32 | `ca4ab98 fix(stability): enable swap and reserve node resources to prevent cascade` | **cascade 防止の直接対策**。swap 有効化と kubelet system-reserved / kube-reserved を導入 |
| 23:32 | `d8c1d61 revert(observability): remove Istio Ambient and Fluent/OTel collector stack` | Istio Ambient (ztunnel + istio-cni) および Fluent Bit + Fluentd + OTel Collector を丸ごと撤去 |
| 23:32 | `047be3b fix(observability): pin Loki/Tempo/kube-prometheus-stack to workers with limits` | 重量コンポーネントの **worker 固定 + リソース制限**。CP (Pi 4GB) にスケジュールされるのを防止 |
| 23:41 | `b1f44cd fix(tempo): switch to single-binary tempo chart` | Tempo を distributed → single-binary に変更 |
| 23:41 | `e53d671 feat(observability): add Grafana Alloy as unified telemetry collector` | **Alloy を単一エージェント**として再構築。旧 3 段 (fluent-bit + fluentd + otel) を置き換え |
| 23:57 | `302c509 fix(observability): switch Loki and Tempo to local filesystem storage` | S3 (Garage) backend も一旦切り離し、ローカルファイルシステムへ |
| 23:57 | `41f3782 revert(observability): remove Garage and Longhorn backup target` | Garage 自体を撤去 |
| 23:57 | `8e44cba revert(provisioner): remove backup, external, and Fluent Bit roles` | Ansible 側の Fluent Bit / external / backup role を削除 |
| 23:58 | `eab4e88 refactor(servers): convert br-external1 to k3s worker, drop EXTERNAL type` | br-external1 を k3s worker に一時的に吸収 |

### 2026-04-14 (post-incident)

- `68495a7 Merge pull request #49 from bright-room/fix/post-incident-stabilization` — **PR タイトルに "post-incident-stabilization" と明記**された最初の収束 PR
- `563ded1 feat(observability): Phase 0 — enable k3s etcd scrape and observability StorageClass` — **Phase 0** と冠した再建開始

### 2026-04-15 (巻き戻し / 再建)

- `4d7aadd revert(observability): remove longhorn-observability StorageClass` — 専用 SC は一旦撤回
- `a7f3e0a feat(external): restore br-external1 with Garage + Caddy for S3 backend` — **br-external1 を k3s の外に再配置**、Garage を復活
- `e44ed38 fix(provisioner): restore br-external1 host_vars and remove br-node7` — br-node7 という存在を撤去 (当時の一時的追加だった模様)

### 2026-04-16 (再構築の仕上げ)

- `db875d9 feat(observability): migrate loki/tempo to Garage S3 backend` — Loki/Tempo を復活した Garage に再接続
- `950de07 feat(observability): add OpenTelemetry Collector gateway (Phase A)` — OTel Collector を**ゲートウェイ専用**として再投入 (旧 3 段構成の otel とは役割が違う)
- `add5881 feat(provisioner): add GOMEMLIMIT for k3s server on control-plane nodes` — **k3s server プロセスの GOMEMLIMIT** を設定。Go runtime の GC 発動タイミングを OOM より手前に
- `0d3e86e feat: add Envoy Gateway tracing and access logging to OTel Collector`
- `18ce69a feat: add Grafana manifests (Phase B)`
- `17065ee fix: disable UAS for RTL9210 NVMe adapter to prevent I/O errors` — **ハードウェア層の副次要因**。ストレージ I/O エラーが cascade 中に増えた一因

### 2026-04-18 以降 (後から発見された副次要因)

- `078280a fix(alloy): switch pod log collection from apiserver to file tailing` — **Alloy の `loki.source.kubernetes` が apiserver log-follow を各 Pod 分開き、client-go QPS throttle → http2 切断 → tailer 再起動で CPU pin** という挙動が判明。ファイル tail に切り替え。今の `alloy/app/base/values.yaml` のコメントに背景が残っている

### 2026-04-21 (追加チューニング)

- `b3df0d2 / d05ac43 / 6d1765f fix(alloy): raise prod memory limit to 384Mi` (同名 commit が 3 回 = cherry-pick / rebase 履歴の痕跡)。Alloy の memory limit を 256Mi → 384Mi に引き上げ

## 根本原因 (推定 RCA)

> **この章は当時の記録が残っていないため、コードと commit メッセージから事後推定したもの**。断定ではない。以下の要因が**単独ではなく同時に作用した**結果として cascade が起きた、というのが後の緊急対応コミット群から読み取れる解釈:

1. **リソース保護の欠如**: swap 無効 / `system-reserved` / `kube-reserved` 未設定 → OS / k3s 本体が Pod と同じ土俵でメモリを取り合う
2. **重量コンポーネントの無秩序配置**: Prometheus / Alertmanager / Loki / Tempo / Grafana / OTel が CP (Pi 4GB) にスケジュールされ得る状態
3. **ログ収集の多段・重複**: Fluent Bit + Fluentd + OTel Collector の 3 段。メモリ・ソケット数・CPU をそれぞれ消費
4. **K8s API ベースのログ収集**: (後から判明) Alloy 移行後も `loki.source.kubernetes` が apiserver log-follow を各 Pod 分開く実装で、client-go QPS throttle → http2 切断 → tailer 再起動が CPU を pin
5. **Longhorn の replica rebuild storm**: ノードが NotReady になると Longhorn がデフォルトで replica 再構築を開始し、ストレージ I/O と CPU をさらに奪う
6. **外部依存 (Garage) の同時導入**: Longhorn backup / Loki / Tempo の backend として Garage に依存。Garage 自体の設定不備と cascade が同時進行して切り分け困難
7. **ハードウェア層の不良**: RTL9210 USB→NVMe アダプタの UAS モードが I/O エラーを誘発 (後日判明)

これらが同日に複合したため、通常なら局所的な 1 ノード OOM で済むところが**全 k3s ノードに波及**し、**ノード上の k3s 自身と sshd も含めて巻き込まれた**と推定される (実際に SSH 不能まで到達したため、単なる Pod レベルの OOM では説明がつかない)。

## 対処とその後残った設計判断

現在のリポジトリに残っている「一見過剰に見えるガード」は全てこの cascade の産物:

### 観測系

| 設定 | 場所 | 目的 |
|---|---|---|
| `prometheus-node-exporter` DaemonSet に **nodeSelector を付けない** | `kube-prometheus-stack/app/overlays/prod/values.yaml` | CP も監視経路を持ち続けるため。cascade 時に CP の挙動が見えなかった反省 |
| **Prometheus / Alertmanager / Prometheus Operator / kube-state-metrics は worker 固定** | 同上 | 再発防止 |
| **Loki / Tempo / OTel Collector / Grafana / Alloy も worker 固定** | 各 `app/overlays/prod/values.yaml` | 同上 |
| Alloy は**ファイル tail のみ** (`loki.source.file`) | `alloy/app/base/values.yaml` | apiserver log-follow による client-go throttle の再発回避 |
| Alloy memory limit **384Mi** | `alloy/app/overlays/prod/values.yaml` | 256Mi だと cardinality 高い namespace で OOM 頻発した経緯 |
| 旧 `fluent-bit` / `fluentd` / `external` / `backup` role は撤去済 | `provisioner/roles/` | 多段ログ収集の禁止 |
| OTel Collector は **Gateway 専用** (tail_sampling / batch のみ) | `opentelemetry-collector/app/base/values.yaml` | Alloy と役割分離。Collector 自体にログ tail 機能を持たせない |

### ストレージ / ノード安定化

| 設定 | 場所 | 目的 |
|---|---|---|
| `nodeDownPodDeletionPolicy: do-nothing` | `longhorn/app/overlays/prod/values.yaml` | **ノードが落ちても Longhorn に勝手に rebuild させない**。cascade の一次要因 |
| k3s server プロセスに `GOMEMLIMIT` | `provisioner/roles/k3s/` | k3s 自身を Go runtime 側で先に GC 圧力に入れる |
| swap + `system-reserved` / `kube-reserved` | `provisioner/roles/common/` | OS / k3s の最低限メモリを確保 |
| UAS を NVMe アダプタで無効化 | `provisioner/roles/common/` | I/O エラー低減 |
| br-external1 は **k3s の外 / Garage 専用ノード**に戻した | `servers.yaml` / provisioner | Longhorn backup / Loki / Tempo の外部保管先を k3s の可用性から切り離す |

### 今後の Do / Don't

- **Do**: 新規エージェント (CP 側 Alloy 等) は影モード (低 limit + 観察 1〜2 週間) で入れる
- **Do**: アクセスログ / フローログのような高頻度テレメトリは**サンプリング**してから Loki へ送る
- **Do**: 新規導入時は**重量コンポーネントを必ず worker pin + resources 明示**
- **Don't**: 同一セッションで重量級を 3 つ以上同時導入しない (当日はこれで複合して切り分け不能になった)
- **Don't**: K8s apiserver を経由するログ収集 (apiserver log-follow / watch ベースの tail) を採用しない
- **Don't**: ノード落下時に自動で replica rebuild するような storage 設定 (Longhorn `do-nothing` を外す等) を無検証で入れない
