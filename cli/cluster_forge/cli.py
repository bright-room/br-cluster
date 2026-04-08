from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

import click

from cluster_forge import config_generator as config_gen
from cluster_forge import packer as packer_mod
from cluster_forge import provisioner as provisioner_mod
from cluster_forge.inventory import load_inventory
from cluster_forge.models import Inventory
from cluster_forge.secrets import OnePasswordCliProvider

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLOUD_INIT_DIR = REPO_ROOT / ".generated" / "cloud-init"
GENERATED_DIR = REPO_ROOT / ".generated"
IMAGER_DIR = REPO_ROOT / "imager"

ENV_OPTION = click.option(
    "--env",
    required=True,
    type=click.Choice(["dev", "prod"]),
    help="Target environment",
)
SERVER_OPTION = click.option(
    "--server",
    default=None,
    help="Target server name (default: all)",
)


def _resolve_servers(inventory: Inventory, env: str, server: str | None) -> list:
    if env not in inventory.environments:
        raise click.BadParameter(
            f"Unknown environment: {env}. Available: {inventory.environments}"
        )
    if server:
        matched = [s for s in inventory.servers if s.name == server]
        if not matched:
            names = [s.name for s in inventory.servers]
            raise click.BadParameter(f"Unknown server: {server}. Available: {names}")
        return matched
    return inventory.servers


def _read_secrets(env: str) -> tuple[str, str]:
    secret_dir = REPO_ROOT / ".secret" / env
    cred_file = secret_dir / "1password-credentials.json"
    token_file = secret_dir / ".connect_token"

    if not cred_file.exists() or not token_file.exists():
        raise click.ClickException(
            f"Missing credentials in {secret_dir}. See README.md for setup."
        )

    session = base64.b64encode(cred_file.read_bytes()).decode()
    token = token_file.read_text().strip()
    return session, token


def _compose_env(env: str) -> dict[str, str]:
    session, token = _read_secrets(env)
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "ENV": env,
        "OP_SESSION": session,
        "OP_CONNECT_TOKEN": token,
        "SSH_AUTH_SOCK": os.environ.get("SSH_AUTH_SOCK", ""),
    }


def _compose_cmd(env: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(REPO_ROOT / "compose.yaml"),
        "-p",
        f"{env}-cluster-forge",
    ]


@click.group()
def main() -> None:
    """cluster-forge: Raspberry Pi cluster build & provision tool."""


@main.command()
@ENV_OPTION
def bootstrap(env: str) -> None:
    """Start 1Password Connect and Ansible Runner."""
    click.echo("Starting 1Password Connect...")
    subprocess.run(
        [*_compose_cmd(env), "up", "-d"],
        env=_compose_env(env),
        check=True,
    )
    click.echo(f"1Password Connect + Ansible Runner ({env}) started.")


@main.command("generate-config")
@ENV_OPTION
@SERVER_OPTION
def generate_config_cmd(env: str, server: str | None) -> None:
    """Generate cloud-config files."""
    inventory = load_inventory()
    servers = _resolve_servers(inventory, env, server)
    provider = OnePasswordCliProvider(env)

    for s in servers:
        click.echo(f"Generating config for {s.name} ({env})...")
        files = config_gen.generate_config(s, env, provider, CLOUD_INIT_DIR)
        for f in files:
            click.echo(f"  -> {f.relative_to(REPO_ROOT)}")

    click.echo("Done.")


@main.command("build-image")
@ENV_OPTION
@SERVER_OPTION
@click.option("--skip-generate", is_flag=True, help="Skip config generation")
def build_image_cmd(env: str, server: str | None, skip_generate: bool) -> None:
    """Build OS images (generates config first by default)."""
    inventory = load_inventory()
    servers = _resolve_servers(inventory, env, server)

    if not skip_generate:
        provider = OnePasswordCliProvider(env)
        for s in servers:
            click.echo(f"Generating config for {s.name} ({env})...")
            config_gen.generate_config(s, env, provider, CLOUD_INIT_DIR)

    output_dir = GENERATED_DIR / "images" / env
    for s in servers:
        click.echo(f"Building image for {s.name} ({env})...")
        packer_mod.build_image(
            s, env, REPO_ROOT, CLOUD_INIT_DIR, output_dir, IMAGER_DIR
        )

    click.echo("Done.")


@main.group()
def provision() -> None:
    """Run provisioning commands via Ansible."""


@provision.command("setup")
@ENV_OPTION
def provision_runner_setup(env: str) -> None:
    """Install Ansible Galaxy dependencies."""
    provisioner_mod.runner_setup(_compose_cmd(env), _compose_env(env))


@provision.command("run")
@ENV_OPTION
@click.argument(
    "playbook", type=click.Choice(sorted(provisioner_mod.PLAYBOOK_COMMANDS))
)
@click.option("--check", "check_mode", is_flag=True, help="Dry-run (--check --diff)")
def provision_run(env: str, playbook: str, check_mode: bool) -> None:
    """Run an Ansible playbook."""
    provisioner_mod.run_playbook(
        _compose_cmd(env),
        _compose_env(env),
        env,
        playbook,
        check_mode=check_mode,
    )


@provision.command("ping")
@ENV_OPTION
def provision_ping(env: str) -> None:
    """Ping all hosts."""
    provisioner_mod.ping(_compose_cmd(env), _compose_env(env), env)


@provision.command("lint")
@ENV_OPTION
def provision_lint(env: str) -> None:
    """Lint Ansible playbooks."""
    provisioner_mod.lint(_compose_cmd(env), _compose_env(env))


@main.command()
@ENV_OPTION
@click.option("--all", "remove_all", is_flag=True, help="Also remove generated files.")
def clean(env: str, *, remove_all: bool) -> None:
    """Stop containers. With --all, also remove generated files."""
    subprocess.run(
        [*_compose_cmd(env), "down", "-v"],
        env=_compose_env(env),
        check=False,
    )
    if remove_all:
        for d in [CLOUD_INIT_DIR, GENERATED_DIR]:
            if d.exists():
                shutil.rmtree(d)
                click.echo(f"Removed {d.relative_to(REPO_ROOT)}")

    click.echo("Done.")
