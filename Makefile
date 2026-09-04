.PHONY: help \
        lint format test \
        yaml/lint actions/lint ansible/lint \
        packer/fmt \
        manifests/build manifests/flux-local manifests/substitute-check \
        policy/test policy/verify \
        check

# === Help ===

help:
	@awk 'BEGIN{FS=":.*?## "} /^[a-zA-Z0-9_\/.-]+:.*?## / {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# === Development (Python) ===

lint: ## ruff check + format check
	uv run ruff check .
	uv run ruff format --check .

format: ## ruff format (write)
	uv run ruff format .

test: ## pytest with coverage
	uv run pytest -v --cov=cluster_forge --cov-report=term-missing

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

manifests/build: ## kustomize build + kubeconform (strict, CRD schemas via datreeio/CRDs-catalog)
	mise exec -- ./scripts/manifests-build.sh

manifests/flux-local: ## flux-local test (HelmRelease offline render)
	uv run flux-local test --enable-helm --path manifests/clusters/prod -v

manifests/substitute-check: ## Flux Kustomization の substituteFrom 参照先 Secret/CM の存在チェック
	mise exec -- ./scripts/manifests-substitute-check.sh

policy/verify: ## Rego ポリシーの単体テスト
	mise exec -- conftest verify --policy policies/

policy/test: policy/verify ## manifests/platform/ をポリシーで検査
	mise exec -- conftest test --combine --policy policies/ manifests/platform/

# === Aggregate ===

check: lint test yaml/lint actions/lint ansible/lint packer/fmt manifests/build manifests/substitute-check manifests/flux-local policy/test ## CI 等価チェック一式

# === Cluster Operations ===

dev/bootstrap:
	uv run cluster-forge bootstrap --env dev

dev/clean:
	uv run cluster-forge clean --env dev

dev/clean-all:
	uv run cluster-forge clean --env dev --all

prod/bootstrap:
	uv run cluster-forge bootstrap --env prod

prod/clean:
	uv run cluster-forge clean --env prod

prod/clean-all:
	uv run cluster-forge clean --env prod --all

dev/generate-config:
	uv run cluster-forge generate-config --env dev

prod/generate-config:
	uv run cluster-forge generate-config --env prod

dev/build-image:
	uv run cluster-forge build-image --env dev

prod/build-image:
	uv run cluster-forge build-image --env prod

dev/image-build/%:
	uv run cluster-forge build-image --env dev --server $*

prod/image-build/%:
	uv run cluster-forge build-image --env prod --server $*

# === Inventory Generation ===

dev/generate-inventory:
	uv run cluster-forge generate-inventory --env dev

prod/generate-inventory:
	uv run cluster-forge generate-inventory --env prod

# === Provisioning ===
# Usage: make dev/provision/setup-node, make prod/provision/k3s-start, etc.

PLAYBOOKS := setup-node setup-gateway setup-standalone setup-monitoring-agent bootstrap-cluster k3s-start k3s-stop k3s-reset setup-k3s-leader-restart shutdown-cluster

$(foreach pb,$(PLAYBOOKS),$(eval dev/provision/$(pb):; uv run cluster-forge provision run --env dev $(pb)))
$(foreach pb,$(PLAYBOOKS),$(eval prod/provision/$(pb):; uv run cluster-forge provision run --env prod $(pb)))

dev/provision/ping:
	uv run cluster-forge provision ping --env dev

prod/provision/ping:
	uv run cluster-forge provision ping --env prod

dev/provision/lint:
	uv run cluster-forge provision lint --env dev

prod/provision/lint:
	uv run cluster-forge provision lint --env prod
