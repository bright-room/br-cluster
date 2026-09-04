from pathlib import Path

import pytest

from cluster_forge.inventory import load_inventory
from cluster_forge.models import ServerType


def test_load_inventory_from_project_root() -> None:
    inventory = load_inventory()
    assert "dev" in inventory.environments
    assert "prod" in inventory.environments
    assert len(inventory.servers) == 8

    gateway = next(s for s in inventory.servers if s.name == "br-gateway1")
    assert gateway.type == ServerType.GATEWAY

    node = next(s for s in inventory.servers if s.name == "br-cluster1")
    assert node.type == ServerType.NODE
    assert node.k8s_role is not None


def test_load_inventory_from_custom_path(tmp_path: Path) -> None:
    yaml_content = """
environments:
  - test
servers:
  - name: test-gw
    type: gateway
  - name: test-node
    type: node
    k8s_role: primary
"""
    path = tmp_path / "servers.yaml"
    path.write_text(yaml_content)

    inventory = load_inventory(path)
    assert inventory.environments == ["test"]
    assert len(inventory.servers) == 2


def test_load_inventory_invalid_type(tmp_path: Path) -> None:
    yaml_content = """
environments:
  - test
servers:
  - name: bad-server
    type: unknown
"""
    path = tmp_path / "servers.yaml"
    path.write_text(yaml_content)

    with pytest.raises(ValueError):
        load_inventory(path)


class TestStandaloneServerType:
    def test_standalone_type_exists(self) -> None:
        from cluster_forge.models import ServerType

        assert ServerType.STANDALONE == "standalone"

    def test_external_type_removed(self) -> None:
        from cluster_forge.models import ServerType

        assert not hasattr(ServerType, "EXTERNAL")

    def test_services_defaults_to_empty_list(self) -> None:
        from cluster_forge.models import ServerDefinition, ServerType

        server = ServerDefinition(name="br-ai1", type=ServerType.STANDALONE)
        assert server.services == []

    def test_services_accepts_list(self) -> None:
        from cluster_forge.models import ServerDefinition, ServerType

        server = ServerDefinition(
            name="br-storage1",
            type=ServerType.STANDALONE,
            services=["garage", "caddy", "certbot"],
        )
        assert server.services == ["garage", "caddy", "certbot"]

    def test_standalone_does_not_need_network_config(self) -> None:
        from cluster_forge.models import ServerDefinition, ServerType

        server = ServerDefinition(name="br-db1", type=ServerType.STANDALONE)
        assert server.needs_network_config is False
