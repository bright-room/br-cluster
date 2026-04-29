# 提案: Istio ambient mode を導入し Pod 間通信を mTLS 化する

> **この提案の位置づけ**
>
> 学習目的で **「Pod 間 (東西方向) の通信を mTLS 化する」** ことを主目標に、Istio
> ambient mode を段階的に導入する提案。サイドカーモードは採用しない (Pi のリソース
> 制約と、サイドカー注入による Pod 再起動・再設計の負担を避けるため)。
>
> 本提案は **「Pod 間が mTLS で繋がっている」状態の達成 (Phase 2 まで)** をゴール
> に置き、L7 ポリシー / waypoint / Kiali 等は学習を深めるためのオプション
> (Phase 3 以降) として位置づける。

## 背景

- 現状の暗号化境界:
  - 南北方向: Cloudflare Tunnel → Envoy Gateway で TLS 終端済み
  - 東西方向 (Pod 間): **平文** (Cilium はデフォルトで Pod 間暗号化なし)
- LAN は閉域 (172.22.10.0/24) だが、学習目的として:
  - SPIFFE / ワークロード ID の概念を触りたい
  - ゼロトラスト (SA 単位の AuthZ) の素振りをしたい
  - Service Mesh のデータプレーン分離 (ztunnel / waypoint) を理解したい

## なぜ ambient mode か (採用 / 不採用 / 理由)

| 候補 | 採否 | 理由 |
|---|---|---|
| **Istio ambient (ztunnel)** | 採用 | Pod 無改修で mTLS、SPIFFE ID 付き、L4 AuthZ 可。Pi リソース節約 |
| Istio sidecar | 不採用 | 全 Pod に envoy 注入 = メモリ倍増、再起動必須。学習価値はあるが Pi で常用は重い |
| Cilium WireGuard | 不採用 | ノード単位暗号化どまり、SA 単位の ID なし。学習対象として既習の Cilium 拡張に閉じる |
| Cilium Mutual Auth (SPIFFE) | 不採用 | 1.17 時点で beta、ドキュメント / 実例少なく学習素材として弱い |
| Linkerd | 不採用 | mTLS 目的なら最有力候補だが、Gateway API / SPIFFE 学習の幅は Istio が広い |

## ゴールと非ゴール

### ゴール (Phase 2 まで)
- 対象 namespace の **Pod ↔ Pod 通信が ztunnel 経由 HBONE (mTLS) で暗号化**される
- `istioctl ztunnel-config` / Hubble で暗号化されていることを観測できる
- ロールバック手順が明文化されている (namespace ラベルを外せば元に戻せる)

### 非ゴール (今回はやらない)
- 全 namespace の一斉 ambient 化 (段階導入する)
- Envoy Gateway の置き換え (南北は Envoy Gateway を維持、Istio Gateway は使わない)
- Envoy `SecurityPolicy` の OIDC を Istio に移植
- Kiali / Grafana Istio dashboard 等の運用 UI 整備 (任意)
- Longhorn / kube-system 等 **インフラ系 namespace の ambient 化** (Phase 4 以降に分離)

## 既知の前提条件 / 競合点

### Cilium との同居 (最大の論点)

[`manifests/platform/cilium/app/base/values.yaml`](../../manifests/platform/cilium/app/base/values.yaml)
の現状値に対して、Istio ambient と同居させるには以下の調整が必要になる見込み。
**Phase 0 (検証) で挙動を確認してから本適用する**。

| 項目 | 現状 | 同居で必要な値 (案) | 影響 |
|---|---|---|---|
| `cni.exclusive` | 既定 (true) | `false` | Istio CNI plugin を chain させる |
| `socketLB.hostNamespaceOnly` | 未設定 | `true` | ztunnel が host netns で listen するため |
| `bpf.masquerade` | 既定 | `false` (要検証) | ambient redirect と二重 NAT を避ける |
| `kubeProxyReplacement` | `true` | 維持 | NodePort 経由の Alloy → `localhost:30800` 経路の挙動再確認が必要 |
| `l2announcements` / `externalIPs` | `true` | 維持 | Service LB は引き続き Cilium |

参考: Istio 公式 "Install with Cilium CNI" / Cilium 公式 "Istio Integration" ドキュメント
(バージョンに合わせて Phase 0 で再確認する)。

### k3s 固有のパス

Istio CNI installer は k3s の CNI ディレクトリを自動検出しない場合があるため、
明示的に上書きする必要がある:

```yaml
# istio-cni values
cni:
  cniBinDir: /var/lib/rancher/k3s/data/cni
  cniConfDir: /var/lib/rancher/k3s/agent/etc/cni/net.d
```

### 既存コンポーネントとの関係

