# CLAUDE.md

## プロジェクト概要

Raspberry Pi Kubernetes クラスタの構築・運用を管理するモノレポ。
統合 CLI `cluster-forge` で全操作を実行する。

## ディレクトリ構成

```
cli/
  cluster_forge/         # Python CLI パッケージ
  tests/                 # pytest テスト
imager/                  # Packer HCL (OS イメージ定義)
provisioner/             # Ansible (ノードプロビジョニング)
manifests/               # Kubernetes マニフェスト (Flux GitOps)
servers.yaml             # サーバー定義の唯一の情報源
compose.yaml             # Docker Compose (1Password Connect + ansible-runner)
```

## コマンド

```shell
# セットアップ
uv sync

# チェック一括実行（CI と同等）
make check

# 個別
make lint              # ruff check + format check
make format            # ruff format 適用
make test              # pytest
make packer-validate   # packer fmt check

# 1Password Connect + Ansible Runner 起動
uv run cluster-forge bootstrap --env dev

# OS イメージビルド
uv run cluster-forge generate-config --env dev [--server br-node1]
uv run cluster-forge build-image --env dev [--server br-node1] [--skip-generate]

# プロビジョニング
uv run cluster-forge provision run --env dev setup-node
uv run cluster-forge provision ping --env dev
uv run cluster-forge provision lint --env dev
uv run cluster-forge provision setup --env dev

# クリーンアップ
uv run cluster-forge clean --env dev
```

## 技術スタック

- Python 3.12+ / uv / Click / Pydantic / Jinja2 / passlib
- Packer (packer-builder-arm) で ARM64 イメージビルド
- Ansible でノードプロビジョニング
- 1Password Connect でシークレット管理
- Flux GitOps で Kubernetes リソース管理
- ruff (lint + format) / pytest

## コーディング規約

- ruff ルール: E, F, I, UP, B, SIM（pyproject.toml で定義）
- 行長制限: 88文字（ruff デフォルト）
- `from __future__ import annotations` を使用
- テストは `MockSecretProvider` を使い、外部依存なしで実行可能にする
- サーバー追加は `servers.yaml` のみ変更（他ファイルの変更不要）
