# 可観測性 メンタルモデル

「どのサーバーから / k3s から / Pod から / 何を取って / どこへ送るか」を整理した視覚化ドキュメント。
色の意味は全図共通:

- 🟦 青 = 現状 **取れている**
- 🟥 赤 = 現状 **取れていない** (ギャップ)
- 🟩 緑 = 理想で **追加する**

---

## 1. サーバー役割ごとのシグナル収集マトリクス

### 現状

```mermaid
flowchart TB
  classDef ok fill:#326ce5,color:#fff
  classDef gap fill:#f44336,color:#fff
  classDef na fill:#bdbdbd,color:#000

  subgraph gw["br-gateway1 (LAN edge / nftables / DHCP)"]
    direction LR
    gw_m["メトリクス<br/>systemd node_exporter (素)"]:::ok
    gw_l["ログ<br/>journald 未収集"]:::gap
    gw_t["トレース<br/>対象なし"]:::na
  end

  subgraph ex["br-external1 (Garage S3)"]
    direction LR
    ex_m["メトリクス<br/>systemd node_exporter (素)<br/>⚠ Garage メトリクス未接続"]:::gap
    ex_l["ログ<br/>journald 未収集"]:::gap
    ex_t["トレース<br/>対象なし"]:::na
  end

  subgraph cp["br-node1〜3 (k3s CP / Pi 4GB)"]
    direction LR
    cp_m["メトリクス<br/>DaemonSet node-exporter<br/>+ etcd :2381"]:::ok
    cp_l["ログ<br/>Pod ログ未収集 (Alloy なし)<br/>journald 未収集"]:::gap
    cp_t["トレース<br/>apiserver trace 未設定"]:::gap
  end

  subgraph wk["br-node4〜6 (k3s worker / Pi 8GB)"]
    direction LR
    wk_m["メトリクス<br/>DaemonSet node-exporter<br/>+ kubelet / cAdvisor<br/>+ ServiceMonitor 各種"]:::ok
    wk_l["ログ<br/>Alloy tail → Loki ✓"]:::ok
    wk_t["トレース<br/>アプリ OTLP 受信 ✓"]:::ok
  end
```

### 理想

```mermaid
flowchart TB
  classDef ok fill:#326ce5,color:#fff
  classDef new fill:#43a047,color:#fff

  subgraph gw["br-gateway1"]
    direction LR
    gw_m["メトリクス<br/>既存 + <br/>🟩 nftables counters<br/>🟩 conntrack<br/>🟩 hwmon (温度)<br/>🟩 RPi textfile (スロットル/電圧)"]:::new
    gw_l["ログ<br/>🟩 alloy (systemd)<br/>→ journald → Loki<br/>(nftables / kea-dhcp /<br/> sshd / systemd)"]:::new
    gw_t["— N/A —"]:::ok
  end

  subgraph ex["br-external1"]
    direction LR
    ex_m["メトリクス<br/>既存 + <br/>🟩 Garage :3903/metrics<br/>🟩 hwmon (温度)<br/>🟩 RPi textfile"]:::new
    ex_l["ログ<br/>🟩 alloy (systemd)<br/>→ journald → Loki<br/>(garage / systemd)"]:::new
    ex_t["— N/A —"]:::ok
  end

  subgraph cp["br-node1〜3 (CP)"]
    direction LR
    cp_m["メトリクス<br/>既存 DaemonSet 維持 + <br/>🟩 systemd node_exporter:9101<br/>  (hwmon + RPi textfile)<br/>🟩 etcd endpoints を動的化"]:::new
    cp_l["ログ<br/>🟩 Alloy DaemonSet を CP にも<br/>  (resources 絞り / 影モード先行)<br/>🟩 k3s journald も同経路で"]:::new
    cp_t["トレース<br/>🟩 apiserver tracing OTLP<br/>  (デバッグ時のみ)"]:::new
  end

  subgraph wk["br-node4〜6 (worker)"]
    direction LR
    wk_m["メトリクス<br/>既存維持 + <br/>🟩 systemd node_exporter:9101<br/>  (hwmon + RPi textfile)"]:::new
    wk_l["既存維持"]:::ok
    wk_t["既存維持"]:::ok
  end
```

---

## 2. k3s 自身から取れるもの

k3s は **control-plane コンポーネントが 1 バイナリに同梱**されているのが普通の k8s と違う点。個別の endpoint を叩くのではなく、kubelet + etcd だけで大半が取れる。

