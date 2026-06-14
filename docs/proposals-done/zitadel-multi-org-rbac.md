# 提案: Zitadel マルチ org 化と RBAC 再設計（公開アプリ SSO ＋ k3s 管理ロール）

> **この提案の位置づけ**
>
> 今後 k3s 上に **個人開発アプリ（一般公開）** を展開し、その一部に Zitadel
> SSO を載せていくにあたり、現状の Zitadel 構成では破綻する 2 点を解消する:
> (A) `auth.b8m.app` が Cloudflare Access に守られていて、公開アプリの利用者が
> SSO ログイン画面に到達できない。(B) Zitadel に認可が無く（`project_role_check`
> = false）、ユーザーを増やすと全リソースにアクセスできてしまう。
>
> 解決方針は **org を分けて隔離境界を作り、platform project に 4 ロールを定義
> して全アプリで強制し、CF Access の path 分割でログイン画面だけ公開到達可に
> する**。設計確定までの議論は brainstorming セッションで実施済み。

作成日: 2026-06-14

## 背景・動機

現状 k3s にデプロイされているのは platform リソースのみ
（[`docs/platform/`](../platform/)）。ここに **個人開発アプリ** を加える:

- 個人開発アプリは **アクセス制限なしで一般公開**（Envoy + Cloudflare Tunnel
  経由）。CF Access は付けない。
- 一部のアプリは **Zitadel による SSO ログイン** を実装予定。

この前提で現状の Zitadel 構成（[`docs/platform/identity.md`](../platform/identity.md)）
を見直すと、2 つの問題がある。

### 問題 A — ログイン画面に到達できない

