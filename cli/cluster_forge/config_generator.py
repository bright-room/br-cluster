from __future__ import annotations

import hashlib
from pathlib import Path

from jinja2 import Environment, PackageLoader

from cluster_forge.models import ServerDefinition
from cluster_forge.secrets import NetworkSecrets, SecretProvider, ServerSecrets

_jinja_env = Environment(
    loader=PackageLoader("cluster_forge", "templates"),
    keep_trailing_newline=True,
)


def hash_password(password: str) -> str:
    from passlib.hash import sha512_crypt

    return sha512_crypt.using(rounds=5000).hash(password)


def hash_wpa_passphrase(ssid: str, passphrase: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha1",
        passphrase.encode(),
        ssid.encode(),
        4096,
        dklen=32,
    ).hex()


def render_user_data(server: ServerDefinition, secrets: ServerSecrets) -> str:
    template = _jinja_env.get_template("user-data.j2")
    return template.render(
        hostname=secrets.hostname,
        root_password=hash_password(secrets.root_password),
        operator_username=secrets.operator_username,
        operator_password=hash_password(secrets.operator_password),
        operator_pubkey=secrets.operator_pubkey,
        server_type=server.type.value,
    )


def render_network_config(secrets: NetworkSecrets) -> str:
    template = _jinja_env.get_template("network-config.j2")
    return template.render(
        internal_ip=secrets.internal_ip,
        ssid=secrets.ssid,
        passphrase=hash_wpa_passphrase(secrets.ssid, secrets.wifi_password),
        external_ip=secrets.external_ip,
        gateway_ip=secrets.gateway_ip,
    )


def generate_config(
    server: ServerDefinition,
    env: str,
    provider: SecretProvider,
    output_dir: Path,
) -> list[Path]:
    server_dir = output_dir / env / server.name
    server_dir.mkdir(parents=True, exist_ok=True)

    server_secrets = provider.get_server_secrets(env, server.name)

    user_data_path = server_dir / "user-data"
    user_data_path.write_text(render_user_data(server, server_secrets))
    generated = [user_data_path]

    if server.needs_network_config:
        network_secrets = provider.get_network_secrets(env, server.name)
        network_config_path = server_dir / "network-config"
        network_config_path.write_text(render_network_config(network_secrets))
        generated.append(network_config_path)

    return generated
