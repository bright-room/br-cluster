# CF Access path 分割 実装プラン（プラン 1/3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `auth.b8m.app` の CF Access を path 分割し、login UI / OIDC endpoint を公開到達可（bypass）にしつつ、admin console (`/ui/console`) だけ GitHub org + WARP 保護を維持する。

**Architecture:** `br-cloudflare-terraform` の `zero_trust` モジュールで、`auth` をジェネリックな `protected` for_each アプリから外し、専用の 2 アプリ（`auth_public` = host root + bypass、`auth_console` = `/ui/console` path + GitHub org/WARP）に分割する。CF Access はより具体的な path のアプリを優先するため、console は守られたまま root が公開になる。

**Tech Stack:** Terraform 1.14.7、cloudflare provider `~> 5.0`、Makefile（`make fmt` / `make validate-zero_trust` / `make plan-zero-trust` / `make apply-zero-trust`）。merge=CI auto-apply。

> **このプランは [`zitadel-multi-org-rbac.md`](./zitadel-multi-org-rbac.md) の 3 プラン中 1/3。** 独立・低リスクで、後続（Zitadel IaC / br-cluster 配線）の前提となるログイン到達性を先に確保する。**対象リポジトリは `br-cluster-zitadel-terraform` ではなく `br-cloudflare-terraform`。**

---

## 前提知識（context ゼロの実装者向け）

- **CF Access の path 優先**: 同一ホストに複数の Access application がある場合、より具体的な path（`auth.b8m.app/ui/console`）が host root（`auth.b8m.app`）より優先される。よって root を bypass（全員許可）にしても、`/ui/console` の保護アプリが先に効く。
- **bypass policy**: `decision = "bypass"` ＋ `include = [{ everyone = {} }]` で「CF Access 認証なし＝公開」になる。Zitadel 自身がそのホストの認証を担うため、login/OIDC には CF Access を被せない。
- **provider v5 スキーマ（確認済み）**:
  - `cloudflare_zero_trust_access_application.domain` は **hostname + path** を受け付ける（例 `"auth.b8m.app/ui/console"`）。
  - `policies` は `[{ id = <policy uuid>, precedence = <number> }]`。
  - `cloudflare_zero_trust_access_policy` の everyone include は `include = [{ everyone = {} }]`。
