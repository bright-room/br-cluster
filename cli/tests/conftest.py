from pathlib import Path

import pytest

from cluster_forge.models import Inventory, K8sRole, ServerDefinition, ServerType
from cluster_forge.secrets import MockSecretProvider


@pytest.fixture
def mock_provider() -> MockSecretProvider:
    return MockSecretProvider()


@pytest.fixture
def gateway_server() -> ServerDefinition:
    return ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY)


@pytest.fixture
def node_server() -> ServerDefinition:
    return ServerDefinition(
        name="br-cluster1", type=ServerType.NODE, k8s_role=K8sRole.PRIMARY
    )


@pytest.fixture
def standalone_server() -> ServerDefinition:
    return ServerDefinition(
        name="br-storage1",
        type=ServerType.STANDALONE,
        services=["garage", "caddy", "certbot"],
    )


@pytest.fixture
def worker_node_server() -> ServerDefinition:
    return ServerDefinition(
        name="br-cluster2", type=ServerType.NODE, k8s_role=K8sRole.WORKER
    )


@pytest.fixture
def sample_inventory() -> Inventory:
    return Inventory(
        environments=["dev", "prod"],
        servers=[
            ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY),
            ServerDefinition(
                name="br-cluster1", type=ServerType.NODE, k8s_role=K8sRole.PRIMARY
            ),
            ServerDefinition(
                name="br-storage1",
                type=ServerType.STANDALONE,
                services=["garage", "caddy", "certbot"],
            ),
        ],
    )


@pytest.fixture
def full_inventory() -> Inventory:
    return Inventory(
        environments=["dev", "prod"],
        servers=[
            ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY),
            ServerDefinition(
                name="br-db1",
                type=ServerType.STANDALONE,
                services=["postgresql", "certbot"],
            ),
            ServerDefinition(
                name="br-storage1",
                type=ServerType.STANDALONE,
                services=["garage", "caddy", "certbot"],
            ),
            ServerDefinition(name="br-observability1", type=ServerType.STANDALONE),
            ServerDefinition(name="br-ai1", type=ServerType.STANDALONE),
            ServerDefinition(
                name="br-cluster1", type=ServerType.NODE, k8s_role=K8sRole.PRIMARY
            ),
            ServerDefinition(
                name="br-cluster2", type=ServerType.NODE, k8s_role=K8sRole.WORKER
            ),
            ServerDefinition(
                name="br-cluster3", type=ServerType.NODE, k8s_role=K8sRole.WORKER
            ),
        ],
    )


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    return tmp_path / "output"
