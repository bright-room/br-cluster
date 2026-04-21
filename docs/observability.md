# 可観測性 (Observability) アーキテクチャ

クラスタ内外の各コンポーネントから **メトリクス / ログ / トレース** がどこで生まれ、誰が集めて、どこに保存されるかをまとめる。

## サーバー構成 (物理ノード)

```mermaid
graph TB
  subgraph lan["自宅 LAN (172.22.10.0/24)"]
    subgraph external_nodes["外部ノード (k3s 管轄外)"]
      gw["br-gateway1<br/>172.22.10.1<br/>(gateway)"]
      ext["br-external1<br/>172.22.10.50<br/>(external)"]
    end

    subgraph k3s["k3s クラスタ"]
      subgraph cp["control-plane (Pi 4B / 4GB)"]
        n1["br-node1<br/>172.22.10.10<br/>primary"]
        n2["br-node2<br/>172.22.10.11<br/>secondary"]
        n3["br-node3<br/>172.22.10.12<br/>secondary"]
      end
      subgraph wk["workers (Pi 4B / 8GB)"]
        n4[br-node4]
        n5[br-node5]
        n6[br-node6]
      end
    end
  end

  style external_nodes fill:#fff3e0,color:#000
  style cp fill:#e3f2fd,color:#000
  style wk fill:#e8f5e9,color:#000
  style k3s fill:#326ce5,color:#fff
```

- **stateful / 重量コンポーネント** (Prometheus, Alertmanager, Grafana, Loki, Tempo, Alloy 等) は `nodeSelector: node_type=worker` でワーカー固定
- **node-exporter DaemonSet と Cilium Agent** は例外的に全ノードで稼働（OS/ネットワーク層の可視化のため）

## 収集対象サマリ (どのノードから何が出るか)

| ノード | ホスト OS メトリクス | k8s メトリクス | ログ | トレース |
|---|---|---|---|---|
| br-gateway1 | systemd `node_exporter` | — | — | — |
| br-external1 | systemd `node_exporter` | — | — | — |
| br-node1〜6 | DaemonSet `node_exporter` Pod | kubelet / cilium / etc | Pod の stdout/stderr | アプリ→OTLP |
| br-node1〜3 (CP) | 上記 + **etcd** `:2381` | 上記 | ※ Alloy 不在（注記参照） | — |

---

## メトリクス (Metrics)

Prometheus が**プル型スクレイプ**で集めるのが基本。アプリ由来メトリクスだけ **OTLP プッシュ**経路を併用する。

```mermaid
graph LR
  classDef host fill:#fff3e0,color:#000
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef app fill:#81c784,color:#000

  subgraph ext["外部ノード (systemd)"]
    ne_gw["node_exporter<br/>:9100"]:::host
    ne_ex["node_exporter<br/>:9100"]:::host
  end

  subgraph k3s["k3s (pull ターゲット)"]
    ne_ds["node-exporter<br/>DaemonSet Pod"]:::pod
    kubelet["kubelet (cAdvisor)"]:::pod
    etcd_m["etcd :2381<br/>(CP 3台)"]:::pod
    ksm["kube-state-metrics"]:::pod
    workloads["ServiceMonitor / PodMonitor<br/>- Cilium / Hubble<br/>- Envoy Gateway<br/>- CoreDNS<br/>- cert-manager<br/>- external-dns<br/>- Longhorn<br/>- CloudNative-PG<br/>- Alloy / OTel / Loki / Tempo / Grafana"]:::pod
  end

  subgraph otlp_push["アプリ (OTLP push)"]
    app["ユーザーアプリ"]:::app
    alloy_r["Alloy OTLP receiver<br/>:4317/:4318"]:::pod
    otelc["OTel Collector (Gateway)<br/>tail_sampling / batch"]:::pod
  end

  prom[("Prometheus TSDB<br/>14日 / 50GB<br/>on Longhorn")]:::sink

  ne_gw  -.->|"scrape (EndpointSlice)"| prom
  ne_ex  -.->|"scrape (EndpointSlice)"| prom
  ne_ds  -.->|scrape| prom
  kubelet -.->|scrape| prom
  etcd_m -.->|scrape| prom
  ksm    -.->|scrape| prom
  workloads -.->|scrape| prom

  app -->|OTLP| alloy_r
  alloy_r -->|OTLP traces/metrics| otelc
  otelc -->|"remote_write"| prom
```

### 現状の「温度」など RPi 固有メトリクス
**取得していない。** node_exporter は `--web.listen-address` のみで起動しており、thermal / hwmon / textfile collector が無効。

---

## ログ (Logs)

