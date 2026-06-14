# Zitadel IaC 再設計 実装プラン（プラン 2/3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zitadel を `br-dev`（k3s 管理）/ `br-apps`（公開アプリ）の 2 org に分割し、`platform` project を `br-dev` に再作成して 4 ロール（admin/maintainer/developer/viewer）＋ role/project check を有効化、全トークンにフラット `roles` claim を注入する Action を追加し、セルフ登録を無効化する。

**Architecture:** `br-cluster-zitadel-terraform` の `terraform/` で、新規 org を TF 作成し、既存 project/apps/users の `org_id` を新 org へ向け替える（`org_id` 変更は ForceNew = destroy+recreate）。OIDC アプリの output キー名は据え置くため、br-cluster 側 ExternalSecret は値の自動再 sync で済む。これは **一度きりのカットオーバー**（全 client secret 再発行・全アプリ再ログイン）。

**Tech Stack:** OpenTofu/Terraform（`>= 1.6`）、provider `zitadel/zitadel ~> 2.12`、tofu-controller（`approvePlan: auto`、merge=即 apply）。provider 認証は iam-admin JWT profile（ZITADEL システム org 在籍、本プランで不変）。

> **このプランは [`zitadel-multi-org-rbac.md`](./zitadel-multi-org-rbac.md) の 3 プラン中 2/3。** 前提: プラン 1（CF Access path 分割）が apply 済みで `auth.b8m.app` のログイン画面が公開到達可。後続: プラン 3（br-cluster 側 Envoy authorization ＋ アプリ role マッピング）。**対象リポジトリは `br-cluster-zitadel-terraform`。**

---

## 前提知識（context ゼロの実装者向け）