| 既存 | ambient 導入時の方針 |
|---|---|
| Envoy Gateway (南北) | **触らない**。HTTPRoute / SecurityPolicy も現状維持 |
| Cloudflare Tunnel | **触らない**。CF → Envoy → Pod の経路は不変 |
| Hubble | 維持。L4 flow は ambient 後も観測可能 (HBONE 化された TCP として見える) |
| OTel Collector | Istio access log / trace の送り先候補。Phase 3+ で接続検討 |
| External Secrets / cert-manager | Istio CA は istiod 内蔵 (self-signed root) を初期採用、cert-manager 連携は Phase 4+ |

## Phase 0: 事前計測・前提検証 (実装前必須)

### 0-1. リソース余力の確認
- br-node2 が CPU 107% で慢性高負荷 (2026-04-29 時点)。**この問題が解決するまで Phase 1 以降に進まない**
  → 別途 `node2-cpu-investigation` として切り出し、本提案とは独立の Phase 0 タスクとして扱う
- worker (br-node4-6) のメモリ余力は 30-50% あり、ztunnel 追加分 (ノードあたり ~100 MiB) は許容範囲

### 0-2. Cilium + Istio ambient 同居の文献レビュー
- 上記「同居で必要な値 (案)」を Istio / Cilium 公式の最新ドキュメントで突き合わせる
- バージョン pin: 検証時点の最新安定版 (Istio 1.27+ / Cilium は現行) を前提
- floating tag は Conftest ポリシーで弾かれる ([`policies/`](../../policies/)) ので install 時に digest / tag を pin

### 0-3. ロールバック手順のドライラン (机上)
- ambient ラベル除去 → 当該 namespace の通信が平文に戻ることを確認
- istio uninstall (helm) → Cilium values を元に戻せばクラスタ機能は完全復帰可能、を確認

## Phase 1: Istio install (mesh は空、データプレーン未稼働)

### 1-1. manifests 構成

[`manifests/platform/`](../../manifests/platform/) 配下に以下を新規追加:

```
manifests/platform/istio/
├── app/
│   ├── base/
│   │   ├── kustomization.yaml
│   │   ├── helmrepository.yaml          # oci://gcr.io/istio-release/charts
│   │   ├── helmrelease-base.yaml        # istio-base (CRDs)
│   │   ├── helmrelease-istiod.yaml      # istiod (control plane)
│   │   ├── helmrelease-cni.yaml         # istio-cni (k3s パス指定)
│   │   └── helmrelease-ztunnel.yaml     # ztunnel (DaemonSet)
│   └── overlays/prod/
│       └── values.yaml                  # ambient profile, k3s 用 CNI パス
└── config/
    └── base/
        └── kustomization.yaml           # 将来の AuthorizationPolicy 等
```

[`manifests/clusters/prod/platform/kustomization.yaml`](../../manifests/clusters/prod/platform/kustomization.yaml)
に `istio-app.yaml` を登録。

### 1-2. Cilium values 変更

[`manifests/platform/cilium/app/base/values.yaml`](../../manifests/platform/cilium/app/base/values.yaml)
に Phase 0 で確定した値を追加:

```yaml
cni:
  exclusive: false
socketLB:
  hostNamespaceOnly: true
# bpf.masquerade は Phase 0 検証で決定
```

**重要**: Cilium values の変更は Flux 経由で順次適用されるため、Phase 1 のデプロイ
順は **「Cilium values 適用 → Cilium pod の rollout 完了確認 → Istio install」**
の順を厳守する。逆順だと Istio CNI が Cilium の exclusive モードで弾かれる。

### 1-3. ambient profile 指定

```yaml
# helmrelease-istiod.yaml (抜粋)
spec:
  values:
    profile: ambient
    pilot:
      env:
        PILOT_ENABLE_AMBIENT: "true"
```

### 1-4. 検証
- `kubectl -n istio-system get pod` で istiod / istio-cni-node / ztunnel が Ready
- 既存ワークロードの通信が **壊れていない** ことを確認 (ambient ラベル未付与なので影響しないはずだが要確認)
- Hubble で平文 TCP が観測できていることを確認 (まだ暗号化していない状態の baseline)

## Phase 2: 1 namespace を ambient に取り込み (mTLS 達成)

### 2-1. 対象 namespace 選定基準
- **stateless かつ 1-2 Pod の小さいワークロード**から始める
- **インフラ系 namespace (`kube-system` / `cilium-secrets` / `flux-system` / `longhorn-system` 等) は対象外**
- 候補: `default` に学習用の sample app をデプロイ → これを ambient 化 (本番ワークロードに最初から触らない)

### 2-2. 適用
```sh
kubectl label ns <target> istio.io/dataplane-mode=ambient
```

### 2-3. 検証
- `istioctl ztunnel-config workload` で対象 Pod が ztunnel 配下に入っているか確認
- 対象 Pod 間通信が **HBONE (mTLS)** になっていることを `istioctl proxy-config` で確認
- Pod に SPIFFE ID (`spiffe://cluster.local/ns/<ns>/sa/<sa>`) が付与されていることを確認
- アプリの動作確認 (HTTP リクエスト・レスポンスが正常)
- Hubble で暗号化された TCP として観測できることを確認