Alloy (DaemonSet) が各ワーカーの **`/var/log/pods/*/*/*.log` を直接 tail** して Loki に push する方式。

```mermaid
graph LR
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef app fill:#81c784,color:#000
  classDef host fill:#fff3e0,color:#000

  subgraph worker["ワーカーノード (br-node4/5/6)"]
    podlog["/var/log/pods/**/*.log<br/>(kubelet が書く)"]:::host
    alloy["Alloy (DaemonSet)<br/>loki.source.file + loki.process"]:::pod
  end

  app["ユーザーアプリ"]:::app
  alloy_otlp["Alloy OTLP receiver"]:::pod

  loki[("Loki<br/>loki-gateway")]:::sink

  podlog -->|tail| alloy
  alloy  -->|"http push (/loki/api/v1/push)"| loki

  app -->|"OTLP logs"| alloy_otlp
  alloy_otlp -->|"otelcol.exporter.loki"| loki
```

- **stdout/stderr の CRI ログ**を kubelet が `/var/log/pods/` に書き、Alloy がファイル tail（apiserver の log-follow は使わない = 過去インシデントの教訓）
- アプリ由来の **OTLP logs** も Alloy が受け取って直接 Loki に投げる（OTel Collector は経由しない＝ログ経路を単純化）

---

## トレース (Traces)

```mermaid
graph LR
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef app fill:#81c784,color:#000

  app["ユーザーアプリ"]:::app
  alloy["Alloy OTLP receiver<br/>:4317/:4318"]:::pod
  otelc["OTel Collector (Gateway)<br/>- memory_limiter<br/>- tail_sampling<br/>  (errors 100% / others 10%)<br/>- batch"]:::pod
  tempo[("Tempo")]:::sink

  app -->|OTLP| alloy
  alloy -->|"OTLP gRPC"| otelc
  otelc -->|"OTLP gRPC"| tempo
```

- Alloy はトレースを**直接 Tempo には送らない**。`tail_sampling` を1箇所に集約するため必ず Collector を経由
- 全エラーは保持、その他は 10% サンプリング

---

## 全体統合ビュー (Grafana)

```mermaid
graph TB
  classDef sink fill:#f44336,color:#fff
  classDef ui fill:#f9a825,color:#000

  prom[("Prometheus<br/>(metrics)")]:::sink
  loki[("Loki<br/>(logs)")]:::sink
  tempo[("Tempo<br/>(traces)")]:::sink

  grafana["Grafana<br/>(*.b8m.app)"]:::ui

  prom --> grafana
  loki --> grafana
  tempo --> grafana

  user["ユーザー<br/>(CF Access 認証)"] --> grafana
```

---

## 注記 / 既知のギャップ

1. **RPi 固有メトリクス (温度 / スロットリング / 実クロック / コア電圧) 未取得**
   - node_exporter の textfile collector + `vcgencmd` で追加可能（別途検討中）

2. **PrometheusRule (アラート定義) 未作成**
   - kube-prometheus-stack 同梱のデフォルトルールは有効だが、温度/スロットリング用カスタムルールは未追加

3. **Alloy がワーカーのみにデプロイされている**
   - `controller.nodeSelector: node_type=worker` により **control-plane (br-node1〜3) 上の Pod ログは収集されていない**
   - 現状 CP 上は DaemonSet (node-exporter, cilium-agent) のみなので影響は限定的だが、障害時調査の盲点になりうる

4. **外部ノード (br-gateway1, br-external1) のログ / トレースは未収集**
   - メトリクスのみ（systemd node_exporter）。journald のログ集約は未実装

5. **長期保存 (Thanos / Mimir 等) 未導入**
   - Prometheus 14日保持のみ

---

---

## 理想像 / ロードマップ

上記の「既知のギャップ」を段階的に解消する案。RPi クラスタのリソース制約を前提に、**既存の Alloy + OTel Collector 構成は維持**した上で、主にホスト OS 層の可観測性を強化する方針とする。

### 目指す全体像

```mermaid
graph LR
  classDef host fill:#fff3e0,color:#000
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef rule fill:#ab47bc,color:#fff

  subgraph lan["全ノード (統一管理)"]
    direction TB
    unified["systemd node_exporter<br/>+ --collector.textfile<br/>+ vcgencmd timer (30s)<br/>→ 温度/スロットル/クロック/電圧"]:::host
  end

  subgraph k3s["k3s"]
    prom[("Prometheus")]:::sink
    rules["PrometheusRule<br/>- HighTemperature<br/>- Throttled<br/>- Undervoltage"]:::rule
    am["Alertmanager"]:::pod
    k8s_metrics["ServiceMonitor 群<br/>(変更なし)"]:::pod
  end

  unified -.->|"scrape (EndpointSlice)"| prom
  k8s_metrics -.-> prom
  prom --> rules
  rules --> am
```

