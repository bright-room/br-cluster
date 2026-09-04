from __future__ import annotations

from pathlib import Path

import yaml

from cluster_forge.inventory_generator import (
    generate_cluster_hosts,
    generate_gateway_host_vars,
    generate_hosts_yaml,
    write_inventory,
)
from cluster_forge.models import Inventory, ServerDefinition, ServerType
from cluster_forge.secrets import MockSecretProvider


class TestGenerateHostsYaml:
    def test_contains_all_groups(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        children = result["all"]["children"]
        assert "br_cluster" in children
        assert "gateway" in children
        assert "clusters" in children
        assert "standalone" in children

    def test_external_group_removed(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        assert "external" not in result["all"]["children"]

    def test_gateway_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        gw_hosts = result["all"]["children"]["gateway"]["hosts"]
        assert "br-gateway1" in gw_hosts

    def test_primary_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        primary = result["all"]["children"]["clusters"]["children"]["master"]
        assert "br-cluster1" in primary["children"]["primary"]["hosts"]

    def test_secondary_group_is_empty(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        master = result["all"]["children"]["clusters"]["children"]["master"]
        assert master["children"]["secondary"]["hosts"] == {}

    def test_worker_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        workers = result["all"]["children"]["clusters"]["children"]["worker"]
        hosts = workers["hosts"]
        assert "br-cluster2" in hosts
        assert "br-cluster3" in hosts

    def test_standalone_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        hosts = result["all"]["children"]["standalone"]["hosts"]
        assert "br-db1" in hosts
        assert "br-storage1" in hosts
        assert "br-observability1" in hosts
        assert "br-ai1" in hosts

    def test_service_groups_generated(self, full_inventory: Inventory) -> None:
        children = generate_hosts_yaml(full_inventory)["all"]["children"]
        assert children["garage"]["hosts"] == {"br-storage1": None}
        assert children["caddy"]["hosts"] == {"br-storage1": None}
        assert children["postgresql"]["hosts"] == {"br-db1": None}

    def test_service_group_can_span_hosts(self, full_inventory: Inventory) -> None:
        children = generate_hosts_yaml(full_inventory)["all"]["children"]
        assert children["certbot"]["hosts"] == {
            "br-storage1": None,
            "br-db1": None,
        }

    def test_no_group_for_unused_service(self, full_inventory: Inventory) -> None:
        children = generate_hosts_yaml(full_inventory)["all"]["children"]
        assert "cloudflared" not in children

    def test_br_cluster_contains_every_server(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        cluster = result["all"]["children"]["br_cluster"]["hosts"]
        assert "br-gateway1" in cluster
        assert "br-db1" in cluster
        assert "br-ai1" in cluster
        assert "br-cluster1" in cluster
        assert "br-cluster3" in cluster

    def test_node_without_k8s_role_excluded_from_clusters(self) -> None:
        inv = Inventory(
            environments=["dev"],
            servers=[
                ServerDefinition(name="br-orphan", type=ServerType.NODE, k8s_role=None),
            ],
        )
        result = generate_hosts_yaml(inv)
        cluster = result["all"]["children"]["br_cluster"]["hosts"]
        assert "br-orphan" not in cluster


class TestGenerateClusterHosts:
    def test_returns_entry_per_server(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        assert len(result) == 8

    def test_gateway_has_external_interface(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        gw = next(h for h in result if h["name"] == "br-gateway1")
        assert gw["interfaces"]["external"] == "wlan0"
        assert gw["interfaces"]["cluster"] == "eth0"

    def test_node_has_only_cluster_interface(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        node = next(h for h in result if h["name"] == "br-cluster1")
        assert node["interfaces"] == {"cluster": "eth0"}

    def test_every_host_has_only_server_domain(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        for entry in result:
            assert list(entry["domains"].keys()) == ["server"]

    def test_server_domain_uses_host_domain_ref(
        self, full_inventory: Inventory
    ) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        gw = next(h for h in result if h["name"] == "br-gateway1")
        assert gw["domains"]["server"] == "gateway1.{{ host_domain }}"

    def test_service_domains_are_not_generated(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        for entry in result:
            assert "dns" not in entry["domains"]
            assert "ntp" not in entry["domains"]
            assert "object_storage" not in entry["domains"]

    def test_uses_secrets_for_ip_and_mac(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        gw = next(h for h in result if h["name"] == "br-gateway1")
        assert gw["ip"] == "192.0.2.1"
        assert gw["mac"] == "00:00:5e:00:53:01"

    def test_mock_provider_covers_every_server(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        # 192.0.2.99 is the MockSecretProvider fallback for unknown hosts.
        for entry in result:
            assert entry["ip"] != "192.0.2.99", entry["name"]

    def test_cluster1_mock_ip(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        node = next(h for h in result if h["name"] == "br-cluster1")
        assert node["ip"] == "192.0.2.100"

    def test_no_hostname_field(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        for entry in result:
            assert "hostname" not in entry


class TestGenerateGatewayHostVars:
    def test_generates_wan_ip_for_gateway(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_gateway_host_vars(full_inventory, "dev", provider)
        assert "br-gateway1" in result
        assert result["br-gateway1"]["wan_ip"] == "198.51.100.50"

    def test_no_vars_for_non_gateway(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_gateway_host_vars(full_inventory, "dev", provider)
        assert "br-cluster1" not in result


class TestWriteInventory:
    def test_creates_all_files(self, full_inventory: Inventory, tmp_path: Path) -> None:
        provider = MockSecretProvider()
        files = write_inventory(full_inventory, "dev", provider, tmp_path)
        assert len(files) == 3  # hosts.yaml, cluster_hosts.yaml, gateway host_vars
        assert (tmp_path / "inventories" / "dev" / "hosts.yaml").exists()
        assert (
            tmp_path
            / "inventories"
            / "dev"
            / "group_vars"
            / "all"
            / "cluster_hosts.yaml"
        ).exists()
        assert (
            tmp_path / "inventories" / "dev" / "host_vars" / "br-gateway1.yaml"
        ).exists()

    def test_hosts_yaml_is_valid(
        self, full_inventory: Inventory, tmp_path: Path
    ) -> None:
        provider = MockSecretProvider()
        write_inventory(full_inventory, "dev", provider, tmp_path)
        content = (tmp_path / "inventories" / "dev" / "hosts.yaml").read_text()
        parsed = yaml.safe_load(content)
        assert "all" in parsed

    def test_cluster_hosts_yaml_is_valid(
        self, full_inventory: Inventory, tmp_path: Path
    ) -> None:
        provider = MockSecretProvider()
        write_inventory(full_inventory, "dev", provider, tmp_path)
        content = (
            tmp_path
            / "inventories"
            / "dev"
            / "group_vars"
            / "all"
            / "cluster_hosts.yaml"
        ).read_text()
        parsed = yaml.safe_load(content)
        assert "cluster_hosts" in parsed
        assert len(parsed["cluster_hosts"]) == 8