`auth.b8m.app` を含む `*.b8m.app` 全ホストが Cloudflare Access により
**GitHub org `bright-room` メンバー ＋ WARP posture** で保護されている
（[`br-cloudflare-terraform` `access_applications.tf`](https://github.com/bright-room/br-cloudflare-terraform)）。
公開アプリ（CF Access なし）の利用者が SSO を要求して `auth.b8m.app` に
リダイレクトされても、GitHub org メンバーでも WARP 接続でもないため
**ログイン画面そのものに到達できない**。

### 問題 B — 認可がスカスカ

Zitadel は単一の自動生成 org（`ZITADEL` / id `369442873638192055`）に
`platform` project が同居し、`project_role_check = false`（認可なし、identity
のみ）。認可は実質「CF Access の GitHub org メンバーか」だけで効いている。
このままユーザーを増やすと **全員が全リソースにアクセスできてしまう**。
個人開発アプリの利用者と k3s 管理者を分け、さらに k3s 管理の中も
admin / maintainer / developer に細分化したい。

## ゴール / 非ゴール

| | 内容 |
|---|------|
| ゴール | (1) 公開アプリ利用者が `auth.b8m.app` のログイン画面に到達できる。(2) k3s 管理アプリへのアクセスを 4 ロールで全アプリ強制する。(3) 公開アプリ利用者と k3s 管理者を org レベルで隔離する |
| 非ゴール | (1) 個人開発アプリ自体のデプロイ（HTTPRoute / Tunnel は既存パターンの再利用。本提案では SSO 配線パターンの提示に留める）。(2) 公開アプリのセルフ登録（公開ユーザーも当面は TF プロビジョン）。(3) CAPTCHA / WAF 等の spam 対策 |

## 採用 / 不採用 / 理由（決定事項）

| 論点 | 採用 | 不採用 | 理由 |
|------|------|--------|------|
| org 構造 | **2 org（br-dev / br-apps）に分割** | 単一 org + project grant | k3s 管理者と公開アプリ利用者のユーザープールを隔離（blast radius）。将来 branding/login policy も分けられる |
| br-dev の作り方 | **完全新規 org として作成し platform 一式を移設** | 既存 default org をリネーム+import | Zitadel system org（`ZITADEL`）から自分のリソースを clean に分離する。client secret 総入れ替えのコストは許容 |
| `ZITADEL` system org | **一切触らない（生かしたまま）** | リネーム / 削除 / default 付け替え | Management/Admin/Auth API・Console を支えるシステム project を抱えるため。停止・削除は不可 |
| 公開ユーザーの作成 | **管理者プロビジョン（TF で追加）** | セルフ登録 ON | 当面は不特定多数に開かず、知人/限定メンバー想定。`human_user` モジュール流用で kukv/bradmin と同じ手順 |
| ロール強制の範囲 | **全アプリ強制** | role 消費可能アプリのみ | 「ユーザーを増やしても勝手に全部見えない」を担保するため SecurityPolicy 系も含めて締める |
| SecurityPolicy 系の認可手段 | **Envoy ネイティブ `authorization` ＋ jwt provider** | 外部 ext_authz サービス | 新規常駐 Pod ゼロ（Pi 負荷を増やさない）。EG 1.7.2 が SecurityPolicy `authorization` をサポート |
| ロール claim 整形 | **Zitadel Action でフラット `roles` 配列 claim を注入** | 標準のネスト claim をそのまま使う | `urn:zitadel:iam:org:project:roles` はネスト JSON マップで Envoy の claim マッチと相性が悪い |
| ログイン到達性 | **CF Access を path 分割（console 保護 / login・OIDC 公開）** | `auth.b8m.app` 全体を bypass | admin console は GitHub org + WARP で守ったまま、login UI と OIDC endpoint だけ公開到達可にする |

## 目標アーキテクチャ

### org / project / user トポロジ

```
Zitadel instance
├─ org: ZITADEL (system org / 369442873638192055)   ← 不可侵
│   └─ project: ZITADEL（API/Console/IAM。Zitadel が管理）
│       ※ instance default org も ZITADEL のまま維持
│
├─ org: br-dev (新規)                                ← 内部・k3s 管理
│   ├─ 登録: 無効（管理者プロビジョンのみ）
│   ├─ users: kukv(admin grant), bradmin(IAM_OWNER = break-glass)
│   └─ project: platform（ZITADEL org から分離・再作成）
│       ├─ roles: admin / maintainer / developer / viewer
│       ├─ project_role_check = true / has_project_check = true / role_assertion = true
│       └─ apps: grafana, argo-workflows, flux-web, alertmanager,
│                hubble, longhorn, prometheus（再作成 → 新 client secret）
│
└─ org: br-apps (新規)                               ← 公開アプリ利用者
    ├─ 登録: 無効（管理者プロビジョンのみ。TF で追加）
    ├─ users: 公開アプリ利用者
    └─ project: <個人アプリごと>
        ※ 公開アプリの OIDC は org scope で br-apps に pin
          （instance default の ZITADEL に流さない）
```

隔離境界の肝:

- **org** が「誰が k3s を触れるか」と「誰が公開アプリを使うか」を分ける。
- platform project の **`has_project_check = true`** により、grant を持たない
  ユーザー（br-apps の利用者など）は **トークン発行段階で拒否** される。
  これが「公開ユーザーが k3s 管理アプリに入れない」ことの実体。

### 4 ロールの意味づけ

| role key | 想定 | 代表ユーザー |
|---|---|---|
| `admin` | クラスタ全権 | kukv, bradmin |
| `maintainer` | 運用全般（storage・alert silence 含む）、破壊的操作は限定 | 運用協力者 |
| `developer` | アプリ中心（dashboard 編集・deploy・sync）、storage/alert silence 不可 | 開発者 |
| `viewer` | 読み取りのみ（dashboard 閲覧） | 閲覧者 |

### ロール → アプリ 認可マトリクス

強制スタイルは 2 種類:

- **ネイティブ系**（Grafana / Argo / Flux）: role をアプリ内 RBAC レベルにマップ（粒度あり）。
- **Envoy 系**（Prometheus / Alertmanager / Hubble / Longhorn）: Envoy
  `authorization` で **到達可否のバイナリ**（アプリ内 RBAC が無いため）。

| アプリ | 強制 | admin | maintainer | developer | viewer |
|---|---|---|---|---|---|
| Grafana | ネイティブ | Admin | Editor | Editor | Viewer |
| Argo Workflows | ネイティブ | admin | edit/submit | submit | read |
| Flux Web (k8s RBAC) | ネイティブ | full | reconcile/suspend | view+sync | view |
| Prometheus (read 専 UI) | Envoy 可否 | ✅ | ✅ | ✅ | ✅ |
| Hubble (read 専) | Envoy 可否 | ✅ | ✅ | ✅ | ❌ |
| Alertmanager (silence=変更) | Envoy 可否 | ✅ | ✅ | ❌ | ❌ |
| Longhorn (storage 全権) | Envoy 可否 | ✅ | ✅ | ❌ | ❌ |

- ネイティブ系の RBAC レベルは各アプリの OIDC role/group マッピングで実現
  （Grafana の `auth.generic_oauth` role attribute path、argo-server の group
  マッピング、Flux Web の email → k8s RBAC impersonation）。
- Envoy 系は SecurityPolicy の `authorization` ルールが、必要ロールを
  `roles` claim に含むトークンだけ通す。

## コンポーネント別の変更

3 リポジトリにまたがる。

### br-cluster-zitadel-terraform（Zitadel IaC）

| 変更 | 内容 |
|---|------|
| org `br-dev` 新規作成 | `zitadel_org.br_dev`。`var.default_org_id` の参照を全面的に `br-dev` の id へ切替 |
| org `br-apps` 新規作成 | `zitadel_org.br_apps`。公開アプリ project の親 |
| platform project 再作成 | `br-dev` 配下に作り直し。`project_role_check` / `has_project_check` / `project_role_assertion` を true |
| roles 定義 | `zitadel_project_role` × 4（admin/maintainer/developer/viewer） |
| grants | kukv → admin。bradmin は IAM_OWNER（instance member）維持。将来ユーザーは appropriate role |
| 全 OIDC アプリ再作成 | `oidc_application` モジュール群を `br-dev` の platform project に再作成。`access_token_role_assertion` / token type を Envoy 系で JWT 認可可能な設定へ（実装時に検証） |
| Zitadel Action | 全トークンに `roles: [...]` フラット配列 claim を注入する Action を追加 |
| users (br-dev/br-apps) | `human_user` モジュール流用。kukv/bradmin を `br-dev` に、公開ユーザーを `br-apps` に |
| outputs | キー名（`<app>_client_id` 等）は据え置き。値だけ変わる |

> **キー名据え置きが重要**: `tf-zitadel-output` Secret のキー名を変えなければ、
> br-cluster 側の ExternalSecret は値の更新を自動 sync するだけで済む。

### br-cluster（k8s manifests）

| 変更 | 内容 |
|---|------|
| SecurityPolicy（Envoy 系 4 アプリ） | `jwt` provider（issuer `https://auth.b8m.app` の JWKS で検証）＋ `authorization` ルール（`principal.jwt.claims.roles` に必要 role を要求）を追加 |
| Grafana | `auth.generic_oauth` の role attribute path を `roles` claim → Grafana role にマップ |
| Argo Workflows | group/role マッピングを `roles` claim ベースに |
| Flux Web | email claim による k8s RBAC impersonation は維持。k8s 側 RBAC をロールに対応付け |
| ExternalSecret | キー名据え置きなので原則変更不要（値は自動再 sync） |
| docs | [`identity.md`](../platform/identity.md) を実態に合わせて更新（下記「ドキュメント修正」） |

### br-cloudflare-terraform（CF Access）

| 変更 | 内容 |
|---|------|
| `auth` app を 2 分割 | `auth-console`（`auth.b8m.app/ui/console`、GitHub org + WARP で保護維持）＋ `auth-public`（`auth.b8m.app` host root、Bypass = 全員許可） |
| 個人アプリ | `access_applications` map に **載せない**。`deny_unmatched_requests = false` なので未登録ホストは素通り（＝公開） |

CF Access はより具体的な path のアプリが優先されるため、`auth-public` を
全員 Bypass にしても `auth-console` の保護は効いたまま。これで login UI
(`/ui/v2/login`) / OIDC endpoint (`/oauth/v2/*`, `/oidc/v1/*`,
`/.well-known/*`) / assets が公開到達可になる。

## 移行計画（完全新規ゆえの一度きりカットオーバー）

| 順 | 作業 | lock-out / 事故対策 |
|---|------|---------------------|
| 1 | **CF Access path 分割を先行投入**（br-cloudflare-terraform） | 低リスク。先に入れておけば後続でログイン到達性が確保される |
| 2 | **zitadel-terraform を一括 apply**: br-dev org・platform project（role+check）・4 roles・全アプリ再作成・kukv/bradmin を br-dev へ + grant・Action・br-apps org | grant を project と**同一 apply** で作成し `has_project_check` の締め出しを回避。bradmin の IAM_OWNER instance member を維持 |
| 3 | **br-cluster 反映**: 各 SecurityPolicy に jwt+authorization 追加、Grafana/Argo/Flux の role マッピング更新 | `tf-zitadel-output` のキー名据え置き → ExternalSecret は自動再 sync |
| 4 | **カットオーバー**: client secret 総入れ替えのため全アプリ一度きりの再ログインが発生 | 事前に order を流して全 green を確認してから周知 |

補足:

- tofu-controller が使う `iam-admin`（instance IAM_OWNER、`ZITADEL` org 在籍）は
  不変なので、移行中も TF は動き続ける。
- `approvePlan: auto` のため zitadel-terraform は **外部リポへの merge が即 apply**。
  段階を分けて PR を出す（org/project/role 先行 → アプリ → outputs）か、検証を
  PR 段階で完結させる。

## リスクと留意点

| リスク | 対策 |
|---|------|
| `has_project_check=true` で自分が締め出される | grant を project と同一 apply で作る。bradmin break-glass（IAM_OWNER）を常に確保 |
| Envoy `jwt` 認可が Zitadel トークン型と噛み合わない | 実装前に token type（opaque bearer → JWT access token か ID token claim か）と Action の claim 注入先（access/id token）を PoC で検証 |
| `roles` claim のフラット化が Action で意図通り出ない | Action 単体を console で検証してから全アプリ展開 |
| client secret 総入れ替え中の一時的なログイン不可 | カットオーバーを周知。order で事前検証 |
| CF Access path 優先順位の誤りで console が露出 | `auth-console`（具体 path）が `auth-public`（host root）より優先されることを apply 後に実機確認 |

## ドキュメント修正（本提案に付随）

[`docs/platform/identity.md`](../platform/identity.md) は実態とズレている箇所がある:

- 「SMTP 現状未設定（Resend 整備後に有効化予定）」→ 実際は
  [`br-cluster-zitadel-terraform` `smtp.tf`](https://github.com/bright-room/br-cluster-zitadel-terraform)
  で **Resend SMTP が `set_active = true` で稼働中**。記述を更新する。
- 「Zitadel は identity のみ、authz は CF Access」→ 本提案で **Zitadel が
  ロール認可を担う** ように変わる。2 層認証の役割分担を書き直す。
- org 構造（単一 default org）→ **br-dev / br-apps / ZITADEL の 3 org** に更新。

## 関連

- [`docs/platform/identity.md`](../platform/identity.md) — Zitadel / OIDC（本提案で更新）
- [`docs/platform/networking.md`](../platform/networking.md) — Envoy SecurityPolicy / CF Tunnel / CoreDNS ショートカット
- [`docs/platform/policy.md`](../platform/policy.md) — manifests のガードレール
- `br-cluster-zitadel-terraform` — Zitadel IaC（org/project/role/app/user/Action）
- `br-cloudflare-terraform` `zero_trust/access_applications.tf` — CF Access path 分割
