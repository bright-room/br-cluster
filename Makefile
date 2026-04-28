.PHONY: help \
        lint format test \
        yaml/lint actions/lint ansible/lint \
        packer/fmt \
        manifests/build manifests/flux-local \
        policy/test policy/verify \
        check

# `pyproject.toml` / `uv.lock` は cli/ 配下にある。pytest / ruff は cwd=cli で
# 動かしたいので `--directory cli`、cluster-forge / flux-local はリポルートを
# cwd に保ちたいので `--project cli` を使う。
UV_RUN_CLI := uv run --project cli

# === Help ===

help:
	@awk 'BEGIN{FS=":.*?## "} /^[a-zA-Z0-9_\/.-]+:.*?## / {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# === Development (Python) ===

lint: ## ruff check + format check
	uv run --directory cli ruff check .
	uv run --directory cli ruff format --check .

format: ## ruff format (write)
	uv run --directory cli ruff format .

test: ## pytest with coverage
	uv run --directory cli pytest -v --cov=cluster_forge --cov-report=term-missing

# === YAML / Actions ===

yaml/lint: ## yamllint
	mise exec -- yamllint -c .yamllint.yaml .

actions/lint: ## actionlint (.github/workflows)
	mise exec -- actionlint -color

# === Ansible ===

ansible/lint: ## ansible-lint (env 非依存、CI と同じ呼び出し)
	cd provisioner && mise exec -- ansible-galaxy collection install -r requirements.yaml -p ./collections >/dev/null
	# `roles/ipr-cnrs.nftables` は gitignore された vendor role。CI の checkout には
	# 存在しないので exclude_paths に書いても効かないが、ローカルには bootstrap で
	# 落ちてくるので CLI で明示的に除外する。
	cd provisioner && mise exec -- ansible-lint --exclude roles/ipr-cnrs.nftables --exclude collections playbooks/

# === Packer ===

packer/fmt: ## packer fmt -check (validate は --privileged 必須なので fmt のみ)
	packer fmt -check imager/

# === Manifests / Policy ===

manifests/build: ## kustomize build + kubeconform on all cluster overlays
	mise exec -- ./scripts/manifests-build.sh

manifests/flux-local: ## flux-local test (HelmRelease offline render)
	$(UV_RUN_CLI) flux-local test --enable-helm --path manifests/clusters/prod -v

policy/verify: ## Rego ポリシーの単体テスト
	mise exec -- conftest verify --policy policies/

policy/test: policy/verify ## manifests/platform/ をポリシーで検査
	mise exec -- conftest test --combine --policy policies/ manifests/platform/

# === Aggregate ===

check: lint test yaml/lint actions/lint ansible/lint packer/fmt manifests/build manifests/flux-local policy/test ## CI 等価チェック一式

# === Cluster Operations ===

dev/bootstrap:
	$(UV_RUN_CLI) cluster-forge bootstrap --env dev

dev/clean:
	$(UV_RUN_CLI) cluster-forge clean --env dev

dev/clean-all:
	$(UV_RUN_CLI) cluster-forge clean --env dev --all

prod/bootstrap:
	$(UV_RUN_CLI) cluster-forge bootstrap --env prod

prod/clean:
	$(UV_RUN_CLI) cluster-forge clean --env prod

prod/clean-all:
	$(UV_RUN_CLI) cluster-forge clean --env prod --all

dev/generate-config:
	$(UV_RUN_CLI) cluster-forge generate-config --env dev

prod/generate-config:
	$(UV_RUN_CLI) cluster-forge generate-config --env prod

dev/build-image:
	$(UV_RUN_CLI) cluster-forge build-image --env dev

prod/build-image:
	$(UV_RUN_CLI) cluster-forge build-image --env prod

dev/image-build/%:
	$(UV_RUN_CLI) cluster-forge build-image --env dev --server $*

prod/image-build/%:
	$(UV_RUN_CLI) cluster-forge build-image --env prod --server $*

# === Inventory Generation ===

dev/generate-inventory:
	$(UV_RUN_CLI) cluster-forge generate-inventory --env dev

prod/generate-inventory:
	$(UV_RUN_CLI) cluster-forge generate-inventory --env prod

# === Provisioning ===
# Usage: make dev/provision/setup-node, make prod/provision/k3s-start, etc.

PLAYBOOKS := setup-node setup-gateway setup-external setup-monitoring-agent bootstrap-cluster k3s-start k3s-stop k3s-reset setup-k3s-leader-restart shutdown-cluster

$(foreach pb,$(PLAYBOOKS),$(eval dev/provision/$(pb):; $(UV_RUN_CLI) cluster-forge provision run --env dev $(pb)))
$(foreach pb,$(PLAYBOOKS),$(eval prod/provision/$(pb):; $(UV_RUN_CLI) cluster-forge provision run --env prod $(pb)))

dev/provision/ping:
	$(UV_RUN_CLI) cluster-forge provision ping --env dev

prod/provision/ping:
	$(UV_RUN_CLI) cluster-forge provision ping --env prod

dev/provision/lint:
	$(UV_RUN_CLI) cluster-forge provision lint --env dev

prod/provision/lint:
	$(UV_RUN_CLI) cluster-forge provision lint --env prod
