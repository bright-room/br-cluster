from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from cluster_forge.op_connect import ConnectClient


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


class OnePasswordConnectProvider(SecretProvider):
    """SecretProvider backed by 1Password Connect REST API."""

    WIFI_ITEM = "home_wifi"
    ADMIN_SECTION = "admin_console"

    def __init__(self, client: ConnectClient, env: str) -> None:
        self._client = client
        self._vault = f"br-cluster-{env}"

    def get_server_secrets(self, env: str, server_name: str) -> ServerSecrets:
        get = self._client.get_field
        return ServerSecrets(
            hostname=get(self._vault, server_name, "hostname"),
            root_password=get(
                self._vault,
                server_name,
                "admin_password",
                section=self.ADMIN_SECTION,
            ),
            operator_username=get(self._vault, server_name, "username"),
            operator_password=get(self._vault, server_name, "password"),
            operator_pubkey=get(self._vault, f"{server_name}_ssh", "public key"),
        )

    def get_network_secrets(self, env: str, server_name: str) -> NetworkSecrets:
        get = self._client.get_field
        return NetworkSecrets(
            internal_ip=get(self._vault, server_name, "ip_address"),
            external_ip=get(
                self._vault,
                server_name,
                "external_ip_address",
                section=self.ADMIN_SECTION,
            ),
            gateway_ip=get(self._vault, self.WIFI_ITEM, "GatewayIP"),
            ssid=get(self._vault, self.WIFI_ITEM, "network_name"),
            wifi_password=get(self._vault, self.WIFI_ITEM, "wireless_password"),
        )

    def get_inventory_secrets(
        self, env: str, server_name: str, *, is_gateway: bool = False
    ) -> InventorySecrets:
        get = self._client.get_field
        wan_ip = None
        if is_gateway:
            wan_ip = get(
                self._vault,
                server_name,
                "external_ip_address",
                section=self.ADMIN_SECTION,
            )
        return InventorySecrets(
            ip_address=get(self._vault, server_name, "ip_address"),
            mac_address=get(self._vault, server_name, "mac_address"),
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
        "br-db1": InventorySecrets(
            ip_address="192.0.2.10",
            mac_address="00:00:5e:00:53:10",
        ),
        "br-storage1": InventorySecrets(
            ip_address="192.0.2.20",
            mac_address="00:00:5e:00:53:20",
        ),
        "br-observability1": InventorySecrets(
            ip_address="192.0.2.30",
            mac_address="00:00:5e:00:53:30",
        ),
        "br-ai1": InventorySecrets(
            ip_address="192.0.2.70",
            mac_address="00:00:5e:00:53:70",
        ),
        "br-cluster1": InventorySecrets(
            ip_address="192.0.2.100",
            mac_address="00:00:5e:00:53:64",
        ),
        "br-cluster2": InventorySecrets(
            ip_address="192.0.2.101",
            mac_address="00:00:5e:00:53:65",
        ),
        "br-cluster3": InventorySecrets(
            ip_address="192.0.2.102",
            mac_address="00:00:5e:00:53:66",
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