- **既存コードの場所**: [`terraform/zero_trust/access_applications.tf`](https://github.com/bright-room/br-cloudflare-terraform/blob/main/terraform/zero_trust/access_applications.tf)。`local.access_applications` map と `protected` for_each、`allow_github` / `allow_github_warp` policy がある。
- **作業ディレクトリ**: 全コマンドは `br-cloudflare-terraform` リポジトリ root から実行する。

---

## ファイル構成

- Modify: `terraform/zero_trust/access_applications.tf`
  - `local.access_applications` から `auth` エントリを削除
  - `cloudflare_zero_trust_access_policy.bypass_public` を追加
  - `cloudflare_zero_trust_access_application.auth_console` を追加
  - `cloudflare_zero_trust_access_application.auth_public` を追加

変更は 1 ファイルに収まる。新規ファイルは作らない。

---

## Task 1: `auth` をジェネリック保護アプリから外す

**Files:**
- Modify: `terraform/zero_trust/access_applications.tf`

- [ ] **Step 1: `local.access_applications` map から `auth` を削除**

`access_applications.tf` の `locals` ブロックを次のように変更する（`auth` 行とそのコメントを削除）。変更前:

```hcl
  access_applications = {
    grafana        = "grafana.b8m.app"
    prometheus     = "prometheus.b8m.app"
    alertmanager   = "alertmanager.b8m.app"
    hubble         = "hubble.b8m.app"
    longhorn       = "longhorn.b8m.app"
    argo_workflows = "argo-workflows.b8m.app"
    flux           = "flux.b8m.app"
    # Zitadel console. A follow-up PR will layer a WARP device-posture
    # requirement on top; for now the same github_organization gate protects
    # the hostname. /oauth/v2/* and /.well-known/* will need path-level
    # bypasses once apps start OIDC-ing through it.
    auth = "auth.b8m.app"
  }
```

変更後:

```hcl
  access_applications = {
    grafana        = "grafana.b8m.app"
    prometheus     = "prometheus.b8m.app"
    alertmanager   = "alertmanager.b8m.app"
    hubble         = "hubble.b8m.app"
    longhorn       = "longhorn.b8m.app"
    argo_workflows = "argo-workflows.b8m.app"
    flux           = "flux.b8m.app"
    # auth.b8m.app は path 分割が必要なため、この map ではなく専用の
    # auth_public / auth_console リソースで個別管理する（本ファイル下部）。
  }
```

- [ ] **Step 2: `terraform fmt` で整形**

Run: `make fmt`
Expected: 終了コード 0。差分が出れば自動整形される。

- [ ] **Step 3: validate**

Run: `make validate-zero_trust`
Expected: `Success! The configuration is valid.`

- [ ] **Step 4: コミット**

```bash
git add terraform/zero_trust/access_applications.tf
git commit -m "refactor(zero_trust): pull auth.b8m.app out of generic protected apps map"
```

---

## Task 2: bypass policy ＋ auth_console / auth_public アプリを追加

**Files:**
- Modify: `terraform/zero_trust/access_applications.tf`

- [ ] **Step 1: bypass policy リソースを追加**

`access_applications.tf` の末尾（`protected` リソースの下）に追加する:

```hcl
# auth.b8m.app の login UI / OIDC endpoint を公開到達可にする bypass policy。
# Zitadel 自身がそのホストの認証を担うため、login 画面と OIDC endpoint には
# CF Access をかけない。admin console (/ui/console) だけは auth_console アプリで
# GitHub org + WARP 保護を維持する。
# project: br-cluster
resource "cloudflare_zero_trust_access_policy" "bypass_public" {
  account_id = local.account_id
  name       = "Bypass public (auth login + OIDC)"
  decision   = "bypass"
  include    = [{ everyone = {} }]
}
```

- [ ] **Step 2: auth_console アプリ（path-scoped・保護）を追加**

bypass policy の下に追加する:

```hcl
# auth.b8m.app/ui/console — Zitadel admin console。より具体的な path なので
# host root の auth_public より CF Access 上で優先評価され、GitHub org + WARP
# 保護が効き続ける。
# project: br-cluster
resource "cloudflare_zero_trust_access_application" "auth_console" {
  account_id                = local.account_id
  name                      = "Auth Console"
  domain                    = "auth.b8m.app/ui/console"
  type                      = "self_hosted"
  session_duration          = "24h"
  allowed_idps              = [cloudflare_zero_trust_access_identity_provider.github.id]
  auto_redirect_to_identity = true
  policies = [{
    id         = cloudflare_zero_trust_access_policy.allow_github_warp.id
    precedence = 1
  }]
}
```

- [ ] **Step 3: auth_public アプリ（host root・bypass）を追加**

auth_console の下に追加する。bypass なので `allowed_idps` / `auto_redirect_to_identity` は付けない（公開ユーザーを GitHub にリダイレクトしないため）:

```hcl
# auth.b8m.app (host root) — login UI (/ui/v2/login) / OIDC endpoint
# (/oauth/v2/*, /oidc/v1/*, /.well-known/*) / assets。bypass で公開到達可にする。
# これにより CF Access 非対象の公開アプリ利用者が Zitadel ログイン画面に届く。
# project: br-cluster
resource "cloudflare_zero_trust_access_application" "auth_public" {
  account_id       = local.account_id
  name             = "Auth Public"
  domain           = "auth.b8m.app"
  type             = "self_hosted"
  session_duration = "24h"
  policies = [{
    id         = cloudflare_zero_trust_access_policy.bypass_public.id
    precedence = 1
  }]
}
```

- [ ] **Step 4: fmt**

Run: `make fmt`
Expected: 終了コード 0。

- [ ] **Step 5: validate**

Run: `make validate-zero_trust`
Expected: `Success! The configuration is valid.`

- [ ] **Step 6: plan を取り、差分を目視確認**

Run: `make plan-zero-trust`
Expected（順不同・件数が一致すること）:
- `cloudflare_zero_trust_access_application.protected["auth"]` が **destroy**（1 件）
- `cloudflare_zero_trust_access_policy.bypass_public` が **create**（1 件）
- `cloudflare_zero_trust_access_application.auth_console` が **create**（1 件）
- `cloudflare_zero_trust_access_application.auth_public` が **create**（1 件）
- 他の `protected[*]` アプリや `allow_github` / `allow_github_warp` policy に変更が無いこと（`grafana` 等が destroy/replace されていないことを必ず確認）

> 想定外に他アプリが replace される差分が出たら **apply せず中断**し、原因（map 変更の副作用等）を調査する。

- [ ] **Step 7: コミット**

```bash
git add terraform/zero_trust/access_applications.tf
git commit -m "feat(zero_trust): split auth.b8m.app into public-bypass + protected console apps"
```

---

## Task 3: apply と実機検証

**Files:** なし（インフラ反映と検証のみ）

> このリポは merge=CI auto-apply（`.github/workflows/on-merge.yaml`）。PR を main に merge すると CI が `terraform apply` する。手動で先に当てる場合は下記。

- [ ] **Step 1: apply（PR merge、または手動）**

PR merge による CI apply を待つ。手動で当てる場合:

Run: `make apply-zero-trust`
Expected: Task 2 Step 6 の plan と同一の差分が apply され、`Apply complete! Resources: 3 added, 1 destroyed.`

- [ ] **Step 2: login UI が公開到達可になったことを検証**

GitHub org メンバーでない / WARP 未接続の状態（例: WARP を切ったブラウザ、別端末、シークレットウィンドウ）で:

```
https://auth.b8m.app/ui/v2/login/
```

Expected: **Cloudflare Access のログイン画面（cloudflareaccess.com へのリダイレクト）を経由せず、Zitadel のログイン画面が直接表示される**。

補助確認（CLI）:

Run: `curl -sSI https://auth.b8m.app/.well-known/openid-configuration | head -20`
Expected: `HTTP/2 200`（または 301/302 で Zitadel 内）。`location:` に `*.cloudflareaccess.com` が含まれ**ない**こと。

- [ ] **Step 3: admin console が保護されたままであることを検証**

GitHub org 認証 / WARP の無いコンテキストで:

```
https://auth.b8m.app/ui/console/
```

Expected: **Cloudflare Access の challenge（GitHub ログイン要求 / WARP posture 要求）が表示される**。Zitadel console に直接は入れない。

正常系（GitHub org メンバー＋WARP 接続）では従来通り console に入れることも確認する。

- [ ] **Step 4: 既存保護アプリの回帰確認**

`https://grafana.b8m.app` 等、既存の `protected[*]` アプリが従来通り GitHub org + WARP で保護され、正常にログインできることを確認する（map 変更の巻き込み事故が無いことの最終確認）。

---

## Self-Review（このプランと spec の突き合わせ）

- **spec「③ CF Access path 分割」**: Task 1–2 でカバー（auth_console 保護維持 / auth_public bypass）。✅
- **spec「個人アプリは map に載せないだけで公開」**: 本プランで `access_applications` map を変更するが個人アプリは追加しない（後続アプリ追加時に map に**載せない**運用を維持するだけ）。本プラン範囲外＝意図通り。✅
- **placeholder スキャン**: TBD/TODO なし。全 HCL とコマンドは実値。✅
- **型・参照整合**: `cloudflare_zero_trust_access_policy.bypass_public` / `.allow_github_warp` / `cloudflare_zero_trust_access_identity_provider.github` は実在リソースを参照。`local.account_id` は `_locals.tf` 定義済み。✅