```mermaid
flowchart LR
  classDef ok fill:#326ce5,color:#fff
  classDef gap fill:#f44336,color:#fff
  classDef new fill:#43a047,color:#fff
  classDef disabled fill:#bdbdbd,color:#000

  subgraph k3s_bin["k3s バイナリ (1 プロセス)"]
    api["kube-apiserver"]
    kcm["controller-manager"]
    sched["scheduler"]
    kubelet["kubelet :10250<br/>/metrics<br/>/metrics/cadvisor<br/>/metrics/resource"]
    kproxy["kube-proxy<br/>(Cilium で代替 / 無効)"]
    etcd_p["組込み etcd :2381"]
  end

  subgraph scrape["現状スクレイプ"]
    s_kubelet["kubelet ✓"]:::ok
    s_etcd["etcd (CP 3 IP ハードコード)"]:::ok
    s_api["apiserver ServiceMonitor ❌"]:::gap
    s_kcm["controller-manager ❌<br/>(k3s 統合のため disabled)"]:::disabled
    s_sched["scheduler ❌<br/>(同上)"]:::disabled
    s_kproxy["kube-proxy ❌ (Cilium 置換)"]:::disabled
  end

  subgraph ideal["理想追加"]
    i_api["🟩 apiserver metrics<br/>(kubelet:10250 経由 or<br/> :6443/metrics に ClusterRole 付与)"]:::new
    i_etcd["🟩 etcd endpoints 動的化<br/>(servers.yaml → EndpointSlice)"]:::new
    i_events["🟩 Kubernetes Events<br/>(event-exporter → Loki)"]:::new
    i_audit["🟩 audit log → Loki<br/>(k3s --audit-log-path)"]:::new
    i_apitrace["🟩 apiserver tracing<br/>(--tracing-config, 調査時のみ)"]:::new
  end

  kubelet --- s_kubelet
  etcd_p --- s_etcd
  api --- s_api
  kcm --- s_kcm
  sched --- s_sched
  kproxy --- s_kproxy

  s_api -.-> i_api
  s_etcd -.-> i_etcd
  api -.-> i_events
  api -.-> i_audit
  api -.-> i_apitrace
```

**要点**:
- `kubeControllerManager` / `kubeScheduler` / `kubeProxy` を `enabled: false` にしているのは**正解** (k3s では別エンドポイントで叩けない)
- kubelet / etcd / kube-state-metrics / node-exporter で **k8s 稼働状況のほぼ全てが取れる**
- 追加するなら **apiserver metrics** (API 応答性 / admission latency) と **Events** (Pod 再起動理由など、メトリクスで拾えない情報) が価値高い

---

## 3. 動いている Pod から取れるもの

```mermaid
flowchart LR
  classDef ok fill:#326ce5,color:#fff
  classDef new fill:#43a047,color:#fff
  classDef gap fill:#f44336,color:#fff

  subgraph pod["1 つの Pod"]
    me["/metrics エンドポイント<br/>(アプリが exposed している場合)"]
    stdout["stdout / stderr"]
    otlp["OTLP client<br/>(instrumented アプリ)"]
    fs["Pod 内 /var/log/*<br/>(一部のみ)"]
  end

  subgraph current["現状の捕捉"]
    c_m["ServiceMonitor / PodMonitor 経由 ✓<br/>(Helm values で有効化済の<br/> cilium / envoy / coredns / cert-manager /<br/> external-dns / longhorn / cnpg /<br/> alloy / otel / loki / tempo / grafana /<br/> metrics-server)"]:::ok
    c_l["/var/log/pods/** を Alloy が tail ✓<br/>(ただし worker のみ)"]:::ok
    c_t["OTLP → Alloy → Collector → Tempo ✓"]:::ok
  end

  subgraph ideal_pod["理想追加"]
    i_kse["🟩 kube-state-metrics は既にあるが<br/> 追加 label で役割を可視化"]:::new
    i_kev["🟩 kubernetes-event-exporter<br/> → Loki で Pod 再起動理由を時系列化"]:::new
    i_envoy["🟩 Envoy access log<br/> (OTLP or file) → Loki<br/> L7 障害調査に効く"]:::new
    i_hub["🟩 Hubble flow log → Loki<br/> (ネットワーク層の to/from)"]:::new
    i_cp_log["🟩 CP 上の Pod ログ<br/>(Alloy を CP にも展開)"]:::new
  end

  me --> c_m
  stdout --> c_l
  otlp --> c_t

  c_l -.-> i_cp_log
  stdout -.-> i_kev
  me -.-> i_envoy
  me -.-> i_hub
```

