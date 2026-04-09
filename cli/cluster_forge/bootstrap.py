from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from cluster_forge.models import ServerDefinition, ServerType

CONNECT_API_PORT = 8080

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


class ConnectClient:
    """1Password Connect REST API client."""

    def __init__(self, token: str, port: int = CONNECT_API_PORT) -> None:
        self._base_url = f"http://localhost:{port}"
        self._token = token
        self._vault_cache: dict[str, str] = {}

    def _request(self, path: str) -> list | dict:
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def wait_for_ready(self, timeout: int = 60) -> None:
        """Poll /heartbeat until the API is ready."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request(f"{self._base_url}/heartbeat")
                with urllib.request.urlopen(req, timeout=5):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(2)
        msg = f"Connect API not ready after {timeout}s"
        raise TimeoutError(msg)

    def _resolve_vault_id(self, vault_name: str) -> str:
        if vault_name in self._vault_cache:
            return self._vault_cache[vault_name]
        vaults = self._request("/v1/vaults")
        for v in vaults:
            if v["name"] == vault_name:
                self._vault_cache[vault_name] = v["id"]
                return v["id"]
        msg = f"Vault not found: {vault_name}"
        raise ValueError(msg)

    def get_field(self, vault_name: str, item_title: str, field_label: str) -> str:
        vault_id = self._resolve_vault_id(vault_name)
        params = urlencode({"filter": f'title eq "{item_title}"'})
        items = self._request(f"/v1/vaults/{vault_id}/items?{params}")
        if not items:
            msg = f"Item not found: {item_title} in {vault_name}"
            raise ValueError(msg)
        item_id = items[0]["id"]
        item = self._request(f"/v1/vaults/{vault_id}/items/{item_id}")
        for field in item.get("fields", []):
            if field.get("label") == field_label:
                return field.get("value", "")
        msg = f"Field '{field_label}' not found in {item_title}"
        raise ValueError(msg)


def fetch_server_ssh_info(
    client: ConnectClient,
    vault_name: str,
    server: ServerDefinition,
) -> ServerSSHInfo:
    """Fetch SSH connection info for a server from 1Password."""
    ip_field = (
        "external_ip_address" if server.type == ServerType.GATEWAY else "ip_address"
    )
    hostname = client.get_field(vault_name, server.name, ip_field)
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
    # Gateway first, then others
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
