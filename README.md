# br-cluster

Raspberry Pi Kubernetes クラスタの構築・運用を管理するモノレポ。

## ディレクトリ構成

| ディレクトリ / ファイル | 概要 |
|---|---|
| `cli/` | 統合 CLI (`cluster-forge`) — ソースコード + テスト |
| `imager/` | Packer HCL (OS イメージ定義) |
| `provisioner/` | Ansible (ノードプロビジョニング) |
| `manifests/` | Kubernetes マニフェスト (Flux GitOps) |
| `servers.yaml` | サーバー定義 |
| `compose.yaml` | Docker Compose (1Password Connect + ansible-runner) |

## セットアップ

```shell
# Python 依存関係のインストール
uv sync
```

### 事前準備

`.secret/` に 1Password Connect の credentials を配置:

```
.secret/
├── dev/
│   ├── 1password-credentials.json
│   └── .connect_token
└── prod/
    ├── 1password-credentials.json
    └── .connect_token
```

## 使い方

```shell
# コンテナ起動 (1Password Connect + Ansible Runner)
uv run cluster-forge bootstrap --env dev

# OS イメージビルド
uv run cluster-forge generate-config --env dev
uv run cluster-forge build-image --env dev

# プロビジョニング
uv run cluster-forge provision run --env dev setup-node
uv run cluster-forge provision ping --env dev

# クリーンアップ
uv run cluster-forge clean --env dev
```
