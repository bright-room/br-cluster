from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cluster_forge.models import ServerDefinition, ServerType
from cluster_forge.op_connect import ConnectClient

SSH_CONFIG_HEADER = """\
CanonicalizeHostname yes
Include ~/.ssh/1Password/config

Host *
  StrictHostKeyChecking accept-new
  UserKnownHostsFile /root/.ssh/known_hosts
"""


@dataclass
class ServerSSHInfo:
    name: str
    hostname: str
    username: str
    public_key: str
    server_type: ServerType


def fetch_server_ssh_info(
    client: ConnectClient,
    vault_name: str,
    server: ServerDefinition,
) -> ServerSSHInfo:
    """Fetch SSH connection info for a server from 1Password."""
    ip_field = (
        "external_ip_address" if server.type == ServerType.GATEWAY else "ip_address"
    )
    section = "admin_console" if server.type == ServerType.GATEWAY else None
    hostname = client.get_field(vault_name, server.name, ip_field, section=section)
    username = client.get_field(vault_name, server.name, "username")
    public_key = client.get_field(vault_name, f"{server.name}_ssh", "public key")
    return ServerSSHInfo(
        name=server.name,
        hostname=hostname,
        username=username,
        public_key=public_key,
        server_type=server.type,
    )


def write_ssh_keys(
    ssh_dir: Path,
    server_infos: list[ServerSSHInfo],
) -> None:
    """Write SSH public keys to docker/ssh/keys/."""
    keys_dir = ssh_dir / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    for info in server_infos:
        key_file = keys_dir / f"{info.name}.pub"
        key_file.write_text(info.public_key + "\n")


def generate_ssh_config(
    ssh_dir: Path,
    server_infos: list[ServerSSHInfo],
) -> None:
    """Generate docker/ssh/config from server info."""
    gateway = next(
        (s for s in server_infos if s.server_type == ServerType.GATEWAY),
        None,
    )
    lines = [SSH_CONFIG_HEADER]
    sorted_infos = sorted(
        server_infos,
        key=lambda s: (s.server_type != ServerType.GATEWAY, s.name),
    )
    for info in sorted_infos:
        lines.append(f"Host {info.name}")
        lines.append(f"  HostName {info.hostname}")
        lines.append("  Port 22")
        lines.append(f"  User {info.username}")
        if info.server_type != ServerType.GATEWAY and gateway:
            lines.append(f"  ProxyJump {gateway.name}")
        lines.append("  IdentitiesOnly yes")
        lines.append(f"  IdentityFile /root/.ssh/keys/{info.name}.pub")
        lines.append("")

    config_file = ssh_dir / "config"
    config_file.write_text("\n".join(lines))
