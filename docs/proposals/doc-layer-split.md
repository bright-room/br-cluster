# 提案: 物理レイヤと k3s レイヤの doc 分離

> **位置づけ**
>
> `docs/hardware.md` と `docs/network.md` に物理 (SBC・配線・L2/L3・nftables) と
> k3s/プラットフォーム (control-plane HA, kube-vip, Cilium LB-IPAM, Envoy Gateway,
> cloudflared) の情報が混在している。受け皿となる `docs/kubernetes.md` /
> `docs/platform/networking.md` は既に存在するため、**新規ファイルは作らず既存
> doc 間で内容を再配分する** リファクタリングを提案する。

## 背景

### 現状の問題

`hardware.md` は物理ホストの doc のはずだが:

- `### br-node1-6 (k3s)` 節に k3s role (primary/secondary/worker)、etcd メンバー、
  control-plane VIP `172.22.10.60` の kube-vip ARP の話が入っている
- `### br-gateway1` の "主要 NAT" に `WAN:6443 → 172.22.10.60:6443` (k8s API VIP)
  が出てくるが、この VIP は k8s レイヤの概念

`network.md` は L2/L3 + DHCP/DNS/nftables の doc のはずだが:

- `### LB IP の払い出し方式 (重要)` が Cilium LB-IPAM の話 (k8s レイヤ)
- `## 外部公開フロー` が cloudflared → Envoy Gateway → Pod の話 (k8s レイヤ)
- `## クラスタ内限定の経路 (internal-gateway)` が Gateway API リソースの話 (k8s レイヤ)

### 受け皿の現状

既存の `docs/kubernetes.md` には `## トポロジ` 節があるが、ノード別の k3s role 表は
持っていない (`hardware.md` を参照する形)。`docs/platform/networking.md` には
Cilium / kube-vip / Envoy Gateway / cloudflared / external-dns の各節があり、
**LB IP 固定**の節も Envoy Gateway 配下に既存 — 内容は `network.md` 側と重複している。

つまり受け皿は揃っており、やることは **重複削除と移管** が中心。

## 提案する分離方針

| 観点 | 物理レイヤ doc | k3s/プラットフォーム doc |
|------|--------------|--------------------------|
| 対象 | OS から下 (SBC, USB-NVMe, NIC, スイッチ, ケーブル, 自宅ルーター) | k3s 以上 (control-plane, etcd, CNI, Service LB, Gateway API) |
| ファイル | `hardware.md`, `network.md` | `kubernetes.md`, `platform/networking.md` |
| 読者ペルソナ | 「ハードを組み直す」「nftables を弄る」 | 「k3s クラスタ運用」「マニフェスト変更」 |

**原則**: 1 つの事実は 1 ヶ所にだけ書く。元の場所には 1 行のスタブとリンクを残す。

## 移管マップ

### `docs/hardware.md` から動かす

| 元の節 | 移管先 | 残すもの |
|--------|--------|----------|
| `### br-node1-6 (k3s)` の k3s role 表 / control-plane VIP / kube-vip ARP | `kubernetes.md` `## トポロジ` | 「k3s レイヤの役割は [`kubernetes.md`](kubernetes.md) 参照」の 1 行 |
| `### br-gateway1` の `主要 NAT: DNAT WAN:6443 → 172.22.10.60:6443` | `network.md` の NAT 節 (DNAT は物理 nftables の責務だが、宛先 VIP の説明は `kubernetes.md` にリンク) | NAT ルール自体は `network.md` に残す (gateway1 の機能なので) |

### `docs/network.md` から動かす

