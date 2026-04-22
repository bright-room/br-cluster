# 可観測性スタック 再設計プロポーザル

元 `docs/observability.md` のロードマップを、実装実態の再調査をもとに組み直した提案。議論用の別ファイル。

---

## 1. 現状 (実装から読み取った正)

### 1.1 シグナル経路の全体像

```mermaid
flowchart LR
  classDef host fill:#fff3e0,color:#000
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef ui fill:#f9a825,color:#000
  classDef gap stroke-dasharray: 5 5,stroke:#d32f2f,color:#d32f2f

  %% Hosts
  subgraph ext["外部ノード (systemd)"]
    ne_sys["node_exporter<br/>:9100<br/>(フラグなし素実装)"]:::host
    j["journald"]:::host
  end

  subgraph k3s["k3s クラスタ"]
    ne_ds["prometheus-node-exporter<br/>DaemonSet<br/>(全ノード nodeSelector なし)"]:::pod
    ksm["kube-state-metrics"]:::pod
    etcd["etcd :2381<br/>(CP 3ノード IP ハードコード)"]:::pod
    sm["ServiceMonitor / PodMonitor<br/>(Helm values 経由)<br/>cilium / envoy-gateway / coredns /<br/>cert-manager / external-dns / longhorn /<br/>cloudnative-pg / grafana / loki / tempo /<br/>metrics-server / alloy(monitoring) /<br/>otel-collector(monitoring)"]:::pod

    podlog["/var/log/pods/**/*.log<br/>(worker のみ tail)"]:::host
    alloy["Alloy DaemonSet<br/>(worker only:<br/> nodeSelector=worker)"]:::pod
    otelc["OTel Collector (deployment)<br/>- memory_limiter<br/>- tail_sampling (err 100% / 他 10%)<br/>- batch"]:::pod

    app["ユーザーアプリ"]:::pod
  end

  prom[("Prometheus<br/>14d / 50GB<br/>Longhorn PVC<br/>remote_write receiver ON")]:::sink
  loki[("Loki (SingleBinary)<br/>Garage S3 (br-external1)")]:::sink
  tempo[("Tempo<br/>Garage S3 (br-external1)")]:::sink
  am["Alertmanager<br/>(receiver 未設定 ⚠)"]:::pod
  graf["Grafana<br/>(Zitadel OIDC / アプリ内蔵)<br/>ダッシュボード手動管理 ⚠"]:::ui

  %% metrics scrapes
  ne_sys -.->|scrape| prom
  ne_ds  -.->|scrape| prom
  ksm    -.->|scrape| prom
  etcd   -.->|scrape| prom
  sm     -.->|scrape| prom

  %% logs
  podlog -->|tail| alloy
  alloy  -->|push| loki
  j     -. 未収集 .-> loki:::gap

  %% OTLP from app
  app -->|OTLP logs/traces/metrics| alloy
  alloy -->|logs| loki
  alloy -->|traces+metrics OTLP| otelc
  otelc -->|traces OTLP| tempo
  otelc -->|"metrics remote_write"| prom
  otelc -->|logs OTLP HTTP| loki

  %% alerting
  prom --> am

  %% UI
  prom --> graf
  loki --> graf
  tempo --> graf
  user["ユーザー"] --> zit["Zitadel OIDC<br/>auth.b8m.app"] --> graf
  zit -.-> sp["SecurityPolicy (Envoy)<br/>prom/alert/hubble/longhorn"]:::pod
```

### 1.2 ホスト/ノード行列 (現実)

| ノード | ホスト OS メトリクス | k8s メトリクス | Pod ログ | アプリ OTLP | journald |
|---|---|---|---|---|---|
| br-gateway1 | systemd node_exporter (素) | — | — | — | **未収集** |
| br-external1 | systemd node_exporter (素) | — | — | — | **未収集** |
| br-node1〜3 (CP) | DaemonSet node-exporter + etcd `:2381` | kubelet / cilium | **未収集** (Alloy なし) | — | — |
| br-node4〜6 (worker) | DaemonSet node-exporter | kubelet / cilium + ServiceMonitor 群 | Alloy tail → Loki | OTLP 受信 | — |

### 1.3 実装から分かった意図的な設計判断

