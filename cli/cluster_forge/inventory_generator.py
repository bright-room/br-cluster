from __future__ import annotations

from pathlib import Path

import yaml

from cluster_forge.models import (
    Inventory,
    K8sRole,
    ServerDefinition,
    ServerType,
)
from cluster_forge.secrets import SecretProvider


def _build_domains(server: ServerDefinition, host_domain_ref: str) -> dict:
    """Build the host domain mapping. One record per physical server.

    Service records (dns / ntp / object-storage / rdbms / k8s-api) live in
    `service_records` in group_vars/all/network.yaml, not here — a service
    name does not always match a role name, and a service can move between
    hosts without the host record changing.
    """
    short = server.name.replace("br-", "")
    return {"server": f"{short}.{host_domain_ref}"}


def _build_interfaces(server: ServerDefinition) -> dict:
    """Build interface mappings based on server type."""
    interfaces: dict[str, str] = {"cluster": "eth0"}
    if server.type == ServerType.GATEWAY:
        interfaces["external"] = "wlan0"
    return interfaces


def generate_hosts_yaml(inventory: Inventory) -> dict:
    """Generate Ansible hosts.yaml structure from servers.yaml."""
    gateways = [s for s in inventory.servers if s.type == ServerType.GATEWAY]
    standalones = [s for s in inventory.servers if s.type == ServerType.STANDALONE]
    nodes_with_k8s = [
        s for s in inventory.servers if s.type == ServerType.NODE and s.k8s_role
    ]
    primaries = [s for s in nodes_with_k8s if s.k8s_role == K8sRole.PRIMARY]
    secondaries = [s for s in nodes_with_k8s if s.k8s_role == K8sRole.SECONDARY]
    workers = [s for s in nodes_with_k8s if s.k8s_role == K8sRole.WORKER]

    cluster_members = [*gateways, *standalones, *nodes_with_k8s]

    def hosts_dict(servers: list[ServerDefinition]) -> dict:
        return {s.name: None for s in servers}

    # One group per service name, in first-seen order. A service can span
    # hosts (certbot runs on both storage1 and db1).
    service_groups: dict[str, dict] = {}
    for server in inventory.servers:
        for service in server.services:
            service_groups.setdefault(service, {"hosts": {}})["hosts"][server.name] = (
                None
            )

    structure: dict = {
        "all": {
            "children": {
                "br_cluster": {"hosts": hosts_dict(cluster_members)},
                "gateway": {"hosts": hosts_dict(gateways)},
                "clusters": {
                    "children": {
                        "master": {
                            "children": {
                                "primary": {"hosts": hosts_dict(primaries)},
                                "secondary": {"hosts": hosts_dict(secondaries)},
                            },
                        },
                        "worker": {"hosts": hosts_dict(workers)},
                    },
                },
                "standalone": {"hosts": hosts_dict(standalones)},
                **service_groups,
            },
        },
    }
    return structure


def generate_cluster_hosts(
    inventory: Inventory,
    env: str,
    provider: SecretProvider,
) -> list[dict]:
    """Generate cluster_hosts list from servers.yaml + 1Password secrets."""
    cluster_hosts = []
    for server in inventory.servers:
        secrets = provider.get_inventory_secrets(
            env,
            server.name,
            is_gateway=server.type == ServerType.GATEWAY,
        )
        # Use Jinja2 reference for host_domain so Ansible resolves it
        domain_ref = "{{ host_domain }}"
        entry: dict = {
            "name": server.name,
            "ip": secrets.ip_address,
            "mac": secrets.mac_address,
            "domains": _build_domains(server, domain_ref),
            "interfaces": _build_interfaces(server),
        }
        cluster_hosts.append(entry)
    return cluster_hosts


def generate_gateway_host_vars(
    inventory: Inventory,
    env: str,
    provider: SecretProvider,
) -> dict[str, dict]:
    """Generate host_vars for gateway servers (wan_ip)."""
    result = {}
    for server in inventory.servers:
        if server.type == ServerType.GATEWAY:
            secrets = provider.get_inventory_secrets(env, server.name, is_gateway=True)
            if secrets.wan_ip:
                result[server.name] = {"wan_ip": secrets.wan_ip}
    return result


def _yaml_str_representer(dumper: yaml.Dumper, data: str) -> yaml.Node:
    """Force quoted strings for values containing Jinja2 expressions."""
    if "{{" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _get_dumper() -> type[yaml.Dumper]:
    dumper = yaml.Dumper
    dumper.add_representer(str, _yaml_str_representer)
    return dumper


def write_inventory(
    inventory: Inventory,
    env: str,
    provider: SecretProvider,
    provisioner_dir: Path,
) -> list[Path]:
    """Generate all dynamic inventory files."""
    inv_dir = provisioner_dir / "inventories" / env
    written: list[Path] = []

    dumper = _get_dumper()

    # 1. hosts.yaml
    hosts = generate_hosts_yaml(inventory)
    hosts_path = inv_dir / "hosts.yaml"
    hosts_path.parent.mkdir(parents=True, exist_ok=True)
    hosts_content = "---\n" + yaml.dump(
        hosts, Dumper=dumper, default_flow_style=False, sort_keys=False
    )
    hosts_path.write_text(hosts_content)
    written.append(hosts_path)

    # 2. group_vars/all/cluster_hosts.yaml
    cluster_hosts = generate_cluster_hosts(inventory, env, provider)
    ch_dir = inv_dir / "group_vars" / "all"
    ch_dir.mkdir(parents=True, exist_ok=True)
    ch_path = ch_dir / "cluster_hosts.yaml"
    ch_content = "---\n" + yaml.dump(
        {"cluster_hosts": cluster_hosts},
        Dumper=dumper,
        default_flow_style=False,
        sort_keys=False,
    )
    ch_path.write_text(ch_content)
    written.append(ch_path)

    # 3. group_vars/all/cluster_env.yaml
    env_dir = inv_dir / "group_vars" / "all"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_path = env_dir / "cluster_env.yaml"
    env_content = "---\n" + yaml.dump(
        {"cluster_env": env}, Dumper=dumper, default_flow_style=False, sort_keys=False
    )
    env_path.write_text(env_content)
    written.append(env_path)

    # 4. host_vars for gateways (wan_ip)
    gw_vars = generate_gateway_host_vars(inventory, env, provider)
    for host_name, variables in gw_vars.items():
        hv_dir = inv_dir / "host_vars"
        hv_dir.mkdir(parents=True, exist_ok=True)
        hv_path = hv_dir / f"{host_name}.yaml"
        hv_content = "---\n" + yaml.dump(
            variables, Dumper=dumper, default_flow_style=False, sort_keys=False
        )
        hv_path.write_text(hv_content)
        written.append(hv_path)

    return written