| 元の節 | 移管先 | 残すもの |
|--------|--------|----------|
| `### LB IP の払い出し方式 (重要)` | `platform/networking.md` の Cilium 節 (LB-IPAM) と Envoy Gateway `### LB IP 固定` (既存) に統合 | `## ホスト IP / VIP` 表に注釈「LB IP の払い出し機構は [`platform/networking.md`](platform/networking.md#cilium) 参照」 |
| `## 外部公開フロー (https://<svc>.b8m.app)` | `platform/networking.md` に新節 `## 外部公開フロー` (sequence/flow 図 + cloudflared/Envoy/external-dns の連携) | 1 行スタブ |
| `## クラスタ内限定の経路 (internal-gateway)` | `platform/networking.md` の Envoy Gateway `### Gateway 一覧` を増補 | 1 行スタブ |
| API VIP `172.22.10.60` の kube-vip 言及 | `kubernetes.md` `## トポロジ` (kube-vip による announce) | `## ホスト IP / VIP` 表には IP だけ残す |

### 受け皿側で増やす

| ファイル | 増やす内容 |
|----------|-----------|
| `kubernetes.md` `## トポロジ` | k3s role 表 (現 `hardware.md` 由来)、control-plane HA、kube-vip ARP の概要 |
| `platform/networking.md` | `## 外部公開フロー` 節 (新設)、`## Envoy Gateway → ### Gateway 一覧` に internal-gateway を追加 |

## Phase 分け (PR 分割)

1 PR にまとめると diff が大きく review コストが高いので 2 段階に分ける。

### Phase 1: `hardware.md` 側の整理

- `### br-node1-6 (k3s)` を `kubernetes.md` `## トポロジ` に移管
- `hardware.md` 側にはスタブを残す
- `kubernetes.md` の `## トポロジ` を表ベースで再構築

スコープが小さく、k3s/プラットフォーム doc 群は触らないので低リスク。

### Phase 2: `network.md` 側の整理

- LB IP 払い出しの重複を `platform/networking.md` に集約
- `## 外部公開フロー` / `## クラスタ内限定の経路 (internal-gateway)` を
  `platform/networking.md` に移管
- `network.md` を「物理ネットワーク (gateway1 の機能)」に絞る

`platform/networking.md` への追記が中心。Phase 1 の後にやることで衝突を避ける。

## リスクと対処

| リスク | 対処 |
|--------|------|
| 既存 PR / Issue / 外部リンクが移動した節を指している | 元の場所に **1 行スタブ + リンク** を残す。完全削除はしない |
| 1 ノードのフルプロファイル (物理 + k3s 役割) を見るのに 2 doc 必要になる | `hardware.md` のノード一覧表に "k3s role" 列を残し、詳細は `kubernetes.md` リンクという形でクロスリファレンス |
| `platform/networking.md` が肥大化する | `## 外部公開フロー` は flow 図 + 短い説明にとどめ、各コンポーネント (cloudflared / Envoy / external-dns) の詳細は既存節に任せる |
| ドキュメント側だけ整理してコードと doc が逆に乖離 | 移管時にコードを再確認し、CLAUDE.md の「コードが SoT」原則を守る |

## 採用しない選択肢

| 案 | 不採用理由 |
|----|-----------|
| 新規 `docs/cluster-topology.md` を作って k3s レイヤを集約 | 既存 `kubernetes.md` `## トポロジ` と責務が重複し、doc 数だけ増える |
| `hardware.md` / `network.md` を完全に物理だけに削る (スタブも残さない) | 既存リンクが切れる。homelab とはいえドキュメントの履歴互換は保ちたい |
| 何もしない | mermaid 図のリッチ化を進めると、混在の見にくさがより目立つ (今回の議論の発端) |

## Open Questions (要レビュー)

- [ ] `hardware.md` のノード一覧表に "k3s role" 列を残す vs 完全に物理 role (gateway/external/node) のみにする — 後者の方が純度は高いが、運用時の見通しは前者の方が良い
- [ ] `network.md` の `## ホスト IP / VIP` 表で API VIP / Envoy LB-IP をどう扱うか
  (列としては残すが「払い出しは k8s レイヤ」と注釈、で十分か)
- [ ] Phase 1 と Phase 2 の間にどれくらい間を空けるか (連続でやるか、Phase 1 をしばらく運用してから Phase 2 を判断するか)

## 関連

- [`docs/hardware.md`](../hardware.md) — Phase 1 の対象
- [`docs/network.md`](../network.md) — Phase 2 の対象
- [`docs/kubernetes.md`](../kubernetes.md) — Phase 1 の主な受け皿
- [`docs/platform/networking.md`](../platform/networking.md) — Phase 2 の主な受け皿
- [CLAUDE.md](../../CLAUDE.md) — 「大きな変更は proposal を先に書く」ルール
