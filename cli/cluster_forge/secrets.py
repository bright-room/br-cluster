from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ServerSecrets:
    hostname: str
    root_password: str
    operator_username: str
    operator_password: str
    operator_pubkey: str


@dataclass
class NetworkSecrets:
    internal_ip: str
    external_ip: str
    gateway_ip: str
    ssid: str
    wifi_password: str


class SecretProvider(ABC):
    @abstractmethod
    def get_server_secrets(self, env: str, server_name: str) -> ServerSecrets: ...

    @abstractmethod
    def get_network_secrets(self, env: str, server_name: str) -> NetworkSecrets: ...


class OnePasswordCliProvider(SecretProvider):
    def __init__(self, env: str) -> None:
        self._env = env
        self._vault_prefix = f"op://br-cluster-{env}"

    def _read(self, uri: str) -> str:
        result = subprocess.run(
            ["op", "read", uri],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def get_server_secrets(self, env: str, server_name: str) -> ServerSecrets:
        base = f"{self._vault_prefix}/{server_name}"
        return ServerSecrets(
            hostname=self._read(f"{base}/hostname"),
            root_password=self._read(f"{base}/admin_console/admin_password"),
            operator_username=self._read(f"{base}/username"),
            operator_password=self._read(f"{base}/password"),
            operator_pubkey=self._read(
                f"op://br-cluster-{self._env}/{server_name}_ssh/public key"
            ),
        )

    def get_network_secrets(self, env: str, server_name: str) -> NetworkSecrets:
        base = f"{self._vault_prefix}/{server_name}"
        wifi = f"{self._vault_prefix}/home_wifi"
        return NetworkSecrets(
            internal_ip=self._read(f"{base}/ip_address"),
            external_ip=self._read(f"{base}/admin_console/external_ip_address"),
            gateway_ip=self._read(f"{wifi}/GatewayIP"),
            ssid=self._read(f"{wifi}/network_name"),
            wifi_password=self._read(f"{wifi}/wireless_password"),
        )


class MockSecretProvider(SecretProvider):
    def get_server_secrets(self, env: str, server_name: str) -> ServerSecrets:
        return ServerSecrets(
            hostname=server_name,
            root_password="test-root-password",
            operator_username="operator",
            operator_password="test-operator-password",
            operator_pubkey=(
                "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKey operator@test"
            ),
        )

    def get_network_secrets(self, env: str, server_name: str) -> NetworkSecrets:
        return NetworkSecrets(
            internal_ip="192.168.1.1",
            external_ip="10.0.0.1",
            gateway_ip="10.0.0.254",
            ssid="test-wifi",
            wifi_password="test-wifi-password",
        )
