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
        assert "external" in children

    def test_gateway_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        gw_hosts = result["all"]["children"]["gateway"]["hosts"]
        assert "br-gateway1" in gw_hosts

    def test_primary_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        primary = result["all"]["children"]["clusters"]["children"]["master"]
        assert "br-node1" in primary["children"]["primary"]["hosts"]

    def test_secondary_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        secondary = result["all"]["children"]["clusters"]["children"]["master"]
        hosts = secondary["children"]["secondary"]["hosts"]
        assert "br-node2" in hosts
        assert "br-node3" in hosts

    def test_worker_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        workers = result["all"]["children"]["clusters"]["children"]["worker"]
        hosts = workers["hosts"]
        assert "br-node4" in hosts
        assert "br-node5" in hosts
        assert "br-node6" in hosts

    def test_external_group(self, full_inventory: Inventory) -> None:
        result = generate_hosts_yaml(full_inventory)
        ext_hosts = result["all"]["children"]["external"]["hosts"]
        assert "br-external1" in ext_hosts

    def test_br_cluster_contains_all_k8s_members(
        self, full_inventory: Inventory
    ) -> None:
        result = generate_hosts_yaml(full_inventory)
        cluster = result["all"]["children"]["br_cluster"]["hosts"]
        assert "br-gateway1" in cluster
        assert "br-external1" in cluster
        assert "br-node1" in cluster
        assert "br-node6" in cluster

    def test_node_without_k8s_role_excluded_from_clusters(self) -> None:
        inv = Inventory(
            environments=["dev"],
            servers=[
                ServerDefinition(
                    name="br-standalone", type=ServerType.NODE, k8s_role=None
                ),
            ],
        )
        result = generate_hosts_yaml(inv)
        cluster = result["all"]["children"]["br_cluster"]["hosts"]
        assert "br-standalone" not in cluster


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
        node = next(h for h in result if h["name"] == "br-node1")
        assert node["interfaces"] == {"cluster": "eth0"}

    def test_gateway_has_dns_ntp_domains(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        gw = next(h for h in result if h["name"] == "br-gateway1")
        assert "dns" in gw["domains"]
        assert "ntp" in gw["domains"]

    def test_external_has_object_storage_domain(
        self, full_inventory: Inventory
    ) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        ext = next(h for h in result if h["name"] == "br-external1")
        assert "object_storage" in ext["domains"]

    def test_uses_secrets_for_ip_and_mac(self, full_inventory: Inventory) -> None:
        provider = MockSecretProvider()
        result = generate_cluster_hosts(full_inventory, "dev", provider)
        gw = next(h for h in result if h["name"] == "br-gateway1")
        assert gw["ip"] == "192.0.2.1"
        assert gw["mac"] == "00:00:5e:00:53:01"

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
        assert "br-node1" not in result


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
