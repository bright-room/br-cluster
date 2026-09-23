# Identity

クラスタ内の **OIDC IdP** と、Zitadel のテナント / アプリ / ロールを **IaC で管理する仕組み**を提供するグループ。

## このグループが解決する課題

- アプリ層の **認証＋認可** を一元化 (Cloudflare Access はネットワーク境界、Zitadel はアプリの user 認証と **project role による認可**)
- ユーザー / プロジェクト / OIDC アプリ / **ロール** 登録を **GitHub リポで宣言的に管理** (手動 console 操作からの脱却)
- クラスタ内クライアント (Envoy SecurityPolicy / tofu-controller) が CF Tunnel を経由せず Zitadel に到達できるようにする
- k3s 管理者と公開アプリ利用者を **org で分離** し、管理者を admin/maintainer/developer/viewer の 4 ロールで細分化する

## グループ全体構成

<!-- TODO(figure): 2026-09-05 のノード再編を未反映。draw.io で更新が必要 -->

![Identity 全体構成](../assets/identity.svg)

OIDC クライアント側の接続パターンには 2 種類ある:

| パターン | 例 | 仕組み |
|---|---|---|
| **Native OIDC** (アプリ自前) | Argo Workflows / Flux Web | アプリが OIDC / OAuth2 をネイティブに喋る。`tf-zitadel-output` の `<app>_client_id` / `<app>_client_secret` を `ExternalSecret` で取り込み、`auth.b8m.app` の token endpoint に **直接** アクセスする。**`SecurityPolicy` は不要** |
| **Envoy SecurityPolicy** (Envoy 肩代わり) | Hubble UI | アプリは認証を喋れない / 共通化したい場合、HTTPRoute に `SecurityPolicy` を attach し Envoy が OIDC を肩代わり。未認証なら Zitadel にリダイレクトし、cookie で sidecar 化。さらに `jwt` provider ＋ `authorization` で `roles` claim を検査し **到達可否のロール認可** まで行う (access token は JWT 型) |

どちらのパターンでも、Zitadel 側の OIDC アプリ宣言は **`br-cluster-zitadel-terraform` の `oidc_application` モジュール群** (`br-dev` org の `platform` project) に集約され、tofu-controller が apply した結果が `tf-zitadel-output` Secret に書き出される (一次元情報)。クライアント側 (br-cluster) はそれを読むだけ。