- **provider 認証は不変**: provider は `jwt_profile_file = /secrets/zitadel-admin/iam-admin.json`（instance IAM_OWNER、ZITADEL システム org 在籍）で認証する。本プランは ZITADEL システム org を**一切触らない**ので、移行中も provider は動き続ける。
- **`org_id` 変更 = ForceNew**: `zitadel_project` / `zitadel_application_oidc` / `zitadel_human_user` の `org_id` を変えると、provider は destroy+recreate する。これが「ZITADEL org から br-dev org への引っ越し」の実体。client_id/secret は再発行される。
- **output キー名据え置き**: [`outputs.tf`](https://github.com/bright-room/br-cluster-zitadel-terraform/blob/main/terraform/outputs.tf) の `<app>_client_id` / `<app>_client_secret` のキー名は変えない。これにより `tf-zitadel-output` Secret のキーが不変→ br-cluster の ExternalSecret は値の更新だけ自動 sync する。
- **検証済みスキーマ**（provider `~> 2.12`）:
  - `zitadel_org`: required `name`。exports `id`。
  - `zitadel_project_role`: required `org_id` / `project_id` / `role_key` / `display_name`。optional `group`。
  - `zitadel_user_grant`: required `user_id`。optional `org_id` / `project_id` / `role_keys`（Set of String）。
  - `zitadel_action`: required `name` / `script` / `timeout` / `allowed_to_fail`。optional `org_id`。
  - `zitadel_trigger_actions`: required `flow_type` / `trigger_type` / `action_ids`。optional `org_id`。token 用は `flow_type="FLOW_TYPE_CUSTOMISE_TOKEN"`、`trigger_type` は `TRIGGER_TYPE_PRE_ACCESS_TOKEN_CREATION` と `TRIGGER_TYPE_PRE_USERINFO_CREATION`。
  - `zitadel_default_login_policy`: instance シングルトン。13+ の必須引数を全て指定する必要がある（`allow_register` 含む）。
- **`var.default_org_id` は CR から渡されていない**（[`terraform.yaml`](https://github.com/bright-room/br-cluster/blob/main/manifests/platform/zitadel/terraform/base/terraform.yaml) は `zitadel_domain` のみ）。よって削除して `zitadel_org.br_dev.id` 参照に置換しても CR は壊れない。
- **作業ディレクトリ**: 全コマンドは `br-cluster-zitadel-terraform` リポジトリ root。ローカルで provider に到達できない場合、`terraform validate`（ネットワーク不要）までをローカルで実施し、`plan`/`apply` は tofu-controller（PR merge）に委ねる。

---

## ファイル構成

| 操作 | パス | 責務 |
|---|---|---|
| Create | `terraform/orgs.tf` | `zitadel_org.br_dev` / `zitadel_org.br_apps` |
| Create | `terraform/roles_platform.tf` | platform project の 4 ロール定義 |
| Create | `terraform/grants.tf` | kukv/bradmin への admin ロール `zitadel_user_grant` |
| Create | `terraform/action_roles_claim.tf` | フラット `roles` claim 注入 Action ＋ token triggers |
| Create | `terraform/login_policy.tf` | instance default login policy（`allow_register = false`） |
| Modify | `terraform/project_platform.tf` | `org_id` を br_dev へ、role/project check を ON |
| Modify | `terraform/modules/oidc_application/variables.tf` | `access_token_type` 変数を追加（Envoy authz 用 JWT 切替） |
| Modify | `terraform/modules/oidc_application/main.tf` | `access_token_type` を変数化 |
| Modify | `terraform/apps_platform.tf` | `org_id` を br_dev へ、Envoy 系 4 アプリを JWT access token に |
| Modify | `terraform/app_grafana.tf` | `org_id` を br_dev へ |
| Modify | `terraform/app_flux_web.tf` | `org_id` を br_dev へ |
| Modify | `terraform/app_argo_workflows.tf` | `org_id` を br_dev へ |
| Modify | `terraform/user_kukv.tf` | `org_id` を br_dev へ、org_member を br_dev へ |
| Modify | `terraform/user_bradmin.tf` | `org_id` を br_dev へ |
| Modify | `terraform/_variables.tf` | `default_org_id` 変数を削除 |

---

## Task 1: br-dev / br-apps org を作成

**Files:**
- Create: `terraform/orgs.tf`

- [ ] **Step 1: orgs.tf を作成**

```hcl
# 自分のリソースを集約する 2 つの org。ZITADEL システム org（Management/Admin
# API・Console を抱える自動生成 org）からは完全に分離する。ZITADEL システム
# org 自体は本リポでは管理しない（触らない）。
#
# br-dev: k3s 管理（platform project・管理者ユーザー）。
# br-apps: 公開アプリ利用者（個人アプリの OIDC client と利用者）。
resource "zitadel_org" "br_dev" {
  name = "br-dev"
}

resource "zitadel_org" "br_apps" {
  name = "br-apps"
}
```

- [ ] **Step 2: validate**

Run: `terraform -chdir=terraform validate`
Expected: `Success! The configuration is valid.`（provider init 済みでない場合は先に `terraform -chdir=terraform init`）

- [ ] **Step 3: コミット**

```bash
git add terraform/orgs.tf
git commit -m "feat: add br-dev and br-apps organizations"
```

---

## Task 2: platform project を br-dev へ移し、role/project check を有効化

**Files:**
- Modify: `terraform/project_platform.tf`
- Modify: `terraform/_variables.tf`

- [ ] **Step 1: project_platform.tf を書き換え**

変更前:

```hcl
resource "zitadel_project" "platform" {
  org_id                   = var.default_org_id
  name                     = "platform"
  project_role_check       = false
  project_role_assertion   = false
  has_project_check        = false
  private_labeling_setting = "PRIVATE_LABELING_SETTING_UNSPECIFIED"
}
```

変更後（コメントも認可前提に合わせて更新）:

```hcl
# Zitadel Project that owns every OIDC Application for a br-cluster platform
# component. Authorization is now enforced by Zitadel: project_role_check +
# has_project_check deny token issuance to users without a grant, and
# project_role_assertion makes role membership available to the roles-claim
# Action (see action_roles_claim.tf).
resource "zitadel_project" "platform" {
  org_id                   = zitadel_org.br_dev.id
  name                     = "platform"
  project_role_check       = true
  project_role_assertion   = true
  has_project_check        = true
  private_labeling_setting = "PRIVATE_LABELING_SETTING_UNSPECIFIED"
}
```

- [ ] **Step 2: _variables.tf から `default_org_id` 変数を削除**

`_variables.tf` の以下のブロック（先頭のコメント `# Default Organization ID...` から `variable "default_org_id" { ... }` の閉じ括弧まで）を**丸ごと削除**する:

```hcl
# Default Organization ID created by Helm's FirstInstance setup. Not managed
# ...（中略。kubectl probe の手順コメント含む）...
variable "default_org_id" {
  description = "ID of the Zitadel default Organization (owner of all platform projects)."
  type        = string
  default     = "369442873638192055"
}
```

> 他の変数（`zitadel_domain`、SMTP 系）は残す。

- [ ] **Step 3: validate**

Run: `terraform -chdir=terraform validate`
Expected: `Success!`。`var.default_org_id` への未解決参照エラーが出た場合、Task 4/5 で全参照を置換するまで一時的に出る可能性がある。**Task 5 完了時点で validate が green になることをゴールとする**（このプランは Task 2〜5 を一括 PR にまとめる前提）。

- [ ] **Step 4: コミット**

```bash
git add terraform/project_platform.tf terraform/_variables.tf
git commit -m "feat: move platform project to br-dev and enable role/project checks"
```

---

## Task 3: platform project に 4 ロールを定義

**Files:**
- Create: `terraform/roles_platform.tf`

- [ ] **Step 1: roles_platform.tf を作成**

```hcl
# platform project の 4 ロール。role_key がトークンの roles claim に載る値
# （action_roles_claim.tf がフラット配列化する）。意味づけ:
#   admin      … クラスタ全権
#   maintainer … 運用全般（storage / alert silence 含む）、破壊的操作は限定
#   developer  … アプリ中心（dashboard 編集 / deploy / sync）、storage/alert silence 不可
#   viewer     … 読み取りのみ（dashboard 閲覧）
locals {
  platform_roles = {
    admin      = "Admin"
    maintainer = "Maintainer"
    developer  = "Developer"
    viewer     = "Viewer"
  }
}

resource "zitadel_project_role" "platform" {
  for_each = local.platform_roles

  org_id       = zitadel_org.br_dev.id
  project_id   = zitadel_project.platform.id
  role_key     = each.key
  display_name = each.value
}
```

- [ ] **Step 2: validate**

Run: `terraform -chdir=terraform validate`
Expected: `Success!`（Task 2 の未解決参照が解消していない段階ではエラーが残りうる。Task 5 完了時に green を確認）。

- [ ] **Step 3: コミット**

```bash
git add terraform/roles_platform.tf
git commit -m "feat: define admin/maintainer/developer/viewer roles on platform project"
```

---

## Task 4: 全 OIDC アプリを br-dev へ向け替え（＋ Envoy 系を JWT access token に）

**Files:**
- Modify: `terraform/modules/oidc_application/variables.tf`
- Modify: `terraform/modules/oidc_application/main.tf`
- Modify: `terraform/apps_platform.tf`
- Modify: `terraform/app_grafana.tf`
- Modify: `terraform/app_flux_web.tf`
- Modify: `terraform/app_argo_workflows.tf`

> **プラン 3 との依存**: Envoy 系アプリ（alertmanager/hubble/longhorn/prometheus）は、プラン 3 の Envoy `jwt`+`authorization` がトークンを検証するため、access token を **JWT 型**にする必要がある（Zitadel デフォルトは opaque bearer で JWT 検証できない）。Grafana/Argo/Flux は ID token + userinfo で claim を読むので bearer のままでよい。

- [ ] **Step 1: モジュールに `access_token_type` 変数を追加**

`terraform/modules/oidc_application/variables.tf` の末尾に追加:

```hcl
variable "access_token_type" {
  description = "Access token format. Envoy SecurityPolicy jwt+authorization needs a JWT (OIDC_TOKEN_TYPE_JWT); apps that read claims from the ID token/userinfo keep the opaque bearer default."
  type        = string
  default     = "OIDC_TOKEN_TYPE_BEARER"
}
```

- [ ] **Step 2: モジュールで `access_token_type` を変数化**

`terraform/modules/oidc_application/main.tf` の該当行を置換:

変更前: `  access_token_type           = "OIDC_TOKEN_TYPE_BEARER"`
変更後: `  access_token_type           = var.access_token_type`

- [ ] **Step 3: apps_platform.tf の `org_id` 置換 ＋ Envoy 系を JWT に**

`module "platform_apps"` ブロックの `org_id` を置換し、Envoy 系 4 アプリ（この module が束ねる alertmanager/hubble/longhorn/prometheus）を JWT access token にする。

`org_id` 変更前: `org_id     = var.default_org_id`
`org_id` 変更後: `org_id     = zitadel_org.br_dev.id`

加えて `module "platform_apps"` ブロックに以下の 1 行を追加（この module の 4 アプリは全て Envoy 系なので一律 JWT でよい）:

```hcl
  access_token_type = "OIDC_TOKEN_TYPE_JWT"
```

（`project_id = zitadel_project.platform.id` は変更不要。project が br-dev に移るため自動で追従する。Grafana/Argo/Flux の各ファイルは `access_token_type` を指定せず default の bearer のまま。）

- [ ] **Step 4: app_grafana.tf の `org_id` を置換**

`module "app_grafana"` ブロック内:

変更前: `org_id     = var.default_org_id`
変更後: `org_id     = zitadel_org.br_dev.id`

- [ ] **Step 5: app_flux_web.tf の `org_id` を置換**

`module "app_flux_web"` ブロック内:

変更前: `org_id     = var.default_org_id`
変更後: `org_id     = zitadel_org.br_dev.id`

- [ ] **Step 6: app_argo_workflows.tf の `org_id` を置換**

`module "app_argo_workflows"` ブロック内:

変更前: `org_id     = var.default_org_id`
変更後: `org_id     = zitadel_org.br_dev.id`

- [ ] **Step 7: validate**

Run: `terraform -chdir=terraform validate`
Expected: `Success!`（Task 5 完了時 green）。

- [ ] **Step 8: コミット**

```bash
git add terraform/modules/oidc_application/variables.tf terraform/modules/oidc_application/main.tf \
        terraform/apps_platform.tf terraform/app_grafana.tf terraform/app_flux_web.tf terraform/app_argo_workflows.tf
git commit -m "feat: repoint platform OIDC apps to br-dev; JWT access tokens for Envoy apps"
```

---

## Task 5: ユーザーを br-dev へ移し、admin ロールを付与

**Files:**
- Modify: `terraform/user_kukv.tf`
- Modify: `terraform/user_bradmin.tf`
- Create: `terraform/grants.tf`

- [ ] **Step 1: user_kukv.tf の `org_id` と org_member を置換**

`module "user_kukv"` の `org_id` と、`resource "zitadel_org_member" "kukv"` の `org_id` を置換:

変更前（モジュール）: `org_id       = var.default_org_id`
変更後（モジュール）: `org_id       = zitadel_org.br_dev.id`

変更前（org_member）: `org_id  = var.default_org_id`
変更後（org_member）: `org_id  = zitadel_org.br_dev.id`

- [ ] **Step 2: user_bradmin.tf の `org_id` を置換**

`module "user_bradmin"` の `org_id` を置換（`zitadel_instance_member.bradmin` は instance スコープなので **変更不要**）:

変更前: `org_id       = var.default_org_id`
変更後: `org_id       = zitadel_org.br_dev.id`

- [ ] **Step 3: grants.tf を作成**

`has_project_check = true` のため、platform アプリを使うユーザーは platform project への grant（authorization）が必須。ORG_OWNER / IAM_OWNER は管理ロールであって project authorization ではないので、別途 admin ロールを grant する。

```hcl
# has_project_check=true 下では grant を持たないユーザーはトークン発行を拒否
# される。kukv（daily admin）と bradmin（break-glass）に platform project の
# admin ロールを付与し、自分が締め出されないようにする。project / roles と
# 同一 apply で作られるため、check 有効化と grant 付与の間に窓は生じない。
resource "zitadel_user_grant" "kukv_admin" {
  org_id     = zitadel_org.br_dev.id
  project_id = zitadel_project.platform.id
  user_id    = module.user_kukv.id
  role_keys  = ["admin"]

  depends_on = [zitadel_project_role.platform]
}

resource "zitadel_user_grant" "bradmin_admin" {
  org_id     = zitadel_org.br_dev.id
  project_id = zitadel_project.platform.id
  user_id    = module.user_bradmin.id
  role_keys  = ["admin"]

  depends_on = [zitadel_project_role.platform]
}
```

- [ ] **Step 4: validate（ここで green になること）**

Run: `terraform -chdir=terraform validate`
Expected: `Success! The configuration is valid.`（Task 2〜5 で `var.default_org_id` の全参照が `zitadel_org.br_dev.id` に置換され、未解決参照が消える）

- [ ] **Step 5: コミット**

```bash
git add terraform/user_kukv.tf terraform/user_bradmin.tf terraform/grants.tf
git commit -m "feat: move kukv/bradmin to br-dev and grant platform admin role"
```

---

## Task 6: フラット `roles` claim を注入する Action

**Files:**
- Create: `terraform/action_roles_claim.tf`

- [ ] **Step 1: action_roles_claim.tf を作成**

Zitadel v1 Action は **action 名と同名の JS 関数**を実行する。ユーザーの project grant を平坦化して `roles` 文字列配列 claim を access token / userinfo に載せる。Envoy / Grafana / Argo はこの `roles` claim を共通で消費する（標準のネスト claim `urn:zitadel:iam:org:project:roles` は Envoy の claim マッチと相性が悪いため）。

```hcl
# 全トークンにフラット roles claim を注入する Action。br-dev org スコープ
# （Action/flow は org 単位で、platform project と users が br-dev にいるため）。
# Zitadel v1 Action は action.name と同名の JS 関数を呼ぶので、name と関数名を
# 一致させる（addRolesClaim）。
resource "zitadel_action" "add_roles_claim" {
  org_id          = zitadel_org.br_dev.id
  name            = "addRolesClaim"
  timeout         = "10s"
  allowed_to_fail = false

  script = <<-EOT
    function addRolesClaim(ctx, api) {
      if (ctx.v1.user.grants === undefined || ctx.v1.user.grants.count == 0) {
        return;
      }
      let roles = [];
      ctx.v1.user.grants.grants.forEach(grant => {
        grant.roles.forEach(role => {
          roles.push(role);
        });
      });
      api.v1.claims.setClaim('roles', roles);
    }
  EOT
}

# access token と userinfo の両方に claim が載るよう、2 つの trigger に同じ
# Action を attach する。
resource "zitadel_trigger_actions" "roles_access_token" {
  org_id       = zitadel_org.br_dev.id
  flow_type    = "FLOW_TYPE_CUSTOMISE_TOKEN"
  trigger_type = "TRIGGER_TYPE_PRE_ACCESS_TOKEN_CREATION"
  action_ids   = [zitadel_action.add_roles_claim.id]
}

resource "zitadel_trigger_actions" "roles_userinfo" {
  org_id       = zitadel_org.br_dev.id
  flow_type    = "FLOW_TYPE_CUSTOMISE_TOKEN"
  trigger_type = "TRIGGER_TYPE_PRE_USERINFO_CREATION"
  action_ids   = [zitadel_action.add_roles_claim.id]
}
```

- [ ] **Step 2: validate**

Run: `terraform -chdir=terraform validate`
Expected: `Success!`

- [ ] **Step 3: コミット**

```bash
git add terraform/action_roles_claim.tf
git commit -m "feat: add Action injecting flat roles claim into token and userinfo"
```

> **注意（実装後に必ず検証）**: `ctx.v1.user.grants.grants` のオブジェクト形状は Zitadel バージョン依存。Task 8 のトークン検証で `roles` claim が実際に `["admin"]` 等で出ることを確認するまで、この script は未検証とみなす。出ない場合は Zitadel console の Actions エディタで grants の形状（`.grants` 配列か iterable か）を確認して script を修正する。

---

## Task 7: セルフ登録を無効化（instance default login policy）

**Files:**
- Create: `terraform/login_policy.tf`

> プラン 1 で `auth.b8m.app` のログイン画面が公開到達可になったため、register ボタンが公開に晒される。両 org とも管理者プロビジョン運用なので、instance default で `allow_register = false` にして公開セルフ登録を塞ぐ。

- [ ] **Step 1: 現行の login policy 値を控える**

Zitadel console（`auth.b8m.app/ui/console` → Instance → Login Behavior and Security）または Admin API で、現在の lifetime 系・MFA 系の値を確認する。下記 Step 2 のテンプレートは Zitadel のデフォルト値。**もし現行が異なる場合は現行値に合わせ、`allow_register` だけを false にする**こと（lifetime を意図せず変えない）。

- [ ] **Step 2: login_policy.tf を作成**

```hcl
# Instance default login policy。allow_register=false で公開セルフ登録を無効化
# （auth.b8m.app 公開後の野良アカウント作成を防ぐ。両 org とも管理者
# プロビジョン運用）。register 以外は Zitadel デフォルト値。現行インスタンスが
# カスタムしている場合は現行値に合わせること（lifetime を意図せず変えない）。
resource "zitadel_default_login_policy" "this" {
  user_login                    = true
  allow_register                = false
  allow_external_idp            = true
  force_mfa                     = false
  force_mfa_local_only          = false
  passwordless_type             = "PASSWORDLESS_TYPE_ALLOWED"
  hide_password_reset           = "false"
  password_check_lifetime       = "240h0m0s"
  external_login_check_lifetime = "240h0m0s"
  multi_factor_check_lifetime   = "24h0m0s"
  mfa_init_skip_lifetime        = "720h0m0s"
  second_factor_check_lifetime  = "24h0m0s"
  ignore_unknown_usernames      = true
  default_redirect_uri          = ""
}
```

- [ ] **Step 3: validate**

Run: `terraform -chdir=terraform validate`
Expected: `Success!`

- [ ] **Step 4: コミット**

```bash
git add terraform/login_policy.tf
git commit -m "feat: disable public self-registration via default login policy"
```

---

## Task 8: plan レビュー・apply・カットオーバー検証

**Files:** なし（インフラ反映と検証）

> tofu-controller は `approvePlan: auto` で **merge=即 apply**。本タスクの plan レビューは PR 段階で行い、問題なければ merge する。

- [ ] **Step 1: plan を取り差分を目視確認**

PR の tofu-controller plan 出力（または手元で provider 到達可能なら `terraform -chdir=terraform plan`）を確認:

Expected:
- `zitadel_org.br_dev` / `zitadel_org.br_apps` … **create**（2）
- `zitadel_project.platform` … **replace**（org_id 変更で destroy+create）
- `module.platform_apps[*].zitadel_application_oidc.this`（4）/ `module.app_grafana` / `module.app_flux_web` / `module.app_argo_workflows` … **replace**（各 client_id/secret 再発行）
- `module.user_kukv.zitadel_human_user.this` / `module.user_bradmin.zitadel_human_user.this` … **replace**
- `zitadel_project_role.platform[*]`（4）/ `zitadel_user_grant.kukv_admin` / `zitadel_user_grant.bradmin_admin` … **create**
- `zitadel_action.add_roles_claim` / `zitadel_trigger_actions.*`（2）… **create**
- `zitadel_default_login_policy.this` … **create**（instance singleton を TF 管理下に）
- ZITADEL システム org 関連リソースへの変更が**無い**こと

> bradmin の `zitadel_instance_member`（IAM_OWNER）が destroy されていないことを必ず確認（user 再作成で member が一時的に参照切れにならないか注視。`module.user_bradmin.id` 参照で追従するはず）。

- [ ] **Step 2: merge して apply**

PR を main に merge し、tofu-controller の apply 完了を待つ（`interval: 10m`）。または手元 apply 可能なら:

Run: `terraform -chdir=terraform apply`
Expected: 上記差分が適用され、`tf-zitadel-output` Secret の値（client_id/secret）が更新される。キー名は不変。

- [ ] **Step 3: roles claim がトークンに載ることを検証（最重要）**

Zitadel console（`auth.b8m.app/ui/console`）で、kukv の OIDC アプリのいずれか（例 grafana）に対し token introspection / debug を行うか、実アプリでログイン後の ID/access token をデコードして、claim に:

```json
"roles": ["admin"]
```

が含まれることを確認する。

Expected: `roles` claim が `["admin"]` で存在する。**含まれない場合は Task 6 の注意に従い script を修正**（`ctx.v1.user.grants` の形状確認）してから再 apply。

- [ ] **Step 4: has_project_check の締め出しを検証**

grant を持たないテストユーザー（br-apps org に TF で 1 人だけ作る、または既存の非 grant ユーザー）で platform アプリ（例 grafana）にログインを試み、**Zitadel がトークン発行を拒否**する（アプリにアクセスできない）ことを確認する。

Expected: 非 grant ユーザーは platform アプリのトークンを得られない。

> このテストユーザーは検証用。恒久運用に不要なら検証後に削除する。

- [ ] **Step 5: 自分が締め出されていないことを確認**

kukv で各 platform アプリ（grafana / prometheus / longhorn 等）にログインでき、bradmin の break-glass（console への IAM_OWNER アクセス）が生きていることを確認する。

Expected: kukv / bradmin とも従来通りアクセス可能。

- [ ] **Step 6: セルフ登録が塞がれたことを確認**

公開コンテキスト（WARP オフ等）で `auth.b8m.app/ui/v2/login/` を開き、**「アカウント作成 / Register」リンクが表示されない**ことを確認する。

Expected: register 導線が無い。

---

## Self-Review（このプランと spec の突き合わせ）

- **spec「br-dev/br-apps 2 org・ZITADEL 不可侵」**: Task 1（orgs 作成）、全タスクで ZITADEL org 非参照。✅
- **spec「platform project を br-dev に再作成・check 群 ON」**: Task 2。✅
- **spec「4 ロール」**: Task 3。✅
- **spec「全アプリ再作成→新 secret、output キー据え置き」**: Task 4 ＋ 前提知識。✅
- **spec「kukv/bradmin を br-dev へ＋grant、IAM_OWNER 維持」**: Task 5。✅
- **spec「Zitadel Action でフラット roles claim」**: Task 6。✅
- **spec「移行で lock-out 回避（grant を project と同一 apply）」**: Task 5（grants）＋ Task 8 Step 1 の確認。✅
- **spec 非 goal「公開ユーザーは当面 TF プロビジョン（セルフ登録しない）」**: Task 7（register 無効化）で担保。✅
- **placeholder スキャン**: login policy の lifetime はデフォルト実値＋「現行に合わせる」注記（プレースホルダではない）。Action script は実コード＋検証ステップ付き。✅
- **型・参照整合**: `zitadel_org.br_dev.id` / `zitadel_project.platform.id` / `zitadel_project_role.platform`（for_each, role_key=each.key）/ `module.user_kukv.id` / `module.user_bradmin.id` / `zitadel_action.add_roles_claim.id` は全て定義済みリソースを参照。`role_keys = ["admin"]` は Task 3 の `local.platform_roles` のキーと一致。✅

---

## 次プランへの引き継ぎ

- 出力 `<app>_client_id` / `<app>_client_secret` のキー名は不変。値のみ変わる。
- トークンに `roles`（文字列配列、例 `["admin","maintainer"]`）claim が載る。
- Envoy 系 4 アプリ（alertmanager/hubble/longhorn/prometheus）の access token は **JWT**（`OIDC_TOKEN_TYPE_JWT`）。Grafana/Argo/Flux は bearer のまま。
- platform project は `has_project_check=true`。grant を持つユーザーのみトークン発行される。
- → プラン 3（br-cluster）は、この `roles` claim を Envoy `authorization` / Grafana / Argo / Flux で消費し、[認可マトリクス](./zitadel-multi-org-rbac.md#ロール--アプリ-認可マトリクス)を実装する。
