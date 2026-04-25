# 運用手順 (Runbook)

繰り返し実行する運用タスクの手順を集約する。各項目は **「いつ実行するか」「コマンド」「確認方法」「注意点」** の 4 点を最低限含める。

過去のインシデント記録 (1 回きり) は [`docs/incidents/`](incidents/)、提案中・未実装の改善案は [`docs/proposals/`](proposals/) を参照。

| カテゴリ | 項目 |
|----------|------|
| ライフサイクル | [A1 クラスタ全体シャットダウン](#a1-クラスタ全体シャットダウン) / [A2 クラスタ起動](#a2-クラスタ起動) / [A3 k3s だけ停止・起動](#a3-k3s-だけ停止--起動) / [A4 k3s リセットして作り直す](#a4-k3s-リセットして作り直す) |
| ノード操作    | [A5 control-plane leader を安全に再起動](#a5-control-plane-leader-を安全に再起動) / [A6 monitoring agent 再配布](#a6-monitoring-agent-再配布) / [A7 ノード追加・再構築](#a7-ノード追加--再構築) |
| 証明書        | [B1 証明書の手動 renew](#b1-証明書の手動-renew) |
| GitOps        | [B2 Flux 同期トリガ / drift 確認](#b2-flux-同期トリガ--drift-確認) |
| ネットワーク  | [B3 LB IP の付与確認 / 切替テスト](#b3-lb-ip-の付与確認--切替テスト) |
| 認証          | [B4 新 OIDC 保護アプリ追加](#b4-新-oidc-保護アプリ追加) |
| ストレージ    | [B5 Longhorn ボリューム拡張](#b5-longhorn-ボリューム拡張) |
| Secret        | [B6 GitHub App / 1Password Connect token のローテーション](#b6-github-app--1password-connect-token-のローテーション) |

---

## ライフサイクル

### A1. クラスタ全体シャットダウン

**いつ:** 物理移動 / 長期停電予告 / メンテで全停する時。

**手順:**

```sh
# 1. k3s を先に停止 (preflight に必須)
make {env}/provision/k3s-stop

# 2. 順序付きシャットダウン (worker → master → external → gateway1)
make {env}/provision/shutdown-cluster
```

**確認:**

- 各ノードが OS シャットダウンしたら ssh が切れる
- gateway1 だけは最後に落ちる (途中で他 tier が失敗すれば gateway1 は残ったまま中断される設計)

**注意:**

- **必ず `k3s-stop` を先に**。preflight で k3s ActiveState を見て、active なら abort される
- gateway1 を先に落とすと残ノードが互いに到達不能になり、リカバリが面倒

---

### A2. クラスタ起動

**いつ:** A1 から復帰、または初回起動以降の通電。

**手順:**

1. `br-gateway1` を最初に通電 (DHCP / DNS / NTP がないと他ノードが上がれない)
2. gateway1 が ready (`ssh br-gateway1 systemctl is-system-running`) になったら、残ノード全部に通電
3. 各ノードは cloud-init 済 → systemd で k3s/k3s-agent が自動起動 → control-plane VIP が ARP announce される
4. Flux が `manifests/clusters/prod/` を再同期するのを待つ

**確認:**

```sh
# control-plane VIP に api が応答すること
curl -k https://172.22.10.60:6443/healthz

# 全ノード Ready
kubectl get nodes

# Flux 同期状態
flux get kustomizations -A
```

**注意:**

- 起動順を gateway1 → 他、と守ること
- 全停からの復帰時、Longhorn のレプリカ rebuild が走る場合がある (`do-nothing` で rebuild storm は抑制済みだが、復帰直後の一瞬は I/O 増)

---

### A3. k3s だけ停止 / 起動

**いつ:** k3s の設定変更前後 / シャットダウン preflight。

**手順:**

```sh
make {env}/provision/k3s-stop
make {env}/provision/k3s-start
```

**確認:**

- `systemctl status k3s` (master) / `systemctl status k3s-agent` (worker) が `active`
- ノード Ready: `kubectl get nodes`

**注意:**

- worker は k3s-agent、master は k3s service (playbook が群ごとに切り替える)
- 停止中も etcd データは保持される (削除しない)

---

### A4. k3s リセットして作り直す

**いつ:** k3s 設定が壊れた / クラスタを完全に作り直したい。**データは消える** (Longhorn 配下のレプリカファイルも k3s ローカル相当)。

**手順:**

```sh
make {env}/provision/k3s-reset            # 全 k3s ノードで k3s を完全削除
make {env}/provision/setup-node           # k3s 再インストール + Cilium/CoreDNS/kube-vip ブート
make {env}/provision/bootstrap-cluster    # Flux 投入
```

**確認:**

- A2 と同じ手順でクラスタが上がっていること

**注意:**

- **PVC は壊れる**。Longhorn の `/var/lib/longhorn` 配下も再 attach 不能になる
- 学習環境のため Git からの再構築前提だが、Zitadel の tofu state も Secret なので **Zitadel リソースも作り直し**になる
- 本当に必要かよく検討してから実行

---

## ノード操作

### A5. control-plane leader を安全に再起動

**いつ:** etcd / apiserver の memory が膨らんだ時。timer で**自動実行**もされている (毎日 / 偏り検知時)。手動で行う場合の手順。

**手順:**

```sh
# timer のセットアップ (初回のみ。すでに済)
make {env}/provision/setup-k3s-leader-restart
```

手動で 1 ノードだけ再起動する場合は、`provisioner/roles/k3s_leader_restart/` の `restart-k3s-leader.sh` 相当を ssh で叩く形になる。

**確認:**

- `systemctl status k3s` (master) で再起動後に active
- 他の 2 ノードの apiserver TCP 到達性 (`6443/tcp`) が事前にチェックされる (peer health check)

**注意:**

- peer の TCP port 到達性をチェックしてから再起動する。1 台でも `:6443` が落ちていたら abort する設計 (詳細: commit `0e0b449`)
- timer は **leader 偏り防止**目的。3 台のうち 1 台に長時間 leader が留まると memory が偏るのを定期再起動で平準化する

---

### A6. monitoring agent 再配布

**いつ:** systemd `node_exporter` / `alloy` の設定変更後、Alloy config を変えた後。

**手順:**

```sh
make {env}/provision/setup-monitoring-agent
```

**確認:**

```sh
ssh br-node1 'systemctl status node_exporter alloy'
# Prometheus で host-monitoring が scrape できているか
curl -s http://br-node1:9101/metrics | head
```

**注意:**

- 全 8 ノード (gateway / external / 全 k3s ノード) にまたがる
- canary + batched serial で順次配布される (一気に切り替えない)

---

### A7. ノード追加 / 再構築

**いつ:** 新しい Pi を追加 / 既存ノードのディスクが死んだ。

**手順:**

1. `servers.yaml` に新ノードを追加 (再構築なら既存定義そのまま)
2. 1Password Vault `br-cluster-{env}` に新ノードの SSH 鍵 / パスワード / IP / MAC を登録
3. ローカルでイメージ生成

   ```sh
   make {env}/image-build/<hostname>
   ```

4. USB-NVMe SSD にイメージを焼く (`dd`)
5. SSD を Pi に挿して通電 (br-gateway1 配下で DHCP reservation により IP が割当される)
6. inventory 再生成 + プロビジョニング

   ```sh
   make {env}/generate-inventory
   make {env}/provision/setup-node          # k3s 再構成 (新規 worker は join まで)
   make {env}/provision/setup-monitoring-agent
   ```

**確認:**

- `kubectl get nodes` に新ノードが Ready で出る
- Longhorn UI で Disk が認識されレプリカが分散され直す

**注意:**

- 既存 master を再構築する場合、その間 etcd クォーラム喪失リスクあり。**必ず 1 台ずつ**
- worker 再構築なら etcd 影響なし。並列でも可だが Longhorn rebuild storm に注意

---

## 証明書

### B1. 証明書の手動 renew

**いつ:** cert-manager 自動 renew が失敗した / 検証目的で強制的に更新したい。

**手順:**

```sh
# 任意の Certificate を強制的に renew
cmctl renew <name> -n <namespace>
# 例: cluster-gateway 用 wildcard 証明書
cmctl renew cluster-gateway-tls -n envoy-gateway-system
```

**確認:**

```sh
kubectl describe certificate -n envoy-gateway-system cluster-gateway-tls
# Status.Conditions が Ready=True、Renewal Time が更新されている
```

**注意:**

- cert-manager は通常、**有効期限の 2/3 経過時**に自動 renew する。手動 renew は基本不要
- DNS01 self-check が遅延しがち。設定: `dns01RecursiveNameservers: 8.8.8.8:53,1.1.1.1:53`
- Cloudflare API Token (`cert-bot` item) が失効していると失敗する
- 詳細: [`docs/platform/certificate.md`](platform/certificate.md)

---

## GitOps

### B2. Flux 同期トリガ / drift 確認

**いつ:** GitHub に push したけど Flux 待ちしたくない / クラスタ実体と Git の差分を見たい。

**手順:**

```sh
# まず GitRepository を強制 fetch
flux reconcile source git flux-system -n flux-system

# 続けて最上位 Kustomization を再 apply
flux reconcile kustomization flux-system -n flux-system

# 特定の platform app だけ
flux reconcile kustomization cilium-app -n flux-system

# drift 確認 (Git に対する diff)
flux diff kustomization <name> --path ./manifests/...
```

**確認:**

```sh
flux get kustomizations -A
flux get helmreleases -A
```

すべて `READY=True` で、`Last applied revision` が最新コミット SHA になっていること。

**注意:**

- `flux diff` は kustomize build した結果と現状を比較するので、`postBuild.substitute*` の値も埋め込まれた状態で比較される
- リポ全体の reconcile 時間は 30m がデフォルト。緊急時は手動 reconcile

---

## ネットワーク

### B3. LB IP の付与確認 / 切替テスト

**いつ:** `*.b8m.app` が 502 / 接続不可になった / kube-vip 設定を変えた後。

**手順:**

```sh
# 1. 各ノードで VIP が誰かに付与されているか確認
for h in br-node1 br-node2 br-node3 br-node4 br-node5 br-node6; do
  echo "=== $h ==="
  ssh $h 'ip -4 -o addr show eth0 | grep -E "172\.22\.10\.(60|70|71)"'
done

# 2. ARP テーブルから他ノードの認識を確認 (gateway1 から)
ssh br-gateway1 'arp -a | grep -E "172\.22\.10\.(60|70|71)"'

# 3. 直接到達確認
curl -k https://172.22.10.70:443 -H 'Host: cluster-gateway.b8m.app'
```

**確認:**

- `172.22.10.60` (k8s API) はいずれかの control-plane に
- `172.22.10.70` (cluster-gateway) / `172.22.10.71` (internal-gateway) はいずれかのノードに
- どこにも付いていなければ kube-vip / Cilium L2 Announcement の設定を疑う

**注意:**

- 過去事例: 2026-04-25 の grafana.b8m.app 502 → kube-vip `svc_enable=false` で VIP 未付与だった (commit `a71e010` で修正済み)
- ARP 切替直後はクライアント側 ARP cache が古い可能性。複数台から疎通テスト
- 詳細: [`docs/network.md`](network.md#lb-ip-の払い出し方式-重要)、[`docs/platform/networking.md`](platform/networking.md#kube-vip)

---

## 認証

### B4. 新 OIDC 保護アプリ追加

**いつ:** 新しい Web UI を `*.b8m.app` で公開したい。

**手順:** 5 リポ横断の手順なので **[`docs/architecture.md` の「新しい OIDC 保護アプリを追加する手順」](architecture.md#新しい-oidc-保護アプリを追加する手順) を参照**。

**確認:**

- `<name>.b8m.app` にブラウザでアクセス → CF Access (GitHub Org + WARP) → Zitadel ログイン → アプリ画面が出る
- Envoy Gateway のログで `oauth2_callback` が 200 を返す

**注意:**

- Grafana 系 (アプリ自前 OIDC) と Envoy SecurityPolicy 系を混在させると二重 OIDC ダンスになる。**SecurityPolicy を付ける/付けないは事前に決める**
- 詳細: [`docs/platform/identity.md`](platform/identity.md)

---

## ストレージ

### B5. Longhorn ボリューム拡張

**いつ:** PVC が満杯に近い / Prometheus retention を伸ばしたい。

**手順:**

1. Longhorn UI (`https://longhorn.b8m.app`) でボリュームを確認
2. PVC 側を編集して新しいサイズに
   ```sh
   kubectl edit pvc <name> -n <namespace>
   # spec.resources.requests.storage を増やす
   ```
3. Longhorn の online expansion が走るのを待つ (PVC `Bound` 状態のまま)

**確認:**

```sh
kubectl get pvc -n <namespace> <name>
# CAPACITY が新サイズに更新されている
df -h  # アプリ Pod 内で確認
```

**注意:**

- **PVC `Bound` 状態のまま** で実行する。`Released` になると拡張が困難になる
- 縮小はサポートされない
- worker ノードのディスク残量を確認してから実施

---

## Secret

### B6. GitHub App / 1Password Connect token のローテーション

**いつ:** クレデンシャル漏えい / 定期ローテーション。

#### GitHub App credentials (Flux 用)

1. GitHub App の private key を再発行
2. 1Password の `flux-system` 用 item を更新
3. `flux-system` namespace の `flux-system` Secret を上書き
   ```sh
   kubectl -n flux-system delete secret flux-system
   # Ansible bootstrap/secrets を再投入するか手動で kubectl apply
   make {env}/provision/bootstrap-cluster
   ```
4. `flux reconcile source git flux-system -n flux-system` で fetch を確認

#### 1Password Connect

1. 1Password 側で新しい credentials JSON / token を発行
2. ローカルの `.secret/{env}/1password-credentials.json` と `.connect_token` を更新
3. `bootstrap` 経由でクラスタ内 Secret も更新
   ```sh
   make {env}/provision/bootstrap-cluster
   ```
4. クラスタ内の onepassword-connect Pod を再起動
   ```sh
   kubectl -n onepassword rollout restart deployment onepassword-connect
   ```
5. ExternalSecret が再 sync するのを確認 (`kubectl get externalsecret -A`)

**注意:**

- GitHub App の private key を失うと **Flux が Git fetch できなくなる**。再発行は merge できる人がいる時に
- 1Password Connect の Vault 暗号化キーが入った `op-credentials` を失うと、その Connect インスタンスから Vault は開けなくなる (Vault の中身は無事だが、新規 Connect の再セットアップが必要)
- 詳細: [`docs/platform/secrets.md`](platform/secrets.md)

---

## 関連

- [`docs/incidents/`](incidents/) — 過去のインシデント記録 (1 回きり)
- [`docs/proposals/`](proposals/) — 検討中・未実装の改善案
- [`docs/architecture.md`](architecture.md) — 設計判断の背景
- [`Makefile`](../Makefile) — 全コマンドの SoT
