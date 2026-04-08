from __future__ import annotations

import subprocess
from pathlib import Path

from cluster_forge.models import ServerDefinition

PACKER_IMAGE = "mkaczanowski/packer-builder-arm:latest"


def generate_pkrvars(server: ServerDefinition, env: str, cloud_init_dir: Path) -> str:
    base = Path("/build/cloud-init") / server.name
    files = [str(base / "user-data")]
    if server.needs_network_config:
        files.append(str(base / "network-config"))

    files_hcl = ", ".join(f'"{f}"' for f in files)
    return f'hostname = "{server.name}"\n\ncloud_config_files = [{files_hcl}]\n'


def build_image(
    server: ServerDefinition,
    env: str,
    project_root: Path,
    cloud_init_dir: Path,
    output_dir: Path,
    packer_dir: Path,
) -> None:
    pkrvars_content = generate_pkrvars(server, env, cloud_init_dir)

    pkrvars_path = packer_dir / f"{server.name}.auto.pkrvars.hcl"
    pkrvars_path.write_text(pkrvars_content)

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir.parent / ".packer_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--privileged",
                "-v",
                "/dev:/dev",
                "-v",
                f"{cloud_init_dir / env}:/build/cloud-init",
                "-v",
                f"{cache_dir}:/build/.packer_cache",
                "-v",
                f"{output_dir}:/build/generated",
                "-v",
                f"{packer_dir}:/build/packer",
                PACKER_IMAGE,
                "build",
                f"--var-file=packer/{server.name}.auto.pkrvars.hcl",
                "packer/",
            ],
            check=True,
        )
    finally:
        pkrvars_path.unlink(missing_ok=True)