**要点**:
- 大半のプラットフォーム Pod は `/metrics` を持っていて Helm values で ServiceMonitor が有効化されている (Explore 調査で 12 種類以上確認)
- **足りないのは「stdout 以外の観測情報」**: Kubernetes Events / Envoy アクセスログ / Hubble flow
- アプリ独自の観測 (RED / USE / SLI) は**アプリを instrument して OTLP で吐く** → 既存経路で Tempo + Prometheus remote_write に乗る

---

## 4. 全体経路

### 現状

```mermaid
flowchart LR
  classDef host fill:#fff3e0,color:#000
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef ui fill:#f9a825,color:#000
  classDef gap fill:#ef9a9a,color:#000,stroke:#d32f2f,stroke-dasharray:4 2

  %% sources
  sys_ne["systemd<br/>node_exporter<br/>(gw / ext のみ)"]:::host
  ds_ne["DaemonSet<br/>node-exporter<br/>(br-node1〜6)"]:::pod
  ksm["kube-state-metrics"]:::pod
  kubelet["kubelet/cAdvisor"]:::pod
  etcd["etcd :2381"]:::pod
  sm_pool["ServiceMonitor 群<br/>(プラットフォーム Pod)"]:::pod

  pod_log["/var/log/pods<br/>(worker のみ)"]:::host
  alloy_wk["Alloy DS<br/>(worker only)"]:::pod
  app["アプリ OTLP"]:::pod

  cp_log["CP Pod ログ"]:::gap
  j_gw["gw journald"]:::gap
  j_ex["ext journald"]:::gap

  otelc["OTel Collector<br/>tail_sampling / batch"]:::pod

  prom[("Prometheus<br/>14d / 50GB")]:::sink
  loki[("Loki<br/>Garage S3")]:::sink
  tempo[("Tempo<br/>Garage S3")]:::sink
  am["Alertmanager<br/>(receiver 未設定)"]:::gap
  graf["Grafana<br/>(dashboards 手動 ⚠)"]:::ui

  sys_ne -.-> prom
  ds_ne -.-> prom
  ksm -.-> prom
  kubelet -.-> prom
  etcd -.-> prom
  sm_pool -.-> prom

  pod_log --> alloy_wk --> loki
  app -->|OTLP| alloy_wk
  alloy_wk -->|traces+metrics| otelc
  alloy_wk -->|logs| loki
  otelc -->|metrics remote_write| prom
  otelc -->|traces| tempo
  otelc -->|logs OTLP HTTP| loki

  cp_log -.-x loki
  j_gw -.-x loki
  j_ex -.-x loki

  prom --> am
  prom --> graf
  loki --> graf
  tempo --> graf
  user["User (Zitadel OIDC)"] --> graf
```

### 理想

```mermaid
flowchart LR
  classDef host fill:#fff3e0,color:#000
  classDef pod fill:#326ce5,color:#fff
  classDef sink fill:#f44336,color:#fff
  classDef ui fill:#f9a825,color:#000
  classDef new fill:#43a047,color:#fff

  %% hosts — now unified
  sys_ne["🟩 systemd node_exporter<br/>(全 8 ノード :9101)<br/>hwmon + textfile(vcgencmd)"]:::new
  ds_ne["DaemonSet node-exporter<br/>(br-node1〜6 / 既存維持)"]:::pod

  ksm["kube-state-metrics"]:::pod
  kubelet["kubelet/cAdvisor"]:::pod
  etcd["🟩 etcd (EndpointSlice 動的化)"]:::new
  sm_pool["ServiceMonitor 群"]:::pod
  api_m["🟩 kube-apiserver metrics"]:::new

  %% logs
  pod_log_wk["/var/log/pods (worker)"]:::host
  alloy_wk["Alloy DS (worker)"]:::pod
  pod_log_cp["🟩 /var/log/pods (CP)"]:::new
  alloy_cp["🟩 Alloy DS (CP, 軽量)"]:::new

  j_host["🟩 journald (gw + ext + CP)"]:::new
  alloy_host["🟩 alloy systemd<br/>(monitoring_agent role)"]:::new

  kev["🟩 event-exporter<br/>(K8s Events → Loki)"]:::new
  envoy_al["🟩 Envoy アクセスログ"]:::new
  hubble_fl["🟩 Hubble flow log"]:::new

  %% traces
  app["アプリ OTLP"]:::pod

  otelc["OTel Collector<br/>tail_sampling / batch"]:::pod

  %% sinks + rules + alerts
  prom[("Prometheus")]:::sink
  rules["🟩 PrometheusRule<br/>- kubernetes-mixin<br/>- RPi 温度/スロットル<br/>- longhorn / cnpg / cert-manager<br/>- TargetDown / SelfMonitor"]:::new
  am["Alertmanager"]:::pod
  recv["🟩 receiver<br/>(Discord / ntfy)"]:::new
  loki[("Loki")]:::sink
  tempo[("Tempo")]:::sink

  graf["Grafana"]:::ui
  sidecar["🟩 dashboard sidecar<br/>(ConfigMap → Grafana)"]:::new

  sys_ne -.-> prom
  ds_ne -.-> prom
  ksm -.-> prom
  kubelet -.-> prom
  etcd -.-> prom
  sm_pool -.-> prom
  api_m -.-> prom

  pod_log_wk --> alloy_wk --> loki
  pod_log_cp --> alloy_cp --> loki
  j_host --> alloy_host --> loki
  kev --> loki
  envoy_al --> loki
  hubble_fl --> loki

  app -->|OTLP| alloy_wk
  alloy_wk -->|traces+metrics| otelc
  alloy_wk -->|logs| loki
  alloy_cp -->|logs only| loki
  otelc -->|metrics remote_write| prom
  otelc -->|traces| tempo

  prom --> rules --> am --> recv
  sidecar --> graf
  prom --> graf
  loki --> graf
  tempo --> graf
  user["User"] --> zit["Zitadel OIDC"] --> graf
```

