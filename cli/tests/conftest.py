from pathlib import Path

import pytest

from cluster_forge.models import Inventory, ServerDefinition, ServerType
from cluster_forge.secrets import MockSecretProvider


@pytest.fixture
def mock_provider() -> MockSecretProvider:
    return MockSecretProvider()


@pytest.fixture
def gateway_server() -> ServerDefinition:
    return ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY)


@pytest.fixture
def node_server() -> ServerDefinition:
    return ServerDefinition(name="br-node1", type=ServerType.NODE)


@pytest.fixture
def external_server() -> ServerDefinition:
    return ServerDefinition(name="br-external1", type=ServerType.EXTERNAL)


@pytest.fixture
def sample_inventory() -> Inventory:
    return Inventory(
        environments=["dev", "prod"],
        servers=[
            ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY),
            ServerDefinition(name="br-node1", type=ServerType.NODE),
            ServerDefinition(name="br-external1", type=ServerType.EXTERNAL),
        ],
    )


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    return tmp_path / "output"
