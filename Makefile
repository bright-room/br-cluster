.PHONY: lint format test packer-validate check bootstrap clean clean-all

# === Development ===

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

test:
	uv run pytest -v

packer-validate:
	packer fmt -check imager/

check: lint test packer-validate

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

PLAYBOOKS := setup-node setup-gateway setup-backup setup-monitoring-agent bootstrap-cluster k3s-start k3s-stop k3s-reset

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