### 2-4. ロールバック
```sh
kubectl label ns <target> istio.io/dataplane-mode-
```
ラベル除去後、即座に平文に戻る (Pod 再起動不要)。

**Phase 2 完了時点で本提案の主目標 (Pod 間 mTLS 化) は達成。Phase 3 以降は学習深掘りオプション。**

## Phase 3 (任意): L4 AuthorizationPolicy で SA 単位の認可を試す

```yaml
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: deny-all
  namespace: <target>
spec:
  {}  # 空 spec = deny-all
---
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata:
  name: allow-from-frontend
  namespace: <target>
spec:
  selector:
    matchLabels:
      app: backend
  rules:
    - from:
        - source:
            principals: ["cluster.local/ns/<target>/sa/frontend"]
```

- 「frontend SA からは backend に通る、それ以外は弾く」が動くことを確認
- 学べる概念: SPIFFE principal、deny-by-default 設計

## Phase 4 (任意): waypoint proxy で L7 を追加

- waypoint proxy は **Gateway API リソース (`gatewayClassName: istio-waypoint`)** として定義
- namespace または ServiceAccount 単位で配置
- 学べる概念: ambient の L4/L7 分離設計、Gateway API の二重活用 (Envoy Gateway は南北、Istio waypoint は東西)

## リスク

| リスク | 影響 | 緩和策 |
|---|---|---|
| Cilium values 変更で既存 Pod ネットワーク断 | クラスタ全停 | Phase 1 でまず Cilium values だけを先行適用し rollout 完了確認 → 異常時は revert |
| Istio CNI と Cilium CNI の chaining 不整合 | 新規 Pod がスケジュール不可 | Phase 0 で同居設定値を確定。問題発生時は istio-cni の DaemonSet を停止すれば Cilium 単独に戻る |
| ztunnel が br-node2 に追加負荷を載せる | CP 高負荷悪化 | br-node2 CPU 107% を **本提案より先に解決**してから Phase 1 へ |
| ambient 化対象 namespace の通信が一部破綻 | 当該アプリ停止 | namespace ラベル除去で即時ロールバック (Phase 2-4 検証済み) |
| Istio control plane が落ちると新規 mTLS 接続不可 | データプレーンの新規接続不可 (既存接続は維持) | istiod は最初は replicas=1 で OK (学習目的)、不安定なら replicas=2 に上げる |
| Cilium / Istio の OSS バージョン不整合 | 同居壊れる | Renovate で同時更新を抑制、major upgrade は手動で 1 つずつ |

## 期待効果 (学習面)

- **触れる新概念**: SPIFFE / SVID, HBONE, ztunnel, waypoint, AuthorizationPolicy, Gateway API の二重活用
- **既存知識の補強**: Cilium CNI chaining の挙動、k3s の CNI パス
- **将来の選択肢**: Zitadel など機微サービスの SA 単位 AuthZ への発展余地

## 作業範囲

### コード追加
- `manifests/platform/istio/` ツリー新規 (上記 1-1 構成)
- `manifests/clusters/prod/platform/kustomization.yaml` への登録
- `manifests/clusters/prod/platform/istio-app.yaml` 新規

### コード変更
- `manifests/platform/cilium/app/base/values.yaml` に同居用キー追加

### ドキュメント追加
- `docs/platform/service-mesh.md` (新規グループ)
- `docs/README.md` のグループ表に Service Mesh 行追加
- `docs/architecture.md` の「主要な設計判断」に "なぜ Istio ambient か" 追記

### Policy 例外
- 想定なし。Helm chart は version pin、Secret は istiod 自動生成 (現状ポリシーに抵触しない見込み) — Phase 1 で `make policy/test` を通して確認

## 未決事項 / Phase 0 で決めること

- Cilium 同居用の正確な values (`bpf.masquerade` の必要性、その他オプション)
- Istio バージョン pin (検証時点の最新安定版)
- 初回 ambient 化対象 namespace (sample app 用に新規 ns を作るか、`default` を使うか)
- istiod の replicas (初期 1 で十分か)
- Phase 0 の前提となる **br-node2 CPU 107% 問題の切り分け** (本提案外で先行)

## 関連

- 主目標達成までの最短経路: Phase 0 → 1 → 2
- 学習深掘り経路: Phase 3 (AuthZ) → Phase 4 (waypoint / L7)
- 関連既存ドキュメント:
  - [`docs/platform/networking.md`](../platform/networking.md) — Cilium / Envoy Gateway の現状
  - [`docs/architecture.md`](../architecture.md) — 認証 2 層の設計
  - [`CLAUDE.md`](../../CLAUDE.md) — chicken-and-egg 依存、設計方針

## 更新履歴

- 2026-04-29 初版