ロールの強制スタイルは 2 系統: **Native OIDC** はアプリ内 RBAC に `roles` claim をマップ (Argo → SSO RBAC の SA、Flux Web → email impersonation ＋ k8s RBAC)。**Envoy SecurityPolicy** は `authorization` で到達可否のバイナリ判定。詳細は [#組織・プロジェクト・ロール (RBAC)](#組織プロジェクトロール-rbac)。

## グループ全体の設計判断

| 判断 | 採用 | 不採用 / 旧構成 | 理由 |
|---|---|---|---|
| OIDC IdP                | Zitadel (Go + Postgres) | Keycloak | JVM が Pi で重い、DB 運用コスト。Zitadel は `br-db1` の PostgreSQL (既存) に乗る |
| Zitadel リソース管理    | OpenTofu + tofu-controller (in-cluster) | console 手動 / GitHub Actions で apply | state を k8s Secret に置けばクラスタ寿命と同期。GitOps から外れない |
| クラスタ内 OIDC 解決経路 | CoreDNS で `auth.b8m.app` を Envoy VIP にリダイレクト | CF Tunnel を一周 | CF Access がクラスタ内クライアントの token endpoint を 403 で弾く問題を回避 |
| Envoy SecurityPolicy → Zitadel 参照 | `backendRefs` (Service 直指し) | `issuer` の DNS 解決まかせ | Envoy の c-ares リゾルバが in-cluster STRICT_DNS を取りこぼすことがあるため EDS 経由で確実化 |
| 旧 CF Access JWT 直検証 | **撤回** (Zitadel に移行) | 各アプリで JWKS 検証して auto-sign-up | アプリ単位の権限制御ができないため。CF Access はネットワーク層に役割を限定 |
| org 構造 | **2 org (`br-dev` / `br-apps`)** ＋ システム org `ZITADEL` 不可侵 | 単一 org / アプリごと org | k3s 管理者と公開アプリ利用者のユーザープールを隔離。project 移動は Zitadel 非対応なので最初から分ける |
| ロール認可の強制 | **全アプリ強制** (`has_project_check` ＋ `roles` claim) | role 消費可能アプリのみ | grant の無いユーザーはトークン発行段階で拒否。Envoy 系も `authorization` で締める |
| Envoy 系の認可手段 | **Envoy ネイティブ `authorization` ＋ jwt provider** | 外部 ext_authz サービス | 新規常駐 Pod ゼロ (Pi 負荷)。EG 1.7+ が SecurityPolicy `authorization` をサポート |
| roles claim の形 | **Zitadel Action でフラット `roles` 文字列配列** | 標準のネスト claim `urn:zitadel:iam:org:project:roles` | ネスト JSON マップは Envoy の claim マッチと相性が悪い |
| Envoy 系の access token | **JWT 型** (`OIDC_TOKEN_TYPE_JWT`) | opaque bearer | Envoy `jwt` provider が cookie 内の access token を検証するため。Native 系は ID token/userinfo で読むので bearer のまま |
| 公開アプリ利用者の登録 | **管理者プロビジョン** (TF で追加) ＋ セルフ登録無効 | セルフ登録 ON | `auth.b8m.app` 公開後の野良アカウント作成を防ぐ (instance default login policy `allow_register=false`) |

---

## Zitadel

### 概要

Go 製の OIDC / OAuth2 / SAML IdP。`auth.b8m.app` で公開し、`br-db1` の PostgreSQL の `zitadel` データベースに永続化する。

### ソース

- Helm: [`manifests/platform/zitadel/app/base/`](../../manifests/platform/zitadel/app/base/)
  - chart `zitadel` v9.34.0 ([`helm.yaml`](../../manifests/platform/zitadel/app/base/helm.yaml))
- HTTPRoute: [`manifests/platform/zitadel/config/base/httproute.yaml`](../../manifests/platform/zitadel/config/base/httproute.yaml)
- ReferenceGrant: [`manifests/platform/zitadel/app/base/referencegrant.yaml`](../../manifests/platform/zitadel/app/base/referencegrant.yaml)

### 設定の要点

| 項目 | 値 / 備考 |
|------|-----------|
| 公開ドメイン            | `auth.b8m.app` (`ExternalDomain`) |
| クラスタ内 TLS          | Off (`TLS.Enabled: false`)。Envoy が TLS 終端し、Pod へは HTTP |
| データベース            | `rdbms.prod.internal-service.bright-room.net:5432` (`br-db1` の PostgreSQL) / database `zitadel`、ユーザー `zitadel` (DB owner) |
| 初期化                  | `initJob.command: zitadel` で **Ansible `postgresql` role が作った DB / Role を再利用** (Helm 同梱の create を skip) |
| Master key / DB password | 1Password 経由で `zitadel-secrets` Secret に注入 (External Secrets) |
| SMTP                    | **Resend で稼働中** (`zitadel_smtp_config`、`set_active=true`)。招待 / メール検証 / パスワードリセットに使用。creds は 1Password `resend` item → `zitadel-smtp-creds` Secret → tofu-controller `varsFrom` |
| Replica                 | 1 (Pi のリソース節約)、worker ノード固定 (`nodeSelector: node_type: worker`) |

### ルーティング (HTTPRoute)

`zitadel-b8m` ([`httproute.yaml`](../../manifests/platform/zitadel/config/base/httproute.yaml)):

| パス             | 振り先 Service          | 備考 |
|------------------|-------------------------|------|
| `/ui/v2/login`   | `zitadel-login:3000`    | v4 以降、ログイン UI が Next.js の独立 Pod に分離 |
| その他           | `zitadel:8080`          | console / OIDC endpoint / API |

### ReferenceGrant の用途

別 namespace の `SecurityPolicy` (`kube-system`) から `zitadel` Service を `backendRefs` で参照させるための許可。**Envoy の c-ares リゾルバが in-cluster STRICT_DNS で取りこぼす問題の回避策**として、DNS ではなく EDS で当てている。オブザーバビリティ / Longhorn 撤去に伴い、それらの namespace からの参照は削除済み。

### CoreDNS のショートカット

[`platform/coredns`](networking.md#coredns) の hosts プラグインに以下が入っている:

```text
${CLUSTER_GATEWAY_IP} auth.b8m.app
```

`CLUSTER_GATEWAY_IP` は `172.22.52.200` (cluster-gateway)。

これにより、クラスタ内 Pod (tofu-controller、Envoy SecurityPolicy の OIDC discovery) は **CF Tunnel を経由せず**、Envoy Gateway VIP に直接当たる。

### 認証 (CF Access との関係)

`br-cloudflare-terraform` の `access_applications.tf` で `auth.b8m.app` を **path 分割** している。公開アプリ利用者が SSO ログイン画面に到達できるよう、login UI / OIDC endpoint は公開 (bypass)、admin console だけ保護する。

| CF Access application | path | policy |
|------|------|--------|
| `auth_public` (host root) | `auth.b8m.app` (login UI `/ui/v2/login`、OIDC `/oauth/v2/*` `/oidc/v1/*` `/.well-known/*`、assets) | **bypass (全員許可)**。Zitadel 自身が認証を担う |
| `auth_console` (具体 path) | `auth.b8m.app/ui/console` | GitHub org + WARP。より具体的な path なので host root の bypass より優先評価され保護が効く |

> 公開アプリ (`<name>.b8m.app`) を CF Access の `access_applications` map に**載せなければ** `deny_unmatched_requests=false` により素通り＝一般公開になる。

### 依存

- 前提: `br-db1` の PostgreSQL (Ansible `postgresql` role が `zitadel` DB / Role を事前作成)、External Secrets、Envoy Gateway (`cluster-gateway`)、cert-manager (`*.b8m.app`)
- これに依存: 全 OIDC 保護アプリ (Argo Workflows 等)

### 運用上の注意

- `br-db1` の `zitadel` ロール / DB は Ansible `postgresql` role が作成済み。Helm 側の init は **skip 前提** (`initJob.command: zitadel`)。両方走らせると失敗するので注意
- `masterkey` を再生成すると **既存の暗号化セッションが全部死ぬ**。1Password に厳重保管

---

## 組織・プロジェクト・ロール (RBAC)

org を隔離境界として、k3s 管理者と公開アプリ利用者を分ける。

```text
Zitadel instance
├─ org: ZITADEL (システム org)          ← 不可侵 (Management/Admin/Auth API・Console を抱える)
├─ org: br-dev                          ← 内部・k3s 管理
│   ├─ users: kukv (admin grant), bradmin (IAM_OWNER = break-glass)
│   ├─ 登録: 無効 (管理者プロビジョン)
│   └─ project: platform
│       ├─ roles: admin / maintainer / developer / viewer
│       ├─ project_role_check / has_project_check / project_role_assertion = true
│       ├─ apps: argo-workflows / flux-web / hubble
│       └─ Action: addRolesClaim (フラット roles claim を token/userinfo に注入)
└─ org: br-apps                         ← 公開アプリ利用者 (TF プロビジョン、登録無効)
    └─ project: <個人アプリごと>
```

### 認可の効き方

| 仕掛け | 効果 |
|--------|------|
| `has_project_check = true` | platform project に **grant を持たないユーザーはトークン発行を拒否**。grant 無し＝全アプリ全拒否 (br-apps の利用者が k3s 管理アプリに入れない実体) |
| `project_role_assertion = true` ＋ Action `addRolesClaim` | トークンに **フラット `roles` 文字列配列 claim** (`["admin", ...]`) を注入。標準のネスト claim を Envoy/各アプリが扱える形に整形 |
| ロール→アプリ | admin=全権 / maintainer=運用全般 / developer=アプリ中心 / viewer=読み取り。Envoy 系は到達可否、Native 系はアプリ内 RBAC レベル |

### roles claim の Action (落とし穴あり)

`addRolesClaim` は `FLOW_TYPE_CUSTOMISE_TOKEN` の `PRE_ACCESS_TOKEN_CREATION` / `PRE_USERINFO_CREATION` に attach。grant の null チェックは **緩い等価 (`==`)** を使う (Zitadel v4 では grant 無しトークンで `ctx.v1.user.grants` が null になり、`===` だと例外 → `allowed_to_fail=false` だとトークン発行ごと 500 になる)。保険で **`allowed_to_fail=true`**。

### 運用

- ユーザー追加は **br-dev に作成 ＋ platform project の role grant** が必須 (grant 無しは全拒否)。
- Zitadel ユーザーを作り直すと OIDC `sub` が変わり、sub でユーザーを持つアプリは再ログインで `User sync failed` になり得る。旧 OAuth ユーザーレコードを削除して作り直す。
- セルフ登録は instance default login policy `allow_register=false` で無効。`zitadel_default_login_policy` は **全フィールド上書き**なので、変更時は live 値 (Admin API `/admin/v1/policies/login`) をミラーし register 以外を変えないこと (MFA 方式・lifetime のリセット事故防止)。

---

## zitadel-terraform-app

### 概要

クラスタ内で **OpenTofu (terraform) を `infra.contrib.fluxcd.io/v1alpha2/Terraform` (tofu-controller) で実行**し、Zitadel のテナント / プロジェクト / OIDC アプリ / ロールを宣言的に管理する。

### ソース

- マニフェスト: [`manifests/platform/zitadel/terraform/base/`](../../manifests/platform/zitadel/terraform/base/)
  - `terraform.yaml` — `Terraform` リソース定義
  - `gitrepository.yaml` — 外部リポ `bright-room/br-cluster-zitadel-terraform` を Flux GitRepository として参照
  - `tf-runner.yaml` — runner Pod 用 ServiceAccount + Role + RoleBinding
- Terraform コード本体: **別リポ** [`bright-room/br-cluster-zitadel-terraform`](https://github.com/bright-room/br-cluster-zitadel-terraform)

### 動作モデル

| 項目 | 内容 |
|------|------|
| 実行サイクル          | `interval: 10m` で plan、`approvePlan: auto` で apply まで自動 |
| Terraform state       | tofu-controller が **k8s Secret として保持** (kubernetes backend)。クラスタ破棄と同期 |
| Provider 認証         | Helm が生成する `iam-admin` Secret (JWT profile JSON) を runner Pod に `/secrets/zitadel-admin/iam-admin.json` でマウント |
| Provider が呼ぶ host  | `auth.b8m.app` (CoreDNS のショートカットで Envoy VIP に解決) |
| 入力変数              | `zitadel_domain` を vars で渡す。`zitadel-smtp-creds` Secret を `varsFrom` で `TF_VAR_*` 化 |
| 出力                  | `tf-zitadel-output` Secret に `<app>_client_id` / `<app>_client_secret` を書き出し |
| Git アクセス          | 外部リポは GitHub App の secret (`flux-system`) を流用してクローン |

### 出力の使われ方

各アプリ namespace の `ExternalSecret` が `tf-zitadel-output` の値を `client-id` / `client-secret` に rename して `Secret` に同期。SecurityPolicy or アプリ自前の OIDC 設定はそれを参照する。

### 新規 k3s 管理アプリ (platform / br-dev) の追加手順

1. **br-cluster** 側で HTTPRoute を追加 (`<name>.b8m.app`)
2. **br-cluster-zitadel-terraform** 側で `oidc_application` モジュールのインスタンスを追加 (`apps_platform.tf` の `platform_apps` map か個別ファイル)。Envoy 系は `access_token_type = "OIDC_TOKEN_TYPE_JWT"`、`outputs.tf` に `<name>_client_id` / `_client_secret` を追加 → tofu-controller apply で `tf-zitadel-output` に書かれる
3. **br-cluster** 側で
   - `ExternalSecret` (`store: kubernetes-backend`、2 キーを `client-id` / `client-secret` に rename)
   - **Envoy 系**: `SecurityPolicy` に `oidc` (issuer `https://auth.b8m.app`、`backendRefs` で `zitadel` Service、`redirectURL: https://<name>.b8m.app/oauth2/callback`、`cookieNames.accessToken`) ＋ `jwt` provider (`extractFrom.cookies`、`remoteJWKS` は uri ＋ `zitadel` Service backendRefs) ＋ `authorization` (`roles` を `StringArray` で必要ロール判定)
4. **br-cluster** 側で `referencegrant.yaml` の `from` リストに対象 namespace を追加 (初回だけ)
5. **br-cloudflare-terraform** 側で `access_applications` map に `<name> = "<name>.b8m.app"` を追加 → CF Access (GitHub Org + WARP) が新ホストに効く

**Native OIDC** (Argo / Flux Web) の場合は 3 の `SecurityPolicy` を付けず、アプリ側の OAuth 設定で client 情報と **roles claim → アプリ内 RBAC** のマッピングを行う (Argo の `sso.rbac` ＋ SA、Flux Web の group impersonation。実例: [`manifests/platform/argo-workflows/app/base/values-workflows.yaml`](../../manifests/platform/argo-workflows/app/base/values-workflows.yaml))。

### 公開アプリ (br-apps) の追加

CF Access で保護しない一般公開アプリで Zitadel SSO を使う場合は、`br-apps` org に project ＋ OIDC アプリを足し、利用者を TF プロビジョンする。`access_applications` map には**載せない** (＝公開)。OIDC リクエストは org scope で `br-apps` に pin する。

### 依存

- 前提: Zitadel が起動済み、`iam-admin` Secret が存在、CoreDNS の `auth.b8m.app` ショートカット、GitHub App credentials Secret
- これに依存: 全 OIDC 保護アプリの client 情報供給元

### 運用上の注意

- `approvePlan: auto` のため、**外部リポへの merge が即 apply される**。レビューは PR 段階で完結させる
- state Secret は cluster ライフサイクルと同期 = **クラスタを reset すると Zitadel 側のリソースもドリフトする**。再構築時は state Secret も復元するか、import を覚悟する

---

## 関連

- [`docs/platform/networking.md`](networking.md) — Envoy SecurityPolicy / CoreDNS ショートカット / cluster-gateway
- [`docs/platform/secrets.md`](secrets.md) — External Secrets / 1Password Connect
- [`docs/platform/certificate.md`](certificate.md) — `*.b8m.app` 証明書
- [`docs/architecture.md`](../architecture.md) — 認証 2 層の設計判断
