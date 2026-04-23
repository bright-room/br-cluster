from __future__ import annotations

from pathlib import Path

import yaml

from cluster_forge.manifest_generator import (
    generate_k3s_etcd_endpoints,
    write_k3s_etcd_endpoints,
    write_manifests,
)
from cluster_forge.models import Inventory, K8sRole, ServerDefinition, ServerType
from cluster_forge.secrets import MockSecretProvider


class TestGenerateK3sEtcdEndpoints:
    def test_includes_primary_and_secondary(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_k3s_etcd_endpoints(full_inventory, "dev", provider)
        endpoints = result["kubeEtcd"]["endpoints"]
        # br-node1 (primary) + br-node2/3 (secondary) — but NOT workers
        assert endpoints == ["192.0.2.10", "192.0.2.11", "192.0.2.12"]

    def test_excludes_workers(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_k3s_etcd_endpoints(full_inventory, "dev", provider)
        endpoints = result["kubeEtcd"]["endpoints"]
        worker_ips = {"192.0.2.13", "192.0.2.14", "192.0.2.15"}
        assert worker_ips.isdisjoint(endpoints)

    def test_excludes_non_node_servers(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_k3s_etcd_endpoints(full_inventory, "dev", provider)
        endpoints = result["kubeEtcd"]["endpoints"]
        # gateway and external must not appear
        assert "192.0.2.1" not in endpoints
        assert "198.51.100.50" not in endpoints

    def test_empty_when_no_master_nodes(self) -> None:
        inv = Inventory(
            environments=["dev"],
            servers=[
                ServerDefinition(
                    name="br-node4", type=ServerType.NODE, k8s_role=K8sRole.WORKER
                ),
            ],
        )
        provider = MockSecretProvider()
        result = generate_k3s_etcd_endpoints(inv, "dev", provider)
        assert result == {"kubeEtcd": {"endpoints": []}}


class TestWriteK3sEtcdEndpoints:
    def test_writes_to_expected_overlay_path(
        self, full_inventory: Inventory, tmp_path: Path
    ) -> None:
        provider = MockSecretProvider()
        path = write_k3s_etcd_endpoints(full_inventory, "prod", provider, tmp_path)
        expected = (
            tmp_path
            / "manifests"
            / "platform"
            / "kube-prometheus-stack"
            / "app"
            / "overlays"
            / "prod"
            / "etcd-endpoints.yaml"
        )
        assert path == expected
        assert path.exists()

    def test_file_has_generated_header(
        self, full_inventory: Inventory, tmp_path: Path
    ) -> None:
        provider = MockSecretProvider()
        path = write_k3s_etcd_endpoints(full_inventory, "prod", provider, tmp_path)
        content = path.read_text()
        assert "do not edit by hand" in content
        assert "cluster-forge generate-manifests --env prod" in content

    def test_file_is_valid_yaml(
        self, full_inventory: Inventory, tmp_path: Path
    ) -> None:
        provider = MockSecretProvider()
        path = write_k3s_etcd_endpoints(full_inventory, "prod", provider, tmp_path)
        parsed = yaml.safe_load(path.read_text())
        assert parsed["kubeEtcd"]["endpoints"] == [
            "192.0.2.10",
            "192.0.2.11",
            "192.0.2.12",
        ]


class TestWriteManifests:
    def test_returns_all_generated_paths(
        self, full_inventory: Inventory, tmp_path: Path
    ) -> None:
        provider = MockSecretProvider()
        files = write_manifests(full_inventory, "prod", provider, tmp_path)
        assert len(files) == 1
        assert files[0].name == "etcd-endpoints.yaml"
