from pathlib import Path

import yaml

from cluster_forge.config_generator import (
    generate_config,
    hash_password,
    hash_wpa_passphrase,
    render_network_config,
    render_user_data,
)
from cluster_forge.models import ServerDefinition
from cluster_forge.secrets import MockSecretProvider, NetworkSecrets


class TestHashPassword:
    def test_produces_sha512_format(self) -> None:
        result = hash_password("test-password")
        assert result.startswith("$6$")

    def test_random_salt_produces_unique_hashes(self) -> None:
        a = hash_password("password")
        b = hash_password("password")
        assert a != b

    def test_different_passwords_differ(self) -> None:
        a = hash_password("password1")
        b = hash_password("password2")
        assert a != b


class TestHashWpaPassphrase:
    def test_produces_64_hex_chars(self) -> None:
        result = hash_wpa_passphrase("TestSSID", "TestPassword")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self) -> None:
        a = hash_wpa_passphrase("SSID", "pass")
        b = hash_wpa_passphrase("SSID", "pass")
        assert a == b


class TestRenderUserData:
    def test_gateway_includes_runcmd(self, gateway_server: ServerDefinition) -> None:
        secrets = MockSecretProvider().get_server_secrets("dev", gateway_server.name)
        result = render_user_data(gateway_server, secrets)
        assert "runcmd:" in result
        assert "REGDOMAIN=JP" in result

    def test_node_excludes_runcmd(self, node_server: ServerDefinition) -> None:
        secrets = MockSecretProvider().get_server_secrets("dev", node_server.name)
        result = render_user_data(node_server, secrets)
        assert "runcmd:" not in result

    def test_contains_cloud_config_header(self, node_server: ServerDefinition) -> None:
        secrets = MockSecretProvider().get_server_secrets("dev", node_server.name)
        result = render_user_data(node_server, secrets)
        assert result.startswith("#cloud-config")

    def test_contains_operator_pubkey(self, node_server: ServerDefinition) -> None:
        secrets = MockSecretProvider().get_server_secrets("dev", node_server.name)
        result = render_user_data(node_server, secrets)
        assert "ssh-ed25519" in result

    def test_passwords_are_hashed(self, node_server: ServerDefinition) -> None:
        secrets = MockSecretProvider().get_server_secrets("dev", node_server.name)
        result = render_user_data(node_server, secrets)
        assert "test-root-password" not in result
        assert "$6$" in result

    def test_external_disables_root_autoexpand(
        self, standalone_server: ServerDefinition
    ) -> None:
        secrets = MockSecretProvider().get_server_secrets("dev", standalone_server.name)
        result = render_user_data(standalone_server, secrets)
        assert "mode: off" in result
        assert "resize_rootfs: false" in result

    def test_worker_node_disables_root_autoexpand(
        self, worker_node_server: ServerDefinition
    ) -> None:
        secrets = MockSecretProvider().get_server_secrets(
            "dev", worker_node_server.name
        )
        result = render_user_data(worker_node_server, secrets)
        assert "mode: off" in result
        assert "resize_rootfs: false" in result

    def test_primary_node_keeps_root_autoexpand(
        self, node_server: ServerDefinition
    ) -> None:
        secrets = MockSecretProvider().get_server_secrets("dev", node_server.name)
        result = render_user_data(node_server, secrets)
        assert "resize_rootfs" not in result

    def test_gateway_keeps_root_autoexpand(
        self, gateway_server: ServerDefinition
    ) -> None:
        secrets = MockSecretProvider().get_server_secrets("dev", gateway_server.name)
        result = render_user_data(gateway_server, secrets)
        assert "resize_rootfs" not in result


class TestRenderNetworkConfig:
    def test_contains_static_ip(self) -> None:
        secrets = NetworkSecrets(
            internal_ip="192.0.2.100",
            external_ip="198.51.100.50",
            gateway_ip="198.51.100.1",
            ssid="MyWiFi",
            wifi_password="secret",
        )
        result = render_network_config(secrets)
        assert "192.0.2.100/24" in result
        assert "198.51.100.50/24" in result
        assert "198.51.100.1" in result

    def test_wifi_password_is_hashed(self) -> None:
        secrets = NetworkSecrets(
            internal_ip="192.0.2.100",
            external_ip="198.51.100.50",
            gateway_ip="198.51.100.1",
            ssid="MyWiFi",
            wifi_password="secret",
        )
        result = render_network_config(secrets)
        assert "secret" not in result


class TestGenerateConfig:
    def test_gateway_generates_two_files(
        self,
        gateway_server: ServerDefinition,
        mock_provider: MockSecretProvider,
        tmp_output: Path,
    ) -> None:
        files = generate_config(gateway_server, "dev", mock_provider, tmp_output)
        assert len(files) == 2
        assert any("user-data" in str(f) for f in files)
        assert any("network-config" in str(f) for f in files)

    def test_node_generates_one_file(
        self,
        node_server: ServerDefinition,
        mock_provider: MockSecretProvider,
        tmp_output: Path,
    ) -> None:
        files = generate_config(node_server, "dev", mock_provider, tmp_output)
        assert len(files) == 1
        assert "user-data" in str(files[0])

    def test_output_directory_structure(
        self,
        node_server: ServerDefinition,
        mock_provider: MockSecretProvider,
        tmp_output: Path,
    ) -> None:
        generate_config(node_server, "dev", mock_provider, tmp_output)
        assert (tmp_output / "dev" / "br-cluster1" / "user-data").exists()

    def test_user_data_is_valid_yaml(
        self,
        node_server: ServerDefinition,
        mock_provider: MockSecretProvider,
        tmp_output: Path,
    ) -> None:
        generate_config(node_server, "dev", mock_provider, tmp_output)
        content = (tmp_output / "dev" / "br-cluster1" / "user-data").read_text()
        parsed = yaml.safe_load(content)
        assert parsed["hostname"] == "br-cluster1"
        assert parsed["timezone"] == "JST"
