from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cluster_forge.bootstrap import (
    ServerSSHInfo,
    fetch_server_ssh_info,
    generate_ssh_config,
    write_ssh_keys,
)
from cluster_forge.models import ServerDefinition, ServerType
from cluster_forge.op_connect import ConnectClient


@pytest.fixture
def gateway_info() -> ServerSSHInfo:
    return ServerSSHInfo(
        name="br-gateway1",
        hostname="198.51.100.50",
        username="bradmin",
        public_key="ssh-ed25519 AAAAGW gateway@test",
        server_type=ServerType.GATEWAY,
    )


@pytest.fixture
def node_info() -> ServerSSHInfo:
    return ServerSSHInfo(
        name="br-cluster1",
        hostname="192.0.2.10",
        username="bradmin",
        public_key="ssh-ed25519 AAAAND node@test",
        server_type=ServerType.NODE,
    )


@pytest.fixture
def external_info() -> ServerSSHInfo:
    return ServerSSHInfo(
        name="br-storage1",
        hostname="192.0.2.16",
        username="bradmin",
        public_key="ssh-ed25519 AAAAEX external@test",
        server_type=ServerType.STANDALONE,
    )


@pytest.fixture
def all_infos(
    gateway_info: ServerSSHInfo,
    node_info: ServerSSHInfo,
    external_info: ServerSSHInfo,
) -> list[ServerSSHInfo]:
    return [gateway_info, node_info, external_info]


class TestWriteSSHKeys:
    def test_creates_key_files(
        self, tmp_path: Path, all_infos: list[ServerSSHInfo]
    ) -> None:
        write_ssh_keys(tmp_path, all_infos)
        for info in all_infos:
            key_file = tmp_path / "keys" / f"{info.name}.pub"
            assert key_file.exists()
            assert key_file.read_text() == info.public_key + "\n"

    def test_creates_keys_dir(self, tmp_path: Path, node_info: ServerSSHInfo) -> None:
        ssh_dir = tmp_path / "ssh"
        write_ssh_keys(ssh_dir, [node_info])
        assert (ssh_dir / "keys").is_dir()

    def test_overwrites_existing_keys(
        self, tmp_path: Path, node_info: ServerSSHInfo
    ) -> None:
        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()
        (keys_dir / "br-cluster1.pub").write_text("old-key\n")
        write_ssh_keys(tmp_path, [node_info])
        assert (keys_dir / "br-cluster1.pub").read_text() == node_info.public_key + "\n"


class TestGenerateSSHConfig:
    def test_generates_config_file(
        self, tmp_path: Path, all_infos: list[ServerSSHInfo]
    ) -> None:
        generate_ssh_config(tmp_path, all_infos)
        config = (tmp_path / "config").read_text()
        assert "Host br-gateway1" in config
        assert "Host br-cluster1" in config
        assert "Host br-storage1" in config

    def test_gateway_has_no_proxy_jump(
        self, tmp_path: Path, all_infos: list[ServerSSHInfo]
    ) -> None:
        generate_ssh_config(tmp_path, all_infos)
        config = (tmp_path / "config").read_text()
        # Extract gateway block
        blocks = config.split("Host ")
        gateway_block = next(b for b in blocks if b.startswith("br-gateway1"))
        assert "ProxyJump" not in gateway_block

    def test_non_gateway_has_proxy_jump(
        self, tmp_path: Path, all_infos: list[ServerSSHInfo]
    ) -> None:
        generate_ssh_config(tmp_path, all_infos)
        config = (tmp_path / "config").read_text()
        blocks = config.split("Host ")
        node_block = next(b for b in blocks if b.startswith("br-cluster1"))
        assert "ProxyJump br-gateway1" in node_block

    def test_gateway_uses_correct_ip(
        self, tmp_path: Path, all_infos: list[ServerSSHInfo]
    ) -> None:
        generate_ssh_config(tmp_path, all_infos)
        config = (tmp_path / "config").read_text()
        assert "HostName 198.51.100.50" in config

    def test_gateway_comes_first(
        self, tmp_path: Path, all_infos: list[ServerSSHInfo]
    ) -> None:
        generate_ssh_config(tmp_path, all_infos)
        config = (tmp_path / "config").read_text()
        gw_pos = config.index("Host br-gateway1")
        node_pos = config.index("Host br-cluster1")
        assert gw_pos < node_pos

    def test_includes_header(self, tmp_path: Path, gateway_info: ServerSSHInfo) -> None:
        generate_ssh_config(tmp_path, [gateway_info])
        config = (tmp_path / "config").read_text()
        assert "CanonicalizeHostname yes" in config
        assert "StrictHostKeyChecking accept-new" in config

    def test_identity_file_path(
        self, tmp_path: Path, node_info: ServerSSHInfo, gateway_info: ServerSSHInfo
    ) -> None:
        generate_ssh_config(tmp_path, [gateway_info, node_info])
        config = (tmp_path / "config").read_text()
        assert "IdentityFile /root/.ssh/keys/br-cluster1.pub" in config


class TestFetchServerSSHInfo:
    def test_fetches_gateway_with_external_ip(self) -> None:
        client = MagicMock(spec=ConnectClient)
        client.get_field.side_effect = lambda vault, item, field, *, section=None: {
            (
                "vault",
                "br-gateway1",
                "external_ip_address",
                "admin_console",
            ): "198.51.100.50",
            ("vault", "br-gateway1", "username", None): "bradmin",
            ("vault", "br-gateway1_ssh", "public key", None): "ssh-ed25519 AAAA",
        }[(vault, item, field, section)]

        server = ServerDefinition(name="br-gateway1", type=ServerType.GATEWAY)
        info = fetch_server_ssh_info(client, "vault", server)
        assert info.hostname == "198.51.100.50"
        assert info.server_type == ServerType.GATEWAY

    def test_fetches_node_with_internal_ip(self) -> None:
        client = MagicMock(spec=ConnectClient)
        client.get_field.side_effect = lambda vault, item, field, *, section=None: {
            ("vault", "br-cluster1", "ip_address", None): "192.0.2.10",
            ("vault", "br-cluster1", "username", None): "bradmin",
            ("vault", "br-cluster1_ssh", "public key", None): "ssh-ed25519 AAAA",
        }[(vault, item, field, section)]

        server = ServerDefinition(name="br-cluster1", type=ServerType.NODE)
        info = fetch_server_ssh_info(client, "vault", server)
        assert info.hostname == "192.0.2.10"
        assert info.server_type == ServerType.NODE


class TestConnectClient:
    @patch("cluster_forge.op_connect.urllib.request.urlopen")
    def test_wait_for_ready_succeeds(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        client = ConnectClient(token="test-token")
        client.wait_for_ready(timeout=5)

    @patch("cluster_forge.op_connect.urllib.request.urlopen")
    def test_wait_for_ready_timeout(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = OSError("refused")
        client = ConnectClient(token="test-token")
        with pytest.raises(TimeoutError, match="not ready"):
            client.wait_for_ready(timeout=1)
