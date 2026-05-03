# Renovate 運用ガイド

依存更新は [Renovate](https://docs.renovatebot.com/) (Mend) が GitHub App として
週次で PR を起票する。本 doc は **「何が追跡されているか」「Dashboard の見方」
「新しい依存を追跡したい時の追加方法」** をまとめる。

設定の SoT は [`renovate.json`](../../renovate.json)。設計判断の背景は
[`docs/proposals-done/renovate-coverage.md`](../proposals-done/renovate-coverage.md)
(着地後に移管予定) を参照。

## 実行スケジュール

| 項目 | 設定 |
|------|------|
| 通常 run | 毎週土曜 09:00 JST 前 (`schedule: ["before 9am on saturday"]`) |
| `lockFileMaintenance` | 同上 (週次) |
| `minimumReleaseAge` (minor/patch) | 7 日 |
| `minimumReleaseAge` (major) | 14 日 |
| `automerge` | **無効** (全カテゴリ) |
| `prConcurrentLimit` / `prHourlyLimit` | 0 (無制限) |

手動 trigger は Dependency Dashboard (Issue #65) の末尾チェックボックス
"Check this box to trigger a request for Renovate to run again" で起こす。

## 追跡対象

| マネージャ | 対象ファイル | 備考 |
|------------|--------------|------|
| **flux** | `manifests/**/*.ya?ml` の `HelmRelease` / `HelmRepository` / `OCIRepository` | digest pin は `pinDigests: false` (chart の version pin で十分) |
| **kustomize** | `manifests/**/kustomization.ya?ml` | Git ref 形式 (`?ref=<tag>`) のみ自動検知 |
| **github-actions** | `.github/workflows/*.ya?ml` | digest pin (`helpers:pinGitHubActionDigests`) |
| **dockerfile** | `docker/Dockerfile` | digest pin (`docker:pinDigests`) |
| **docker-compose** | `compose.yaml` | digest pin |
| **mise** | `mise.toml` の `[tools]` | `pipx:ansible-lint` も追跡対象。registry 経由 |
| **pep621** | `pyproject.toml` | `uv.lock` は `lockFileMaintenance` 経由 |
| **ansible-galaxy** | `provisioner/requirements.yaml` | collections と roles の両方 |
| **customManager (regex)** ×3 | `imager/variables.pkr.hcl` / `manifests/platform/system-upgrade-controller/**` / `provisioner/inventories/base/group_vars/all/versions.yaml` | `# renovate:` コメント注釈で datasource を指示 |

### グルーピング

| `groupName` | 内容 |
|-------------|------|
| `github-actions` | GitHub Actions 全体 |
| `mise tools` | mise.toml の全エントリ |
| `k3s` | `k3s-io/k3s` (server-plan + agent-plan を 1 PR に集約) |
| `system-upgrade-controller` | SUC の crd.yaml + system-upgrade-controller.yaml |

## Dependency Dashboard の見方

[Issue #65](https://github.com/bright-room/br-cluster/issues/65) が Dashboard。
週次で確認するセクションは:

| セクション | 何を見るか |
|------------|-----------|
| **Repository Problems** | Renovate 自体のエラー / 警告。出ていたら原因を特定 |
| **Pending Status Checks** | `minimumReleaseAge` 待ちの PR 候補。チェックを入れると即起票 |
| **Open** | 起票済 PR。レビュー待ち |
| **Detected Dependencies** | 追跡できている依存の全リスト。**新依存追加時はここに出るか確認** |

## 新しい依存を追跡したい時

### ケース 1: 既存マネージャが拾える形で書く

mise.toml / pyproject.toml / requirements.yaml / HelmRelease / Dockerfile /
GitHub Actions に書く分には自動追跡される。**まずこれを優先**。

### ケース 2: customManager (regex) を追加

GitHub release URL 直参照、HCL 変数、shell スクリプト内の version 文字列など、
標準マネージャが拾えない形式の場合。

1. 対象ファイル内に `# renovate:` 注釈を追加

   ```yaml
   # renovate: datasource=github-releases depName=foo/bar versioning=semver
   foo: "v1.2.3"
   ```

2. [`renovate.json`](../../renovate.json) の `customManagers` に regex を追加
   (既存の versions.yaml 用 / SUC 用 / Packer 用パターンを参考に)
3. PR を作って Renovate に再 run させる
4. Dependency Dashboard の **Detected Dependencies** に `regex` セクションが出る
   ことを確認

### datasource の主な選択肢

| datasource | 例 | 備考 |
|-----------|----|------|
| `github-releases` | `k3s-io/k3s` / `helm/helm` | 最も汎用 |
| `docker` | `coredns/coredns` | OCI registry 上の tag |
| `pypi` | `certbot` | Python パッケージ |
| `npm` | — | Node パッケージ (未使用) |

`versioning` を省略すると `semver`。**k3s だけは `loose`** (`+k3sN` suffix のため)。

### `extractVersion` で `v` prefix を剥がす

`coredns/coredns` のように tag が `v1.14.3` だが値は `1.14.3` で書きたいとき、
`packageRules` で `extractVersion: "^v(?<version>.+)$"` を指定する
(既存の `coredns/coredns` / `prometheus/node_exporter` / `grafana/alloy` を参照)。

## トラブルシュート

### PR が立たない

1. Dependency Dashboard の **Detected Dependencies** に出ているか確認
   - 出ていない → manager の matchPattern や customManager の regex を疑う
   - 出ている → `minimumReleaseAge` 待ちの可能性 (Pending Status Checks 参照)
2. `renovate.json` の構文エラーは Renovate 側の log を見る
   ([Mend.io Web Portal](https://developer.mend.io/github/bright-room/br-cluster))
3. 新版が `currentValue` の versioning 範囲外 (例: `loose` でないと拾えない suffix)

### 想定と違う datasource で取りに行く

`# renovate:` 注釈の `datasource=` を見直す。install 経路 (snap / apt / pip / GitHub release / docker) と
合っているか確認。

### `groupName` を変えたい

`renovate.json` の `packageRules` に `matchPackageNames` + `groupName` を追加。
既存の `k3s-io/k3s` / `rancher/system-upgrade-controller` のパターンを参考に。

## 関連

- [`renovate.json`](../../renovate.json) — 設定の SoT
- [Dependency Dashboard (Issue #65)](https://github.com/bright-room/br-cluster/issues/65)
- [`docs/runbooks/k3s-upgrade.md`](k3s-upgrade.md) — k3s upgrade は Renovate PR のレビューが起点
- [`docs/proposals/renovate-coverage.md`](../proposals/renovate-coverage.md) — Phase 1 設計判断 (着地後に proposals-done へ移管予定)
