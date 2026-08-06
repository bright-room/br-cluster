# Runbook: k3s クラスタアップグレード

system-upgrade-controller (SUC) 経由で k3s をアップグレードする手順。

> **Phase 1b 着地 (2026-06 時点)**
>
> `nodeSelector` を本番並び (server-plan = control-plane、agent-plan = worker)
> に解放し、`window` (02:00-05:00 JST) を有効化した。事前 etcd snapshot は
> **k3s 組み込みの自動スナップショット (12h ごと・全 CP)** をベースラインとして
> 利用する。オンデマンド取得用の `make prod/k3s/snapshot` と `versions.yaml`
> 同期は **provisioner 別 proposal のまま未着手** (受け入れ基準 #9 / #11)。
>
> Phase の定義は [`docs/proposals/k3s-upgrade.md`](../proposals/k3s-upgrade.md) 参照。

## 前提

| 項目 | 値 |
|------|-----|
| アップグレード方式 | SUC + `Plan` CRD (`upgrade.cattle.io/v1`) |
| Plan SoT | [`manifests/platform/system-upgrade-controller/app/base/{server,agent}-plan.yaml`](../../manifests/platform/system-upgrade-controller/app/base/) |
| バージョン追跡 | Renovate customManager (`renovate.json` 内 `manifests/platform/system-upgrade-controller/.+\.ya?ml$` 対象) |
| Renovate group | `k3s` (server-plan + agent-plan を 1 PR にまとめる) / `system-upgrade-controller` (controller + CRD URL を 1 PR にまとめる) |
| etcd snapshot | k3s 組み込み自動スナップショット (12h ごと・全 CP)。オンデマンド `make prod/k3s/snapshot` は provisioner 別 proposal |
| `window` | **02:00-05:00 JST** (両 Plan) |
| concurrency | server: 1 / agent: 1 |
| cordon | `cordon: true` (両 Plan、k3s-upgrade image が drain 実施) |

## 通常手順 (パッチ更新)

### 1. Renovate PR のレビュー

Renovate が `k3s` グループで PR を起票する (server-plan + agent-plan の `version:` 行が同期して更新される)。レビュー時に必ず確認:

- [k3s release notes](https://github.com/k3s-io/k3s/releases) の breaking change セクション
- 関連 component (Cilium / Longhorn / Flux / cert-manager) との互換性表
- minor 跨ぎなら下記 [minor upgrade チェックリスト](#minor-upgrade-チェックリスト) を必ず通す

### 2. 事前 etcd snapshot の確認 (取得)

k3s は既定で **12h ごとに全 control-plane で自動 etcd snapshot** を取得している。
直近のスナップショットを `kubectl` で確認できる:

```sh
kubectl get etcdsnapshotfile \
  -o custom-columns='NODE:.spec.nodeName,TIME:.status.creationTime,NAME:.spec.snapshotName' \
  | sort
# 全 CP (br-node1/2/3) で当日の新しいスナップショットがあること
```

直近の自動スナップショットで足りない場合や、確実に upgrade 直前の点を取りたい場合は
**手動でオンデマンド取得**する (host CLI、`make prod/k3s/snapshot` は provisioner
別 proposal で実装予定):

```sh
# 全 control-plane (br-node1/2/3) で 1 ノードずつ実行
ssh br-node1 sudo k3s etcd-snapshot save --name pre-upgrade-$(date +%Y%m%d-%H%M)
ssh br-node2 sudo k3s etcd-snapshot save --name pre-upgrade-$(date +%Y%m%d-%H%M)
ssh br-node3 sudo k3s etcd-snapshot save --name pre-upgrade-$(date +%Y%m%d-%H%M)
```

確認: `ls /var/lib/rancher/k3s/server/db/snapshots/` に当日のスナップショットが 3 ノード分。

### 3. PR merge → Flux apply

PR を merge すると Flux が `Plan` を apply する。`Plan` の `version:` フィールドが更新されることで SUC が新規 Job を起こす。

```sh
# Flux reconcile を即時 trigger したい場合
flux reconcile kustomization system-upgrade-controller-app
kubectl get plans -n system-upgrade  # version が新しい値になっていること
```

### 4. window 内に SUC が実行

`window: 02:00-05:00 JST` 内に SUC が server → agent の順で Job を起動する。
window 外で `Plan` の `version` が更新されても Job は起こされず、次の window
開始時に実行される。window 内に作られた Job は window 終了後も走り続ける。

> **初回の本番 minor upgrade はライブ観察を推奨** (proposal リスク表)。
> 無人の深夜実行を避けたい場合は、観察できる時間帯に合わせて window を一時的に
> 調整するか、`window` 内に入ってから `kubectl get jobs -n system-upgrade -w` で
> CP→worker の進行・API 断・Pod 退避を実機確認する。

進行確認:

```sh
kubectl get jobs -n system-upgrade -w
# server-plan の Job が完了してから agent-plan の Job が起動することを確認
kubectl get nodes -w  # VERSION が CP→worker の順に v1.36.1+k3s1 へ
```

### 5. 翌朝の post-check

```sh
kubectl get nodes  # 全ノードの VERSION が新版で揃っていること
kubectl get pods -A | grep -vE 'Running|Completed'  # 異常 Pod が無いこと
kubectl get plans -n system-upgrade  # COMPLETE=True
```

## minor upgrade チェックリスト

minor 跨ぎ (`v1.X.Y` → `v1.(X+1).Y`) は patch より影響が大きいので、必ず通す。

- [ ] [k3s release notes](https://github.com/k3s-io/k3s/releases) の **Breaking Changes** セクション全項目を読んだ
- [ ] **CRD 変更** (k3s 同梱 traefik / coredns 等) を確認、apply 後に conflict が起きないか
- [ ] **kubelet ↔ apiserver の skew** が k3s 公式の許容範囲内か (公式: "ensure plan does not skip intermediate minor versions")
  - [ ] minor は **1 段ずつ** 上げる (例: 1.34 → 1.35 → 1.36、1.34 → 1.36 はしない)
- [ ] **Cilium 互換** ([Cilium ↔ k8s compatibility](https://docs.cilium.io/en/stable/network/kubernetes/compatibility/)) を確認
- [ ] **Flux 互換** ([Flux ↔ k8s compatibility](https://fluxcd.io/flux/installation/#prerequisites)) を確認
- [ ] **Longhorn 互換** ([Longhorn ↔ k8s compatibility](https://longhorn.io/docs/latest/best-practices/#kubernetes-distribution)) を確認
- [ ] **cert-manager 互換** を確認
- [ ] (任意) dev クラスタが存在する場合は dev で 1 周流してから prod
- [ ] `versions.yaml` の `versions.k3s` も同期更新 (Renovate の `groupName: k3s` で同 PR 内に来る想定、Phase 1b で結線)

## Rollback

### 前提となる挙動

- **k3s-upgrade image は downgrade を拒否する**。Plan の `version` を旧版に戻して merge しても SUC では戻らない (Job が "binary mismatch" 等で fail する)
- したがって rollback は **Ansible 経由で旧版を再 install** + 必要なら etcd snapshot から restore する 2 段構え

### 手順 (片付け順)

1. **Plan の `version` を旧版に戻す PR を出す** (新 Plan で更に進まないよう止める。実行は次ステップの Ansible で行う)
2. **Ansible で対象ノードに旧版バイナリを再 install**

   ```sh
   # 対象ノードの inventory / group を絞って実行
   uv run cluster-forge ansible prod -- \
     ansible-playbook playbooks/setup_node.yaml \
     --limit <node-name> \
     --extra-vars "k3s_version=v1.X.Y+k3s1"
   ```

   (現状の install playbook が `versions.yaml` を SoT とする前提。一時的に override で下げる)
3. **etcd データ不整合がある場合のみ snapshot restore** (control-plane で実行):

   ```sh
   # primary CP を停止して restore
   ssh br-node1 sudo systemctl stop k3s
   ssh br-node1 sudo k3s server \
     --cluster-reset \
     --cluster-reset-restore-path=/var/lib/rancher/k3s/server/db/snapshots/<snapshot-name>
   # secondary CP は通常起動で primary に follow
   ```

   公式手順: [k3s.io/datastore/backup-restore](https://docs.k3s.io/datastore/backup-restore)
4. ノードが Ready で戻ってきたら `kubectl get nodes` / `kubectl get pods -A` で確認

### 注意

- snapshot restore は **etcd データを巻き戻す** ので、restore 時刻以降に作られた Pod / Secret / ConfigMap は消える。restore は最終手段
- restore 元には k3s 自動スナップショット (12h ごと) か、upgrade 直前に手動取得したスナップショットを使う。snapshot 一覧は `kubectl get etcdsnapshotfile` で確認できる

## 個別ノード復旧

### 1 台だけ古いまま残った場合

SUC Job が失敗して特定ノードだけ古いまま残る場合:

```sh
# Job の状態を確認
kubectl get jobs -n system-upgrade -l upgrade.cattle.io/plan=agent-plan
kubectl describe job -n system-upgrade <failed-job>
kubectl logs -n system-upgrade <failed-job-pod> -c upgrade  # メインコンテナログ
```

復旧オプション:

1. **Job 再実行**: 失敗 Job を delete すると SUC が再生成する
   ```sh
   kubectl delete job -n system-upgrade <failed-job>
   ```
2. **Plan annotation 削除でリトライ強制**: ノード側の Plan 適用 annotation を消す
   ```sh
   kubectl annotate node <node-name> plan.upgrade.cattle.io/<plan-name>-
   ```
3. **手動 Ansible install**: SUC で詰まる場合は Ansible 経由で直接バイナリ更新

### cordon が外れない場合

SUC Job が drain 後に uncordon せず終わると、ノードに Pod が schedule されなくなる:

```sh
kubectl get nodes  # SchedulingDisabled が残っていないか
kubectl uncordon <node-name>
```

## API 不通時の障害対応フロー

upgrade 中に `kubectl` が応答しなくなったら、kube-vip / Cilium 起因の切り分けを優先:

```sh
# 1. VIP の到達性 (172.22.10.<vip>)
ping <api-vip>
# 2. control-plane 個別の :6443 直接到達
ssh br-node1 sudo curl -k https://localhost:6443/healthz
ssh br-node2 sudo curl -k https://localhost:6443/healthz
ssh br-node3 sudo curl -k https://localhost:6443/healthz
# 3. kube-vip Pod 状態
ssh br-node1 sudo crictl ps | grep kube-vip
# 4. Cilium agent
ssh br-node1 sudo crictl ps | grep cilium-agent
```

切り分け方針:

| 症状 | 切り分け |
|------|----------|
| VIP に ping 通らない | kube-vip リーダーが居ない / ARP 問題 → primary CP 上の kube-vip ログ確認 |
| 個別 `:6443` も応答無し | k3s server プロセスが落ちている → `systemctl status k3s` |
| `:6443` は応答するが Pod 通信不通 | Cilium agent 起因 → `cilium-cli status` (clutser-internal から) |
| 1 ノードだけ NotReady | upgrade Job がそのノードで詰まっている → 上記「個別ノード復旧」へ |

詳細は [`docs/network.md`](../network.md) と [`docs/platform/networking.md`](../platform/networking.md) を参照。

## SUC のセキュリティ前提

SUC が起こす Job は host namespace で動作するため、`system-upgrade` namespace 全体が他より高権限:

| 項目 | 内容 |
|------|------|
| host IPC / NET / PID | `true` (k3s プロセスを操作する必要があるため) |
| capabilities | `CAP_SYS_BOOT` (再起動 trigger) |
| volume mount | `/host` が rw mount (バイナリ書き換え) |
| ServiceAccount | `system-upgrade` (Plan の `serviceAccountName` で参照) |

実装上の含意:

- `system-upgrade` namespace に他のワークロードを混ぜない
- 必要に応じて Conftest / OPA で **`system-upgrade` namespace を policy 例外として明示**する (Phase 2 で必要なら追加、現状 `policies/exceptions.rego` は空)
- SUC Job が走っているタイミングは host への影響が大きいので、他の高負荷 batch を被せない

## 関連 doc

- [`docs/proposals/k3s-upgrade.md`](../proposals/k3s-upgrade.md) — 採用 / 不採用の理由、Phase 設計
- [`docs/operations.md`](../operations.md) — クラスタ全体の運用手順インデックス
- [`docs/platform/storage.md`](../platform/storage.md) — Longhorn `nodeDownPodDeletionPolicy: do-nothing` (drain 時の前提)
- [k3s upgrades (Automated)](https://docs.k3s.io/upgrades/automated) — k3s 公式の SUC 手順
- [k3s upgrades (Backup/Restore)](https://docs.k3s.io/datastore/backup-restore) — etcd snapshot 公式手順
