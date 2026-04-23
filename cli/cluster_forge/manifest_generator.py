from __future__ import annotations

from pathlib import Path

import yaml

from cluster_forge.models import Inventory, K8sRole, ServerDefinition, ServerType
from cluster_forge.secrets import SecretProvider


def _master_servers(inventory: Inventory) -> list[ServerDefinition]:
    """Return servers running the K3s embedded etcd (primary + secondary)."""
    return [
        s
        for s in inventory.servers
        if s.type == ServerType.NODE
        and s.k8s_role in (K8sRole.PRIMARY, K8sRole.SECONDARY)
    ]


def generate_k3s_etcd_endpoints(
    inventory: Inventory,
    env: str,
    provider: SecretProvider,
) -> dict:
    """Generate the kubeEtcd.endpoints block for kube-prometheus-stack.

    K3s embedded etcd runs on all master (primary + secondary) nodes and
    exposes metrics on :2381 via `etcd-expose-metrics: true`
    (provisioner/roles/k3s/templates/config.yaml.master.j2).
    """
    endpoints = [
        provider.get_inventory_secrets(env, s.name).ip_address
        for s in _master_servers(inventory)
    ]
    return {"kubeEtcd": {"endpoints": endpoints}}


_HEADER_TEMPLATE = """\
# Managed by `cluster-forge generate-manifests --env {env}` — do not edit by hand.
# Source of truth: servers.yaml (k8s_role = primary|secondary) + 1Password IPs.
# K3s embedded etcd exposes metrics on :2381 via `etcd-expose-metrics: true`
# (provisioner/roles/k3s/templates/config.yaml.master.j2).
"""


def _etcd_endpoints_path(repo_root: Path, env: str) -> Path:
    return (
        repo_root
        / "manifests"
        / "platform"
        / "kube-prometheus-stack"
        / "app"
        / "overlays"
        / env
        / "etcd-endpoints.yaml"
    )


def write_k3s_etcd_endpoints(
    inventory: Inventory,
    env: str,
    provider: SecretProvider,
    repo_root: Path,
) -> Path:
    """Write the generated kubeEtcd endpoints values file into the env overlay."""
    payload = generate_k3s_etcd_endpoints(inventory, env, provider)
    target = _etcd_endpoints_path(repo_root, env)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(payload, default_flow_style=False, sort_keys=False)
    target.write_text(_HEADER_TEMPLATE.format(env=env) + body)
    return target


def write_manifests(
    inventory: Inventory,
    env: str,
    provider: SecretProvider,
    repo_root: Path,
) -> list[Path]:
    """Generate all env-specific manifest fragments from inventory + secrets."""
    return [write_k3s_etcd_endpoints(inventory, env, provider, repo_root)]