| 判断 | 背景 |
|---|---|
| DaemonSet node-exporter に nodeSelector を付けない | `incident-2026-04-13-observability-cascade.md` 参照 (ファイルは空)。**CP も監視経路を持つ**ため意図的にバラまく |
| Grafana だけ SecurityPolicy 非適用 | Envoy OIDC + Grafana OIDC で二重ログインになる。アプリ内蔵 OIDC のみ |
| Alloy は logs だけ Loki 直送、traces/metrics だけ Collector 経由 | `tail_sampling` を Collector に集約するため |
| OTel Collector は **logs/traces/metrics の 3 シグナル全部**を処理 | アプリ OTLP は全シグナル Collector を通過。元 doc はトレース専用に見える図だが違う |
| Tempo `metrics-generator` 無効 | Pi 制約。サービスマップ / RED は未導入 |
| kube-prometheus-stack `defaultRules.create: false` | k3s 前提でないルールを除外。**→ PrometheusRule は本当に 0 件** |

---

## 2. ギャップ (穴の地図)

```mermaid
flowchart TB
  classDef p0 fill:#d32f2f,color:#fff
  classDef p1 fill:#f57c00,color:#fff
  classDef p2 fill:#fbc02d,color:#000
  classDef p3 fill:#9e9e9e,color:#fff

  subgraph P0["P0 通知経路 / 検知基盤 (今の構成で穴)"]
    A["Alertmanager receiver 未設定<br/>→ アラート書いても届かない"]:::p0
    B["PrometheusRule 0 件<br/>→ 検知なし運用"]:::p0
    C["Grafana ダッシュボード手動管理<br/>→ ロスト / 属人化"]:::p0
    D["incident doc が空<br/>→ observability-cascade の教訓が残らない"]:::p0
  end

  subgraph P1["P1 RPi 固有"]
    E["温度 / スロットル / 電圧未取得"]:::p1
    F["hwmon / thermal_zone collector 未有効"]:::p1
  end

  subgraph P2["P2 観測穴"]
    G["CP ノード Pod ログ未収集"]:::p2
    H["外部ノード journald 未収集"]:::p2
    I["kubeEtcd endpoints ハードコード"]:::p2
    J["Envoy/Hubble アクセスログ未投入"]:::p2
  end

  subgraph P3["P3 将来"]
    K["メトリクス長期保存 (Thanos/Mimir)"]:::p3
    L["Continuous Profiling (Pyroscope)"]:::p3
    M["Tempo metrics-generator<br/>(service map / RED)"]:::p3
    N["Blackbox / synthetic"]:::p3
  end
```

---

## 3. 理想像

### 3.1 P0 完了時点の姿 (最小コストで「使える」状態)

```mermaid
flowchart LR
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef ui fill:#f9a825,color:#000
  classDef new fill:#43a047,color:#fff

  prom[("Prometheus")]:::sink
  am["Alertmanager"]:::pod
  recv["Discord/ntfy/Email<br/>receiver"]:::new
  rules["PrometheusRule 群<br/>- kubernetes-mixin<br/>- longhorn / cnpg / cert-manager<br/>- TargetDown / PrometheusSelf"]:::new
  graf["Grafana"]:::ui
  sidecar["grafana sidecar<br/>(ConfigMap watch)"]:::new
  cm["ConfigMap:<br/>Node Exporter Full /<br/>K8s Views / Cilium /<br/>Envoy / Loki / Tempo /<br/>CNPG"]:::new
  inc["docs/incidents/<br/>2026-04-13-observability-cascade.md"]:::new

  prom --> rules --> am --> recv
  cm --> sidecar --> graf
  prom --> graf
```

### 3.2 P1 完了時点 (RPi 固有メトリクス)

**元 doc からの重要な変更点**: `prometheus-node-exporter` DaemonSet は**撤去しない**。systemd 側を全ノードに広げて textfile + vcgencmd を担わせ、**DaemonSet と systemd の二経路を両立** (observability-cascade 再発回避)。

```mermaid
flowchart TB
  classDef host fill:#fff3e0,color:#000
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef keep stroke:#1976d2,stroke-width:2px
  classDef new fill:#43a047,color:#fff

  subgraph all["全 8 ノード"]
    sys["systemd node_exporter<br/>+ --collector.hwmon<br/>+ --collector.thermal_zone<br/>+ --collector.textfile<br/>(:9100)"]:::new
    timer["rpi-metrics.timer (30s)<br/>vcgencmd → .prom"]:::new
    tf["/run/node_exporter/textfile/<br/>(tmpfs, RuntimeDirectory=)"]:::new
    timer --> tf --> sys
  end

  subgraph k3s["k3s ノード (br-node1〜6) の追加経路"]
    ds["prometheus-node-exporter<br/>DaemonSet<br/>(そのまま残す:<br/> observability-cascade 対策)"]:::keep
  end

  prom[("Prometheus")]:::sink
  sys -.->|"scrape (EndpointSlice)<br/>job=node-exporter-host"| prom
  ds -.->|"scrape (existing)<br/>job=node-exporter"| prom
```

