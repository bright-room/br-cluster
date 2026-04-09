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

    node = next(s for s in inventory.servers if s.name == "br-node1")
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
