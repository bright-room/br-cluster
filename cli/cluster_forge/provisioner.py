from __future__ import annotations

import subprocess

PLAYBOOK_COMMANDS = {
    "setup-gateway": "playbooks/setup_gateway.yaml",
    "setup-external": "playbooks/setup_external.yaml",
    "setup-node": "playbooks/setup_node.yaml",
    "setup-backup": "playbooks/setup_backup.yaml",
    "setup-monitoring-agent": "playbooks/setup_monitoring_agent.yaml",
    "bootstrap-cluster": "playbooks/bootstrap_cluster.yaml",
    "k3s-start": "playbooks/k3s_start.yaml",
    "k3s-stop": "playbooks/k3s_stop.yaml",
    "k3s-reset": "playbooks/k3s_reset.yaml",
}


def _exec_in_runner(
    compose_cmd: list[str],
    compose_env: dict[str, str],
    args: list[str],
) -> None:
    subprocess.run(
        [*compose_cmd, "exec", "ansible-runner", *args],
        env=compose_env,
        check=True,
    )


def runner_setup(compose_cmd: list[str], compose_env: dict[str, str]) -> None:
    _exec_in_runner(
        compose_cmd,
        compose_env,
        ["ansible-galaxy", "install", "-r", "requirements.yaml"],
    )


def run_playbook(
    compose_cmd: list[str],
    compose_env: dict[str, str],
    env: str,
    playbook_key: str,
    *,
    check_mode: bool = False,
) -> None:
    if playbook_key not in PLAYBOOK_COMMANDS:
        available = ", ".join(sorted(PLAYBOOK_COMMANDS))
        raise ValueError(f"Unknown playbook: {playbook_key}. Available: {available}")

    inventory = f"inventories/{env}/hosts.yaml"
    playbook = PLAYBOOK_COMMANDS[playbook_key]
    args = ["ansible-playbook", "-i", inventory, playbook]
    if check_mode:
        args.extend(["--check", "--diff"])
    _exec_in_runner(compose_cmd, compose_env, args)


def ping(compose_cmd: list[str], compose_env: dict[str, str], env: str) -> None:
    inventory = f"inventories/{env}/hosts.yaml"
    _exec_in_runner(
        compose_cmd,
        compose_env,
        ["ansible", "-i", inventory, "all", "-m", "ping"],
    )


def lint(compose_cmd: list[str], compose_env: dict[str, str]) -> None:
    _exec_in_runner(compose_cmd, compose_env, ["ansible-lint", "playbooks/"])