- **systemd 経路**: 温度 (hwmon) / スロットル・電圧・実クロック (textfile+vcgencmd) を集約
- **DaemonSet 経路**: 今まで通り kubelet / cAdvisor と同じ観点で k3s ノード OS を見る
- **ラベル衝突回避**: systemd 側は別 `job` 名 (`node-exporter-host`) を relabeling で固定。ダッシュボードもラベルで切り替え
- ポート競合: DaemonSet は `hostNetwork: true` で :9100 を占有するので、systemd 側は **別ポート (例: :9101)** で上げる

### 3.3 P2 完了時点 (観測穴埋め)

```mermaid
flowchart LR
  classDef host fill:#fff3e0,color:#000
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef new fill:#43a047,color:#fff

  subgraph ext["外部ノード"]
    j["journald"]:::host
    a_sys["alloy (systemd)<br/>loki.source.journal"]:::new
  end

  subgraph k3s["k3s"]
    subgraph cp["control-plane"]
      cp_log["/var/log/pods"]:::host
      alloy_cp["Alloy DaemonSet<br/>(CP にも展開)"]:::new
    end
    subgraph wk["worker"]
      wk_log["/var/log/pods"]:::host
      alloy_wk["Alloy DaemonSet<br/>(既存)"]:::pod
    end

    etcd["etcd :2381"]:::pod
    eps["EndpointSlice<br/>(servers.yaml から生成)"]:::new

    envoy_log["Envoy Gateway<br/>access log → OTLP"]:::new
    hubble_flow["Hubble flow log"]:::new
  end

  loki[("Loki")]:::sink
  prom[("Prometheus")]:::sink

  j --> a_sys --> loki
  cp_log --> alloy_cp --> loki
  wk_log --> alloy_wk --> loki
  etcd -.-> eps -.-> prom
  envoy_log --> loki
  hubble_flow --> loki
```

### 3.4 P3 (将来要件が出たら)

```mermaid
flowchart LR
  classDef sink fill:#f44336,color:#fff
  classDef new fill:#9e9e9e,color:#fff

  prom[("Prometheus")]:::sink
  thanos["Thanos Sidecar<br/>→ Garage S3"]:::new
  store["Thanos Store / Query"]:::new
  pyro["Pyroscope<br/>(eBPF profiler)"]:::new
  tempo[("Tempo")]:::sink
  mg["Tempo metrics-generator<br/>(span metrics only)"]:::new

  prom --- thanos --> store
  pyro -->|profiles| prom
  tempo --- mg --> prom
```

---

## 4. フェーズ分け (優先度順)

### Phase 0: 通知経路 + 検知基盤 (最優先 / 実装コスト小)

| 作業 | 内容 |
|---|---|
| **incident doc 実体化** | `docs/incidents/2026-04-13-observability-cascade.md` を書き起こし。CP node-exporter を残した背景を永続化 |
| **Alertmanager receiver** | Discord webhook or ntfy + ExternalSecret で資格情報を注入。`alertmanagerSpec.configSecret` で 1Password 管理 |
| **PrometheusRule の土台** | kube-prom-stack の `defaultRules.create` を `true` に戻しつつ `disabled` で k3s に合わない項目のみ切る。加えて `TargetDown` / `PrometheusOutOfDisk` / `LonghornVolumeUsageCritical` / `CNPGBackupFailed` / `CertManagerCertExpirySoon` をカスタム追加 |
| **Grafana dashboard を code 化** | `sidecar.dashboards.enabled: true` + label selector `grafana_dashboard=1`。公式 ID (Node Exporter Full 1860, Cilium, Envoy, Loki, Tempo, CNPG) を ConfigMap として import |

### Phase 1: RPi 固有メトリクス (元 doc の主題、ただし非破壊化)