---

## 5. 「何を見たいか」→「どこから取るか」早見表

| 見たい情報 | シグナル | 源泉 |
|---|---|---|
| ノード CPU/メモリ/ディスク/ネット | metrics | node_exporter (現状は DaemonSet or systemd) |
| **RPi 温度/スロットル/電圧** | metrics | 🟩 hwmon + textfile collector (vcgencmd) |
| k8s オブジェクトの状態 (desired vs current) | metrics | kube-state-metrics |
| コンテナ CPU/メモリ 実使用 | metrics | kubelet /metrics/cadvisor |
| etcd の健全性 (raft leader, latency) | metrics | etcd :2381 |
| Pod が何回再起動したか (時系列) | logs | 🟩 event-exporter → Loki |
| Pod の stdout (アプリログ) | logs | Alloy tail (現状 worker only → CP も) |
| **nftables でパケットがどれだけ落ちたか** | metrics/logs | 🟩 nftables counters + journald |
| **Garage (S3) の健全性** | metrics | 🟩 Garage :3903/metrics |
| **どの HTTP リクエストが 5xx 返してるか** | logs | 🟩 Envoy アクセスログ |
| **Pod A が Pod B と通信できているか** | logs | 🟩 Hubble flow log |
| アプリのリクエスト latency 分布 | metrics/traces | アプリ instrument → OTLP |
| クロスサービスのリクエスト追跡 | traces | アプリ instrument → Tempo |
| 温度が 75℃ 超えたら通知 | alert | 🟩 PrometheusRule → Alertmanager → Discord |

---

## 6. まとめ: 何から手を付けるべきか

**2026-04-23 時点で Phase 0 + Phase 1 完了**。元の 2 つの穴は埋まった:

| 元の穴 | 解消状態 |
|---|---|
| 通知経路が開通していない | ✅ Discord receiver + 25 PrometheusRule + 13 ダッシュボード (code 化) + externalUrl で Source リンクまで機能 |
| ホスト OS 層の可視化が分散 | ✅ systemd node_exporter を全 8 ノードに展開 (:9101)、温度 / クロック / 電圧 / スロットルまで可視化、RPi 専用アラートあり |

残りのギャップ (Phase 2 候補、優先度順は `observability-plan.md`):

- 🟨 **CP ノードの Pod ログ** が未収集 (Alloy が worker only、cascade 再発リスクで慎重に)
- 🟨 **外部ノードの journald** が未収集 (monitoring_agent role が広がったので追加容易)
- 🟨 **K8s Events** が Loki に流れていない (event-exporter で解消可能)
- 🟨 **Envoy アクセスログ** が Loki に無い (L7 可視化、サンプリング前提)
- 🟨 **Hubble flow log** が Loki に無い (L3/4 可視化、drop 判定のみから)
- 🟨 **kubeEtcd endpoints** がハードコード (servers.yaml からの生成化は nice-to-have)

詳細は `observability-plan.md` の Phase 2 セクション。
