# Runbook: k3s クラスタアップグレード

system-upgrade-controller (SUC) 経由で k3s をアップグレードする手順。

> **Phase 1a 段階の注意 (2026-09 時点)**
>
> シングル control-plane 化 (`br-cluster1` 1 台、SQLite datastore) に伴い、Phase 1a の
> テスト運用対象を `br-node5` から **`agent-plan` (`br-cluster2` のみ)** に変更した。
> `server-plan` は `br-cluster1` を対象に定義済みだが、**実行するとクラスタ全停止を
> 伴う** ため、計画的な実行のみとし日常のテスト運用には含めない。
> `window` (02:00-05:00 JST) の有効化は **Phase 1b で対応** (provisioner 別 proposal の決着後)。
> SQLite datastore には etcd snapshot に相当する機構が無いため、**snapshot 取得 /
> snapshot からの rollback は行わない** (下記 [Rollback](#rollback) 参照)。
>
> Phase の定義は [`docs/proposals/k3s-upgrade.md`](../proposals/k3s-upgrade.md) 参照。

## 前提

| 項目 | 値 |
|------|-----|
| アップグレード方式 | SUC + `Plan` CRD (`upgrade.cattle.io/v1`) |
| Plan SoT | [`manifests/platform/system-upgrade-controller/app/base/{server,agent}-plan.yaml`](../../manifests/platform/system-upgrade-controller/app/base/) |
| バージョン追跡 | Renovate customManager (`renovate.json` 内 `manifests/platform/system-upgrade-controller/.+\.ya?ml$` 対象) |
| Renovate group | `k3s` (server-plan + agent-plan を 1 PR にまとめる) / `system-upgrade-controller` (controller + CRD URL を 1 PR にまとめる) |
| datastore バックアップ | **無し**。SQLite には etcd snapshot 機構が無く、クラスタ状態のバックアップは取らない方針 ([`docs/kubernetes.md#トポロジ`](../kubernetes.md#トポロジ)) |
| `window` | **未指定** (Phase 1b で 02:00-05:00 JST に絞る) |
| concurrency | server: 1 / agent: 1 |
| cordon | `cordon: true` (両 Plan、k3s-upgrade image が drain 実施) |

## 通常手順 (パッチ更新)

### 1. Renovate PR のレビュー

Renovate が `k3s` グループで PR を起票する (server-plan + agent-plan の `version:` 行が同期して更新される)。レビュー時に必ず確認:

- [k3s release notes](https://github.com/k3s-io/k3s/releases) の breaking change セクション
- 関連 component (Cilium / Flux / cert-manager) との互換性表
- minor 跨ぎなら下記 [minor upgrade チェックリスト](#minor-upgrade-チェックリスト) を必ず通す

### 2. 事前バックアップは無し

SQLite datastore には etcd snapshot に相当する機構が無いため、事前スナップショット取得は行わない。`br-cluster1` の control-plane を upgrade する場合、その間クラスタが全停止することを前提に計画する。**agent-plan (worker) のアップグレードは control-plane に影響しないため、この制約を受けない。**

### 3. PR merge → Flux apply

PR を merge すると Flux が `Plan` を apply する。`Plan` の `version:` フィールドが更新されることで SUC が新規 Job を起こす。

```sh
# Flux reconcile を即時 trigger したい場合
flux reconcile kustomization system-upgrade-controller-app
kubectl get plans -n system-upgrade  # version が新しい値になっていること
```

### 4. (Phase 1b で実体化) window 内に SUC が実行

Phase 1b 以降は `window: 02:00-05:00 JST` 内に SUC が server → agent の順で Job を起動する。Phase 1a の現状は **window 未指定** なので apply 直後から実行される。

進行確認:

```sh
kubectl get jobs -n system-upgrade -w
# server-plan の Job が完了してから agent-plan の Job が起動することを確認
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
- [ ] **cert-manager 互換** を確認
- [ ] (任意) dev クラスタが存在する場合は dev で 1 周流してから prod
- [ ] `versions.yaml` の `versions.k3s` も同期更新 (Renovate の `groupName: k3s` で同 PR 内に来る想定、Phase 1b で結線)

## Rollback

### 前提となる挙動

- **k3s-upgrade image は downgrade を拒否する**。Plan の `version` を旧版に戻して merge しても SUC では戻らない (Job が "binary mismatch" 等で fail する)
- SQLite datastore には snapshot restore の逃げ道が無い。rollback は **Ansible 経由で旧版バイナリを再 install** するのみ
- `br-cluster1` (control-plane) の rollback はクラスタ全停止を伴う。`br-cluster2` / `br-cluster3` (worker) の rollback はワークロードへの影響のみ

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
3. ノードが Ready で戻ってきたら `kubectl get nodes` / `kubectl get pods -A` で確認

### 注意

- Flux による GitOps と「PVC は ephemeral」という前提のもと、`br-cluster1` に致命的な問題が起きた場合は snapshot restore ではなく **再フラッシュして Flux に再構築させる**のが復旧手順になる ([`docs/kubernetes.md#トポロジ`](../kubernetes.md#トポロジ))

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

control-plane が `br-cluster1` の 1 台のみなので、`br-cluster1` の upgrade 中は API が確実に一時停止する (想定内)。upgrade 対象が worker (`br-cluster2` / `br-cluster3`) のはずなのに `kubectl` が応答しなくなった場合は、Cilium 起因の切り分けを優先:

```sh
# 1. control-plane の :6443 直接到達
ssh br-cluster1 sudo curl -k https://localhost:6443/healthz
# 2. Cilium agent
ssh br-cluster1 sudo crictl ps | grep cilium-agent
```

切り分け方針:

| 症状 | 切り分け |
|------|----------|
| `:6443` も応答無し | k3s server プロセスが落ちている → `systemctl status k3s` (`br-cluster1` の upgrade 中なら想定内) |
| `:6443` は応答するが Pod 通信不通 | Cilium agent 起因 → `cilium-cli status` |
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
- [k3s upgrades (Automated)](https://docs.k3s.io/upgrades/automated) — k3s 公式の SUC 手順
