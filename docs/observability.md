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

## 関連ファイル

- `manifests/platform/kube-prometheus-stack/app/` — Prometheus / Alertmanager / node-exporter / kube-state-metrics
- `manifests/platform/kube-prometheus-stack/app/components/k3s/values.yaml` — kubelet / etcd スクレイプ設定
- `manifests/platform/kube-prometheus-stack/externalnodes-monitoring/` — 外部ノード ServiceMonitor + EndpointSlice
- `manifests/platform/alloy/app/base/values.yaml` — ログ収集パイプライン定義
- `manifests/platform/opentelemetry-collector/app/base/values.yaml` — トレース tail_sampling 設定
- `provisioner/roles/monitoring_agent/` — 外部ノード systemd node_exporter
