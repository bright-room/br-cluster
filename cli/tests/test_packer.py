from pathlib import Path

from cluster_forge.models import ServerDefinition, ServerType
from cluster_forge.packer import generate_pkrvars


class TestGeneratePkrvars:
    def test_gateway_includes_network_config(self, tmp_path: Path) -> None:
        server = ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY)
        result = generate_pkrvars(server, "dev", tmp_path)
        assert 'hostname = "br-gateway1"' in result
        assert "user-data" in result
        assert "network-config" in result

    def test_node_excludes_network_config(self, tmp_path: Path) -> None:
        server = ServerDefinition(name="br-cluster1", type=ServerType.NODE)
        result = generate_pkrvars(server, "dev", tmp_path)
        assert 'hostname = "br-cluster1"' in result
        assert "user-data" in result
        assert "network-config" not in result

    def test_external_excludes_network_config(self, tmp_path: Path) -> None:
        server = ServerDefinition(name="br-storage1", type=ServerType.STANDALONE)
        result = generate_pkrvars(server, "dev", tmp_path)
        assert "user-data" in result
        assert "network-config" not in result

    def test_valid_hcl_format(self, tmp_path: Path) -> None:
        server = ServerDefinition(name="br-cluster1", type=ServerType.NODE)
        result = generate_pkrvars(server, "dev", tmp_path)
        assert "cloud_config_files = [" in result
        assert result.endswith("]\n")
