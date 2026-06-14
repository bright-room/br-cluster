# br-cluster ロール強制配線 実装プラン（プラン 3/3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zitadel が発行する `roles` claim を全 platform アプリで消費し、[認可マトリクス](./zitadel-multi-org-rbac.md#ロール--アプリ-認可マトリクス)を実装する。Envoy 系（Prometheus/Alertmanager/Hubble/Longhorn）は SecurityPolicy の `jwt`+`authorization` で到達可否を、ネイティブ系（Grafana/Argo/Flux）は各アプリの OIDC role マッピングで RBAC レベルを強制する。

**Architecture:** `br-cluster` の manifests。Envoy 系は SecurityPolicy に `oidc.cookieNames.accessToken`（access token を cookie 保存）＋ `jwt` provider（その cookie から検証）＋ `authorization`（`roles` を `StringArray` でマッチ）を足す。最大の不確実性（OIDC+JWT の合成と in-cluster JWKS 取得）は **1 アプリ（longhorn）の spike** で先に潰し、動いた形を他 3 つに横展開する。

**Tech Stack:** Envoy Gateway 1.7.2（`gateway.envoyproxy.io/v1alpha1` SecurityPolicy）、Grafana `auth.generic_oauth`、Argo Workflows SSO RBAC、flux-operator Web（OAuth2 impersonation + k8s RBAC）。GitOps は Flux（merge=reconcile）。

> **このプランは [`zitadel-multi-org-rbac.md`](./zitadel-multi-org-rbac.md) の 3 プラン中 3/3。** 前提: **プラン 2 が apply 済み**で (1) トークンにフラット `roles` claim が載る、(2) Envoy 系 4 アプリの OIDC app が **JWT access token**（`access_token_type = OIDC_TOKEN_TYPE_JWT`）になっている、(3) `has_project_check=true`。**対象リポジトリは `br-cluster`。**

---

## 前提知識（context ゼロの実装者向け）