### フェーズ分け

#### Phase 1: node_exporter を k3s 外に統一

| 項目 | 内容 |
|---|---|
| 目的 | 全 8 ノードを単一実装 (systemd + Ansible) に統合。k3s 障害時にも監視継続 |
| 変更範囲 | ホスト OS メトリクス層のみ。k8s ワークロード系 ServiceMonitor は**一切触らない** |
| 作業 | 1. `monitoring_agent` role を `br-node*` にも適用<br/>2. `externalnodes-monitoring` EndpointSlice に `br-node1〜6` 追加 (できれば `servers.yaml` から生成)<br/>3. kube-prometheus-stack Helm values で `prometheus-node-exporter` を無効化 |
| 得るもの | ・保守対象が 1 経路に集約<br/>・後続 Phase で hostPath マウント体操が不要<br/>・過去の observability-cascade 系インシデント再発耐性 |

#### Phase 2: RPi 固有メトリクス収集 (textfile collector + vcgencmd)

| 項目 | 内容 |
|---|---|
| 取得する値 | 温度 (`measure_temp`) / スロットリング状態 (`get_throttled`) / 実クロック (`measure_clock arm`) / コア電圧 (`measure_volts core`) |
| 手段 | **既存の node_exporter の内蔵機能** (`--collector.textfile`) に乗せる。別エクスポータ (rpi_exporter 等) は導入しない |
| 実装箇所 | `provisioner/roles/monitoring_agent/` 配下<br/>・`node_exporter.service.j2` に textfile フラグ追加<br/>・`/run/node_exporter/textfile` ディレクトリ (tmpfs, SD 摩耗回避)<br/>・`rpi-metrics.sh` (vcgencmd を叩いて `.prom` に書き出し)<br/>・`rpi-metrics.service` + `rpi-metrics.timer` (30秒間隔) |
| リソース影響 | 常駐プロセスなし。CPU 平均 < 0.3%、メモリ 0 (timer 起動時のみ一時数MB) |
| Phase 1 依存 | あり。Phase 1 完了後なら hostPath マウント設定が不要で最もシンプル |

#### Phase 3: PrometheusRule (アラート) 追加

現状 `PrometheusRule` は 0 件。RPi 運用で特に価値が高いルールを定義する:

| アラート名 | 条件 | 重大度 |
|---|---|---|
| `NodeHighTemperature` | `node_thermal_temperature_celsius > 75` for 5m | warning |
| `NodeCriticalTemperature` | `node_thermal_temperature_celsius > 80` for 2m | critical |
| `NodeThrottlingDetected` | `rpi_throttled_state != 0` for 1m | warning |
| `NodeUndervoltage` | `rpi_throttled_state & 0x1 == 1` for 1m | critical |
| `NodeFrequencyCapped` | `rpi_arm_clock_hertz < expected` for 5m | warning |

Phase 2 でメトリクスが出るようになってから追加。

### 将来的検討 (優先度低)

Pi クラスタの規模感では直近必須ではないもの:

- **外部ノード (br-gateway1, br-external1) の journald → Loki ログ収集**: `alloy` の systemd 版 or `promtail` 同等品を systemd で
- **control-plane の Pod ログ収集**: Alloy の `nodeSelector` を外すか、CP 専用の軽量 Alloy を配置
- **メトリクス長期保存**: Thanos / Mimir。現状 14 日保持で運用上問題なし
- **Blackbox exporter**: 外形監視。家庭内サービスなので優先度低

### 非目標 (やらないこと)

- **Alloy の OTel Collector への置き換え**: Pi 制約下ではメモリ +200〜400Mi 悪化、得るメリット小
- **rpi_exporter の導入**: 常駐 10〜20MB が無駄。textfile 方式で完全代替可能
- **Prometheus の水平分散**: 単一インスタンスで十分

---

## 関連ファイル

- `manifests/platform/kube-prometheus-stack/app/` — Prometheus / Alertmanager / node-exporter / kube-state-metrics
- `manifests/platform/kube-prometheus-stack/app/components/k3s/values.yaml` — kubelet / etcd スクレイプ設定
- `manifests/platform/kube-prometheus-stack/externalnodes-monitoring/` — 外部ノード ServiceMonitor + EndpointSlice
- `manifests/platform/alloy/app/base/values.yaml` — ログ収集パイプライン定義
- `manifests/platform/opentelemetry-collector/app/base/values.yaml` — トレース tail_sampling 設定
- `provisioner/roles/monitoring_agent/` — 外部ノード systemd node_exporter
