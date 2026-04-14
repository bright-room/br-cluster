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
        name="br-node1", type=ServerType.NODE, k8s_role=K8sRole.PRIMARY
    )


@pytest.fixture
def worker_node_server() -> ServerDefinition:
    return ServerDefinition(
        name="br-node4", type=ServerType.NODE, k8s_role=K8sRole.WORKER
    )


@pytest.fixture
def sample_inventory() -> Inventory:
    return Inventory(
        environments=["dev", "prod"],
        servers=[
            ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY),
            ServerDefinition(
                name="br-node1", type=ServerType.NODE, k8s_role=K8sRole.PRIMARY
            ),
            ServerDefinition(
                name="br-node7", type=ServerType.NODE, k8s_role=K8sRole.WORKER
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
                name="br-node7", type=ServerType.NODE, k8s_role=K8sRole.WORKER
            ),
            ServerDefinition(
                name="br-node1", type=ServerType.NODE, k8s_role=K8sRole.PRIMARY
            ),
            ServerDefinition(
                name="br-node2", type=ServerType.NODE, k8s_role=K8sRole.SECONDARY
            ),
            ServerDefinition(
                name="br-node3", type=ServerType.NODE, k8s_role=K8sRole.SECONDARY
            ),
            ServerDefinition(
                name="br-node4", type=ServerType.NODE, k8s_role=K8sRole.WORKER
            ),
            ServerDefinition(
                name="br-node5", type=ServerType.NODE, k8s_role=K8sRole.WORKER
            ),
            ServerDefinition(
                name="br-node6", type=ServerType.NODE, k8s_role=K8sRole.WORKER
            ),
        ],
    )


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    return tmp_path / "output"