- **Envoy 系アプリの現状**: [`securitypolicy-*.yaml`](https://github.com/bright-room/br-cluster/tree/main/manifests/platform) は `oidc` ブロックのみで authz が無い（認証だけ）。アプリ自身に RBAC が無いため、到達可否を Envoy で決める＝バイナリ認可。
- **EG の OIDC+JWT 合成パターン（検証済み）**: `oidc` でログイン → `oidc.cookieNames.accessToken` で access token を named cookie に保存 → `jwt` provider が `extractFrom.cookies` でその cookie を取り出し検証 → `authorization` ルールが `principal.jwt.claims` の `roles`（`valueType: StringArray`）をマッチ。
- **なぜ JWT access token が要るか**: jwt provider は JWT を検証するため、access token が opaque だと検証できない。プラン 2 で Envoy 系アプリだけ `access_token_type = OIDC_TOKEN_TYPE_JWT` にしてある。`roles` claim は Action が `PRE_ACCESS_TOKEN_CREATION` で JWT access token に注入する。
- **in-cluster JWKS 取得**: jwt provider の `remoteJWKS` は `uri`（JWKS の URL）に加え `backendRefs`（Zitadel Service 直指し）をサポートする。既存 `oidc.provider.backendRefs` と同じく c-ares 回避のため backendRefs を併用する。Zitadel の JWKS path は `/oauth/v2/keys`。
- **ReferenceGrant**: SecurityPolicy（kube-prom-stack / kube-system / longhorn-system）から `zitadel` ns の Service を参照する許可は [`referencegrant.yaml`](https://github.com/bright-room/br-cluster/blob/main/manifests/platform/zitadel/app/base/referencegrant.yaml) に既存。`from` は kind=SecurityPolicy なので oidc/jwt 双方の backendRefs を同じ grant でカバーする（新規 namespace を足す場合のみ追記）。
- **ネイティブ系の role 消費**:
  - Grafana: `auth.generic_oauth.role_attribute_path`（JMESPath）で claim → Grafana role（Admin/Editor/Viewer）。
  - Argo: `sso.rbac.enabled: true` ＋ `sso.customGroupClaimName: roles` ＋ ServiceAccount の `workflows.argoproj.io/rbac-rule` annotation で role → SA。
  - Flux Web: `impersonation.groups` を `claims.roles` から取り、k8s RBAC（Group バインド）で認可。
- **認可マトリクス**（[spec](./zitadel-multi-org-rbac.md#ロール--アプリ-認可マトリクス)再掲）:

  | アプリ | admin | maintainer | developer | viewer |
  |---|---|---|---|---|
  | Grafana | Admin | Editor | Editor | Viewer |
  | Argo | admin | edit | edit | read |
  | Flux Web | admin | admin | edit | view |
  | Prometheus | ✅ | ✅ | ✅ | ✅ |
  | Hubble | ✅ | ✅ | ✅ | ❌ |
  | Alertmanager | ✅ | ✅ | ❌ | ❌ |
  | Longhorn | ✅ | ✅ | ❌ | ❌ |

- **作業ディレクトリ**: 全コマンドは `br-cluster` リポジトリ root。

---

## ファイル構成

| 操作 | パス | 責務 |
|---|---|---|
| Modify | `manifests/platform/longhorn/config/base/securitypolicy-longhorn.yaml` | spike: jwt+authorization 追加（admin/maintainer） |
| Modify | `manifests/platform/kube-prometheus-stack/app/base/securitypolicy-alertmanager.yaml` | jwt+authorization（admin/maintainer） |
| Modify | `manifests/platform/kube-prometheus-stack/app/base/securitypolicy-prometheus.yaml` | jwt+authorization（全 4 ロール） |
| Modify | `manifests/platform/cilium/app/components/hubble/securitypolicy-hubble.yaml` | jwt+authorization（admin/maintainer/developer） |
| Modify | `manifests/platform/grafana/app/base/values.yaml` | role_attribute_path 追加 |
| Modify | `manifests/platform/argo-workflows/app/base/values-workflows.yaml` | sso.rbac 有効化 + customGroupClaimName |
| Create | `manifests/platform/argo-workflows/app/base/rbac-sso.yaml` | role→SA（rbac-rule）+ RoleBinding |
| Modify | `manifests/platform/flux-operator/web/base/config.yaml` | impersonation.groups を roles claim から |
| Modify | `manifests/platform/flux-operator/web/base/rbac.yaml` | email バインド → Group バインド |

---

## Task 1: SPIKE — longhorn で jwt+authorization を成立させる

> **このタスクの目的は「動く形を 1 つ確定する」こと。** ここで詰めた SecurityPolicy の構造を Task 2 で他 3 アプリに複製する。失敗時は Envoy のログを見て uri/scheme/backendRefs を調整する。

**Files:**
- Modify: `manifests/platform/longhorn/config/base/securitypolicy-longhorn.yaml`

- [ ] **Step 1: longhorn の SecurityPolicy に cookieNames / jwt / authorization を追加**

既存の `oidc` ブロックはそのままに、`oidc` に `cookieNames` を足し、`spec` 直下に `jwt` と `authorization` を追加する。変更後の全文:

```yaml
---
# Gates longhorn.b8m.app behind Zitadel OIDC at the Envoy Gateway layer,
# then authorizes by Zitadel project role (admin / maintainer) via a JWT
# provider that reads the OIDC access token from a cookie.
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: longhorn-oidc
  namespace: longhorn-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: longhorn-ui-b8m
  oidc:
    provider:
      issuer: https://auth.b8m.app
      backendRefs:
        - group: ""
          kind: Service
          name: zitadel
          namespace: zitadel
          port: 8080
    clientIDRef:
      name: longhorn-oidc
    clientSecret:
      name: longhorn-oidc
    redirectURL: https://longhorn.b8m.app/oauth2/callback
    logoutPath: /oauth2/logout
    scopes:
      - openid
      - profile
      - email
    # access token を named cookie に保存し、下の jwt provider が読む。
    cookieNames:
      accessToken: longhorn-access-token
  jwt:
    providers:
      - name: zitadel
        issuer: https://auth.b8m.app
        remoteJWKS:
          uri: https://auth.b8m.app/oauth/v2/keys
          backendRefs:
            - group: ""
              kind: Service
              name: zitadel
              namespace: zitadel
              port: 8080
        extractFrom:
          cookies:
            - longhorn-access-token
  authorization:
    defaultAction: Deny
    rules:
      - name: allow-admin-maintainer
        action: Allow
        principal:
          jwt:
            provider: zitadel
            claims:
              - name: roles
                valueType: StringArray
                values:
                  - admin
                  - maintainer
```

- [ ] **Step 2: kustomize build で dry-run 検証**

Run: `kubectl kustomize manifests/platform/longhorn/config/base`
Expected: エラーなく SecurityPolicy がレンダリングされ、`jwt` / `authorization` ブロックが出力に含まれる。

- [ ] **Step 3: conftest（policy ガードレール）に通す**

Run: `make policy/test`
Expected: 既存 policy 4 本に違反せず pass（SecurityPolicy は対象外だが回帰確認）。

- [ ] **Step 4: コミット**

```bash
git add manifests/platform/longhorn/config/base/securitypolicy-longhorn.yaml
git commit -m "feat(longhorn): authorize by Zitadel role (admin/maintainer) via jwt+authorization"
```

- [ ] **Step 5: merge して Flux reconcile を待ち、実機で挙動確認**

PR を main に merge し、Flux が SecurityPolicy を適用するのを待つ（または対象ブランチを cluster に向けてテスト）。次を確認:

1. **admin（kukv）**で `https://longhorn.b8m.app` にログイン → **到達できる**。
2. **viewer ロールのテストユーザー**（プラン 2 Task 8 の検証ユーザーに viewer grant を付けて使う）で同 URL → **403（Deny）**。

Expected: admin は通り、viewer は弾かれる。

- [ ] **Step 6: 失敗時のデバッグ（通らない / 全部 403 になる場合）**

Envoy proxy のログを確認:

Run: `kubectl -n envoy-gateway-system logs deploy/envoy-<gateway> -c envoy | grep -i "jwt\|jwks\|rbac" | tail -50`
（gateway pod 名は `kubectl -n envoy-gateway-system get pods` で確認）

確認ポイント:
- `jwks` 取得失敗 → `remoteJWKS.uri` の scheme/path、`backendRefs` を見直す（`uri` を `http://auth.b8m.app/oauth/v2/keys` にする / backendRefs を外して CoreDNS ショートカット任せにする等を試す）。
- `jwt` が cookie から取れない → access token が JWT になっているか（プラン 2 の `access_token_type`）、cookie 名一致を確認。
- claim 不一致で全 Deny → トークンを decode して `roles` claim が実在するか（プラン 2 Task 8 Step 3）を確認。

> **動いた構成を「正」として確定する。** Task 2 はこの確定形を複製する。もし最終形が上記から変わった場合（uri scheme 等）、Task 2 のテンプレートも同じ変更を反映すること。

---

## Task 2: 他 3 つの Envoy 系アプリへ横展開

> Task 1 で確定した jwt+authorization 構造を、alertmanager / prometheus / hubble に複製する。**差分は `authorization.rules[].principal.jwt.claims[].values`（許可ロール）と cookie 名 / clientIDRef / redirectURL / namespace だけ**。

**Files:**
- Modify: `manifests/platform/kube-prometheus-stack/app/base/securitypolicy-alertmanager.yaml`
- Modify: `manifests/platform/kube-prometheus-stack/app/base/securitypolicy-prometheus.yaml`
- Modify: `manifests/platform/cilium/app/components/hubble/securitypolicy-hubble.yaml`

- [ ] **Step 1: alertmanager（admin/maintainer）**

`securitypolicy-alertmanager.yaml` の `oidc` に `cookieNames.accessToken: alertmanager-access-token` を足し、`spec` 直下に `jwt`（cookie 名 `alertmanager-access-token`、remoteJWKS は Task 1 と同一）と `authorization` を追加:

```yaml
  authorization:
    defaultAction: Deny
    rules:
      - name: allow-admin-maintainer
        action: Allow
        principal:
          jwt:
            provider: zitadel
            claims:
              - name: roles
                valueType: StringArray
                values:
                  - admin
                  - maintainer
```

jwt ブロック（Task 1 と同形、cookie 名だけ差し替え）:

```yaml
  jwt:
    providers:
      - name: zitadel
        issuer: https://auth.b8m.app
        remoteJWKS:
          uri: https://auth.b8m.app/oauth/v2/keys
          backendRefs:
            - group: ""
              kind: Service
              name: zitadel
              namespace: zitadel
              port: 8080
        extractFrom:
          cookies:
            - alertmanager-access-token
```

- [ ] **Step 2: prometheus（全 4 ロール）**

`securitypolicy-prometheus.yaml` に同様に追加。cookie 名は `prometheus-access-token`。authorization は 4 ロール全許可:

```yaml
  authorization:
    defaultAction: Deny
    rules:
      - name: allow-all-platform-roles
        action: Allow
        principal:
          jwt:
            provider: zitadel
            claims:
              - name: roles
                valueType: StringArray
                values:
                  - admin
                  - maintainer
                  - developer
                  - viewer
```

jwt ブロックは Step 1 と同形で `extractFrom.cookies: [prometheus-access-token]`。

- [ ] **Step 3: hubble（admin/maintainer/developer）**

`securitypolicy-hubble.yaml`（namespace: kube-system）に同様に追加。cookie 名は `hubble-access-token`。authorization:

```yaml
  authorization:
    defaultAction: Deny
    rules:
      - name: allow-admin-maintainer-developer
        action: Allow
        principal:
          jwt:
            provider: zitadel
            claims:
              - name: roles
                valueType: StringArray
                values:
                  - admin
                  - maintainer
                  - developer
```

jwt ブロックは `extractFrom.cookies: [hubble-access-token]`。

- [ ] **Step 4: 3 ファイルを kustomize build で検証**

Run:
```bash
kubectl kustomize manifests/platform/kube-prometheus-stack/app/base >/dev/null && echo OK-prom
kubectl kustomize manifests/platform/cilium/app/components/hubble >/dev/null && echo OK-hubble
```
Expected: `OK-prom` / `OK-hubble` が出てエラーなし。

- [ ] **Step 5: policy/test ＋ コミット**

Run: `make policy/test`
Expected: pass。

```bash
git add manifests/platform/kube-prometheus-stack/app/base/securitypolicy-alertmanager.yaml \
        manifests/platform/kube-prometheus-stack/app/base/securitypolicy-prometheus.yaml \
        manifests/platform/cilium/app/components/hubble/securitypolicy-hubble.yaml
git commit -m "feat: authorize alertmanager/prometheus/hubble by Zitadel role"
```

---

## Task 3: Grafana の role_attribute_path

**Files:**
- Modify: `manifests/platform/grafana/app/base/values.yaml`

- [ ] **Step 1: auto_assign を外し role_attribute_path を追加**

`grafana.ini` の `users.auto_assign_org_role: Admin` を削除し、`auth.generic_oauth` に role マッピングを追加する。

`users` ブロック変更前:

```yaml
  users:
    auto_assign_org_role: Admin
```

変更後（`auto_assign_org_role` 行を削除。`users` に他キーが無ければ `users:` ブロックごと削除）。

`auth.generic_oauth` ブロックに以下を追記（`allow_sign_up: true` の下）:

```yaml
    # roles claim（Zitadel Action が注入する文字列配列）を Grafana role に
    # マップ。admin→Admin / maintainer・developer→Editor / viewer→Viewer。
    # 一致が無ければアクセス拒否（role_attribute_strict）。
    role_attribute_path: >-
      contains(roles[*], 'admin') && 'Admin'
      || contains(roles[*], 'maintainer') && 'Editor'
      || contains(roles[*], 'developer') && 'Editor'
      || contains(roles[*], 'viewer') && 'Viewer'
    role_attribute_strict: true
```

> JMESPath: `roles` claim は文字列配列。`contains(roles[*], 'admin')` で判定。`role_attribute_strict: true` によりどの role にも一致しないユーザーはログインを拒否される（has_project_check と二重で k3s 非管理者を締め出す）。

- [ ] **Step 2: kustomize build 検証**

Run: `kubectl kustomize manifests/platform/grafana/app/base >/dev/null && echo OK`
Expected: `OK`。

- [ ] **Step 3: コミット**

```bash
git add manifests/platform/grafana/app/base/values.yaml
git commit -m "feat(grafana): map Zitadel roles to Grafana org roles via role_attribute_path"
```

- [ ] **Step 4: merge 後の実機確認**

admin（kukv）で grafana にログイン → Org role が **Admin**。viewer テストユーザー → **Viewer**（編集不可）。role 無しユーザー → ログイン拒否。

---

## Task 4: Argo Workflows SSO RBAC

**Files:**
- Modify: `manifests/platform/argo-workflows/app/base/values-workflows.yaml`
- Create: `manifests/platform/argo-workflows/app/base/rbac-sso.yaml`
- Modify: `manifests/platform/argo-workflows/app/base/kustomization.yaml`

- [ ] **Step 1: sso.rbac を有効化し roles claim を group として使う**

`values-workflows.yaml` の `sso` ブロックを編集。`sso.rbac.enabled` を `true` にし、`customGroupClaimName: roles` と `scopes` を調整:

変更前:

```yaml
    rbac:
      # 単一オペレーター運用なので RBAC は無効化、認証された全ユーザに
      # default SA (argo-server) の admin 権限を付与する。
      # default の rbac.enabled: true だと SSO 認証しても ServiceAccount
      # に紐付かず無権限になる。
      enabled: false
```

変更後:

```yaml
    # roles claim を argo の "groups" として読み、rbac-rule で SA にマップ。
    customGroupClaimName: roles
    rbac:
      enabled: true
```

> `customGroupClaimName: roles` により argo は `roles` claim を groups 扱いにする。rbac-rule（次 Step の SA annotation）が `'admin' in groups` 等で評価される。

- [ ] **Step 2: rbac-sso.yaml を作成（role→SA）**

argo の SSO RBAC は、`workflows.argoproj.io/rbac-rule` annotation を持つ ServiceAccount にユーザーをマップする。admin/edit/read の 3 SA を作り、それぞれ argo の ClusterRole（`argo-server-cluster-template` 相当ではなく、ここでは標準の admin/edit/view 権限を RoleBinding で付与）に束ねる。

```yaml
---
# Zitadel role → argo ServiceAccount マッピング（SSO RBAC）。
# rbac-rule は argo が customGroupClaimName(roles) を groups として評価する。
# precedence が高い（数字が大きい）ルールが優先。
apiVersion: v1
kind: ServiceAccount
metadata:
  name: argo-admin
  namespace: argo-workflows
  annotations:
    workflows.argoproj.io/rbac-rule: "'admin' in groups"
    workflows.argoproj.io/rbac-rule-precedence: "3"
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: argo-edit
  namespace: argo-workflows
  annotations:
    workflows.argoproj.io/rbac-rule: "'maintainer' in groups || 'developer' in groups"
    workflows.argoproj.io/rbac-rule-precedence: "2"
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: argo-view
  namespace: argo-workflows
  annotations:
    workflows.argoproj.io/rbac-rule: "'viewer' in groups"
    workflows.argoproj.io/rbac-rule-precedence: "1"
---
# admin: workflow の full 管理
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: argo-admin
  namespace: argo-workflows
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: argo-workflows-admin
subjects:
  - kind: ServiceAccount
    name: argo-admin
    namespace: argo-workflows
---
# edit: workflow の submit/編集
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: argo-edit
  namespace: argo-workflows
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: argo-workflows-edit
subjects:
  - kind: ServiceAccount
    name: argo-edit
    namespace: argo-workflows
---
# view: 読み取り専用
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: argo-view
  namespace: argo-workflows
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: argo-workflows-view
subjects:
  - kind: ServiceAccount
    name: argo-view
    namespace: argo-workflows
```

> ClusterRole `argo-workflows-admin` / `-edit` / `-view` は argo-workflows chart が `server.rbac` または同梱で生成する標準 ClusterRole。**Step 4 の検証で実在を確認**し、名前が異なる場合は `kubectl get clusterrole | grep argo` で実名に合わせる。無ければ最小権限の ClusterRole を本ファイルに追加する。

- [ ] **Step 2.5: kustomization.yaml に rbac-sso.yaml を追加**

`manifests/platform/argo-workflows/app/base/kustomization.yaml` の `resources:` リストに `- rbac-sso.yaml` を追記する。

- [ ] **Step 3: kustomize build 検証**

Run: `kubectl kustomize manifests/platform/argo-workflows/app/base >/dev/null && echo OK`
Expected: `OK`。

- [ ] **Step 4: argo ClusterRole 実名の確認**

Run: `kubectl get clusterrole | grep -i argo`
Expected: `argo-workflows-admin` / `argo-workflows-edit` / `argo-workflows-view` 等が存在。**異なれば rbac-sso.yaml の `roleRef.name` を実名に修正**して再 build。

- [ ] **Step 5: コミット**

```bash
git add manifests/platform/argo-workflows/app/base/values-workflows.yaml \
        manifests/platform/argo-workflows/app/base/rbac-sso.yaml \
        manifests/platform/argo-workflows/app/base/kustomization.yaml
git commit -m "feat(argo): enable SSO RBAC mapping Zitadel roles to argo SAs"
```

- [ ] **Step 6: merge 後の実機確認**

admin で argo UI にログイン → workflow の submit/削除ができる。viewer → 読み取りのみ。

---

## Task 5: Flux Web の role→k8s RBAC

**Files:**
- Modify: `manifests/platform/flux-operator/web/base/config.yaml`
- Modify: `manifests/platform/flux-operator/web/base/rbac.yaml`

- [ ] **Step 1: impersonation.groups を roles claim から取る**

`config.yaml` の impersonation 設定を変更:

変更前:

```yaml
        # Zitadel は groups を返さないので空スライスにフォールバックする。
        groups: "has(claims.groups) ? claims.groups : []"
```

変更後:

```yaml
        # Zitadel Action が注入する roles claim を k8s group として impersonate。
        # k8s RBAC は Group("admin"/"maintainer"/... ) に対して認可する。
        groups: "has(claims.roles) ? claims.roles : []"
```

（`username: "claims.email"` は変更不要。）

- [ ] **Step 2: rbac.yaml を email バインドから Group バインドへ**

現行は kukv の email に `flux-web-admin` ClusterRole を束ねている。これを role(group) ベースの ClusterRoleBinding に置換する。`flux-web-admin` は chart が `web.rbac.createRoles` で生成するフルアクセス ClusterRole（既存コメント参照）。読み取り用には k8s 標準の `view` ClusterRole を使う。

`rbac.yaml` を以下で置換（既存の email subject ブロックを削除し、Group subject に）:

```yaml
---
# Zitadel role(group) → k8s RBAC。flux-web は claims.roles を group として
# impersonate するので、Group 名 = role_key。
#   admin / maintainer → flux-web-admin（Flux リソース full）
#   developer          → flux-web-admin（sync 可。粒度を分けるなら別 ClusterRole）
#   viewer             → view（読み取り）
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: flux-web-admin
roleRef:
  kind: ClusterRole
  name: flux-web-admin
  apiGroup: rbac.authorization.k8s.io
subjects:
  - kind: Group
    name: admin
    apiGroup: rbac.authorization.k8s.io
  - kind: Group
    name: maintainer
    apiGroup: rbac.authorization.k8s.io
  - kind: Group
    name: developer
    apiGroup: rbac.authorization.k8s.io
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: flux-web-view
roleRef:
  kind: ClusterRole
  name: view
  apiGroup: rbac.authorization.k8s.io
subjects:
  - kind: Group
    name: viewer
    apiGroup: rbac.authorization.k8s.io
```

> developer を admin と分けたい場合は別 ClusterRole（sync/reconcile のみ許可）を切るが、まずは admin 系に寄せる（マトリクスの「Flux Web: developer=edit」は flux-web-admin で満たす。viewer のみ読み取り限定）。

- [ ] **Step 3: kustomize build 検証**

Run: `kubectl kustomize manifests/platform/flux-operator/web/base >/dev/null && echo OK`
Expected: `OK`。

- [ ] **Step 4: コミット**

```bash
git add manifests/platform/flux-operator/web/base/config.yaml \
        manifests/platform/flux-operator/web/base/rbac.yaml
git commit -m "feat(flux-web): authorize by Zitadel role via group impersonation + k8s RBAC"
```

- [ ] **Step 5: merge 後の実機確認**

admin（kukv）で flux.b8m.app にログイン → Flux リソースを操作できる。viewer → 読み取りのみ。

---

## Task 6: マトリクス全体の E2E 検証

**Files:** なし（検証のみ）

- [ ] **Step 1: 検証用ユーザーに各ロールを順に付与してマトリクスを総当たり**

プラン 2 Task 8 の検証ユーザー（br-apps ではなく br-dev に作る検証用 human_user）に、zitadel-terraform で `viewer` → `developer` → `maintainer` の grant を順に切替え（または 4 ユーザー用意）、各アプリの到達/権限がマトリクス通りか確認する:

| ロール | longhorn | alertmanager | hubble | prometheus | grafana | argo | flux |
|---|---|---|---|---|---|---|---|
| admin | 可 | 可 | 可 | 可 | Admin | admin | 可 |
| maintainer | 可 | 可 | 可 | 可 | Editor | edit | 可 |
| developer | 403 | 403 | 可 | 可 | Editor | edit | 可 |
| viewer | 403 | 403 | 403 | 可 | Viewer | read | 読取 |

Expected: 全セルが表通り。ズレたら該当アプリの authorization/role マッピングを修正。

- [ ] **Step 2: 検証用ユーザーの後始末**

検証が済んだら zitadel-terraform から検証用ユーザー/grant を削除（恒久運用に不要）。

- [ ] **Step 3: ドキュメント更新**

[`docs/platform/identity.md`](../platform/identity.md) を実態に合わせて更新（spec「ドキュメント修正」節）:
- SMTP は Resend 稼働中（「未設定」記述を訂正）
- Zitadel が role 認可を担う（identity のみ → 認可層へ役割変更）
- org 構造を br-dev / br-apps / ZITADEL の 3 org に
- SecurityPolicy に jwt+authorization パターンを追記

```bash
git add docs/platform/identity.md
git commit -m "docs(identity): reflect multi-org RBAC, role enforcement, and active SMTP"
```

---

## Self-Review（このプランと spec の突き合わせ）

- **spec「Envoy 系を jwt+authorization で全アプリ強制」**: Task 1（spike/longhorn）＋ Task 2（alertmanager/prometheus/hubble）。✅
- **spec 認可マトリクス**: Task 1–5 の各 authorization.values / role マッピングが [マトリクス](./zitadel-multi-org-rbac.md#ロール--アプリ-認可マトリクス)と一致。Task 6 で総当たり検証。✅
- **spec「ネイティブ系は role→内部 RBAC」**: Grafana（Task 3）/ Argo（Task 4）/ Flux（Task 5）。✅
- **spec「Action のフラット roles claim を共通消費」**: Envoy（StringArray claim）/ Grafana（JMESPath）/ Argo（customGroupClaimName）/ Flux（claims.roles）が全て `roles` claim を参照。✅
- **spec「ドキュメント修正」**: Task 6 Step 3。✅
- **placeholder スキャン**: TBD/TODO なし。spike の失敗時手順・ClusterRole 実名確認など、不確実箇所は「検証ステップ＋調整指示」で具体化（プレースホルダではない）。✅
- **型・参照整合**: cookie 名（`<app>-access-token`）と `extractFrom.cookies` が各アプリで一致。jwt provider 名 `zitadel` を authorization.principal.jwt.provider が参照。`roles` claim 名が全アプリで統一。✅

---

## 3 プラン完了後の最終状態

- `auth.b8m.app` のログイン画面は公開到達可、admin console は GitHub org + WARP 保護（プラン 1）。
- Zitadel は br-dev / br-apps の 2 org。platform は has_project_check で grant 必須、トークンにフラット `roles` claim（プラン 2）。
- 全 platform アプリが `roles` claim で認可マトリクスを強制（プラン 3）。
- 個人開発アプリは CF Access map に載せなければ公開。SSO する場合は br-apps org に OIDC app を足し、利用者を TF プロビジョン（本 3 プランの外、既存パターンの再利用）。
