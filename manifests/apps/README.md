# manifests/apps/

認証**不要**なワークロード (単発のツール、ローカルなダッシュボード、PoC、遊び) の置き場。

## manifests/platform/ との使い分け

| 条件 | 配置先 |
|---|---|
| クラスタの基盤 (gateway, observability, IdP, ストレージ) | `manifests/platform/` |
| ログインユーザー単位の identity を必要とするアプリ | `manifests/platform/` (既存 app と同じパターンで OIDC gating を付けるため) |
| 社外公開する一次成果物 | `manifests/platform/` 扱い (CF Access + Zitadel OIDC 経由で保護) |
| 上記に当てはまらない tenant ワークロード | **ここ (`manifests/apps/`)** |

基盤コンポーネントと tenant ワークロードを区別することで、`platform/` 側は安定した状態を保ち、`apps/` 側は壊れても基盤ごとは巻き込まないレイヤリングを目指す。

## 追加の流れ (初回)

最初の app が入るタイミングで:

1. 本ディレクトリ配下に `<app-name>/base/` を切り、`namespace.yaml` + `kustomization.yaml` を配置
2. 新規 Flux Kustomization CR (`<app-name>-app.yaml`) を `manifests/clusters/prod/apps/` に追加 (`apps/` サブディレクトリを新設)
3. `manifests/clusters/prod/apps/kustomization.yaml` のトップレベル resources に新 CR を登録

OIDC 保護が必要な app を追加する場合は、ここではなく `manifests/platform/` の既存 app をテンプレートに使い、`docs/architecture.md` の「新しい OIDC 保護アプリを追加する手順」を参照する。
