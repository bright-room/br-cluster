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


@dataclass
class InventorySecrets:
    ip_address: str
    mac_address: str
    wan_ip: str | None = None


class SecretProvider(ABC):
    @abstractmethod
    def get_server_secrets(self, env: str, server_name: str) -> ServerSecrets: ...

    @abstractmethod
    def get_network_secrets(self, env: str, server_name: str) -> NetworkSecrets: ...

    @abstractmethod
    def get_inventory_secrets(
        self, env: str, server_name: str, *, is_gateway: bool = False
    ) -> InventorySecrets: ...


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

    def get_inventory_secrets(
        self, env: str, server_name: str, *, is_gateway: bool = False
    ) -> InventorySecrets:
        base = f"{self._vault_prefix}/{server_name}"
        wan_ip = None
        if is_gateway:
            wan_ip = self._read(f"{base}/admin_console/external_ip_address")
        return InventorySecrets(
            ip_address=self._read(f"{base}/ip_address"),
            mac_address=self._read(f"{base}/mac_address"),
            wan_ip=wan_ip,
        )


class MockSecretProvider(SecretProvider):
    # All addresses below are from documentation-only ranges and have no
    # relationship to real infrastructure:
    #   - IPv4: RFC 5737 TEST-NET-1/2 (192.0.2.0/24, 198.51.100.0/24)
    #   - MAC:  RFC 7042 documentation range (00:00:5E:00:53:00-FF)
    MOCK_SERVERS: dict[str, InventorySecrets] = {
        "br-gateway1": InventorySecrets(
            ip_address="192.0.2.1",
            mac_address="00:00:5e:00:53:01",
            wan_ip="198.51.100.50",
        ),
        "br-node1": InventorySecrets(
            ip_address="192.0.2.10",
            mac_address="00:00:5e:00:53:10",
        ),
        "br-node2": InventorySecrets(
            ip_address="192.0.2.11",
            mac_address="00:00:5e:00:53:11",
        ),
        "br-node3": InventorySecrets(
            ip_address="192.0.2.12",
            mac_address="00:00:5e:00:53:12",
        ),
        "br-node4": InventorySecrets(
            ip_address="192.0.2.13",
            mac_address="00:00:5e:00:53:13",
        ),
        "br-node5": InventorySecrets(
            ip_address="192.0.2.14",
            mac_address="00:00:5e:00:53:14",
        ),
        "br-node6": InventorySecrets(
            ip_address="192.0.2.15",
            mac_address="00:00:5e:00:53:15",
        ),
        "br-node7": InventorySecrets(
            ip_address="192.0.2.16",
            mac_address="00:00:5e:00:53:16",
        ),
    }

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
            internal_ip="192.0.2.100",
            external_ip="198.51.100.1",
            gateway_ip="198.51.100.254",
            ssid="test-wifi",
            wifi_password="test-wifi-password",
        )

    def get_inventory_secrets(
        self, env: str, server_name: str, *, is_gateway: bool = False
    ) -> InventorySecrets:
        if server_name in self.MOCK_SERVERS:
            return self.MOCK_SERVERS[server_name]
        return InventorySecrets(
            ip_address="192.0.2.99",
            mac_address="00:00:5e:00:53:99",
        )