| 作業 | 内容 |
|---|---|
| **systemd node_exporter を全 8 ノードへ拡張** | `setup_monitoring_agent.yaml` の対象を `gateway:external:master:worker` に。**ポート :9101** を採用して DaemonSet (:9100) と共存 |
| **collector 有効化** | `--collector.hwmon --collector.thermal_zone --collector.textfile=...` を `node_exporter.service.j2` に追記。`RuntimeDirectory=node_exporter/textfile` で tmpfs ディレクトリを systemd 管理 |
| **rpi-metrics.{sh,service,timer}** | vcgencmd を叩いて `.prom.$$` へ書き atomic rename。30 秒周期 |
| **EndpointSlice 拡張** | `externalnodes-monitoring` を `host-node-exporter` (名称変更) に改名、br-node1〜6 も追加。**`servers.yaml` から kustomize plugin or CI で生成** |
| **ServiceMonitor relabel** | `job=node-exporter-host` / `instance=<nodeName>` で固定 (DaemonSet の `job=node-exporter` と衝突回避) |
| **PrometheusRule (RPi 固有)** | `NodeHighTemperature` (hwmon_temp > 75℃) / `NodeThrottled` (textfile メトリクスの bit ごとに別メトリクス化してスカラー比較) / `NodeUndervoltage` |

### Phase 2: 観測穴埋め

| 作業 | 内容 |
|---|---|
| **CP の Pod ログ** | Alloy の nodeSelector を撤去。CP 側は `resources.limits.memory=128Mi` 程度に絞る |
| **外部ノード journald** | `monitoring_agent` role に `grafana/alloy` 単体バイナリ (systemd) を同梱。`loki.source.journal` → Loki gateway |
| **kubeEtcd 動的化** | `servers.yaml` からの生成 or Service/EndpointSlice 方式。CP 入れ替え時に手動編集不要に |
| **Envoy Gateway アクセスログ** | EnvoyProxy CR の `telemetry.accessLog` で OTLP export → Alloy → Loki |
| **Hubble flow log** | hubble-relay → hubble-exporter (fluentd) もしくは直接 Loki push を検討 |

### Phase 3: 将来要件ベース

- Thanos Sidecar + Store (Garage 再利用)
- Tempo metrics-generator (span metrics のみ、service_graph は off)
- Pyroscope (eBPF profiling)
- Blackbox exporter (家庭用途は優先度低)

---

## 5. 元 doc からの主要な変更点サマリ

| 観点 | 元 doc | 本提案 |
|---|---|---|
| Phase 1 の目的 | 「node_exporter を systemd に統一」(DaemonSet 撤去) | **両経路を残したまま** systemd を全ノードに拡張 |
| node-exporter DaemonSet | 無効化する | **維持** (observability-cascade 対策) |
| 優先順位 | 温度メトリクスが Phase 1 | **通知経路 / PrometheusRule 基盤が先** (Phase 0) |
| Grafana 認証 | CF Access (古い) | **Zitadel OIDC** |
| ダッシュボード運用 | 言及なし | **sidecar + ConfigMap で code 化** |
| incident 参照 | 根拠不明 | **`docs/incidents/` に実体化** |
| 温度メトリクス取得方法 | textfile + vcgencmd 一括 | **温度は hwmon で、スロットル系のみ textfile** |
| ポート設計 | 暗黙的 | **:9100 = DaemonSet / :9101 = systemd** で明示共存 |

---

## 6. 非目標 (やらない)

- **Alloy → OTel Collector 置き換え**: Pi 制約下でメモリ +200〜400Mi 悪化、メリット小
- **rpi_exporter 導入**: 常駐 10〜20MB 無駄。textfile + hwmon で代替
- **Prometheus 水平分散**: 単一で十分
- **kube-proxy / kube-controller-manager / kube-scheduler の個別スクレイプ有効化**: k3s では 1 バイナリなので既に disabled が正解

---

## 7. 関連ファイル (再設計後に触る対象)

- `manifests/platform/kube-prometheus-stack/app/base/values.yaml` — defaultRules / alertmanagerSpec.configSecret
- `manifests/platform/kube-prometheus-stack/externalnodes-monitoring/` → リネーム (`host-monitoring` 等)
- `manifests/platform/grafana/app/base/values.yaml` — sidecar dashboards
- `manifests/platform/alloy/app/base/values.yaml` + overlays — nodeSelector 緩和 / CP 配布
- `provisioner/roles/monitoring_agent/` — systemd node_exporter フラグ追加 / rpi-metrics / alloy-journal
- `provisioner/playbooks/setup_monitoring_agent.yaml` — 対象ホストグループ拡張
- `docs/incidents/2026-04-13-observability-cascade.md` — 新規作成
- `servers.yaml` — EndpointSlice / kubeEtcd endpoints の生成元
