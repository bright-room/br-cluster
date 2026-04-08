from __future__ import annotations

from unittest.mock import patch

import pytest

from cluster_forge.provisioner import (
    PLAYBOOK_COMMANDS,
    lint,
    ping,
    run_playbook,
    runner_setup,
)

FAKE_CMD = ["docker", "compose", "-p", "test"]
FAKE_ENV = {"ENV": "dev", "PATH": "/usr/bin"}


class TestPlaybookCommands:
    def test_all_playbooks_have_yaml_extension(self) -> None:
        for key, path in PLAYBOOK_COMMANDS.items():
            assert path.endswith(".yaml"), f"{key} -> {path}"

    def test_all_playbooks_in_playbooks_dir(self) -> None:
        for key, path in PLAYBOOK_COMMANDS.items():
            assert path.startswith("playbooks/"), f"{key} -> {path}"


class TestRunnerSetup:
    @patch("cluster_forge.provisioner.subprocess.run")
    def test_calls_ansible_galaxy(self, mock_run) -> None:
        runner_setup(FAKE_CMD, FAKE_ENV)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == [
            *FAKE_CMD,
            "exec",
            "ansible-runner",
            "ansible-galaxy",
            "install",
            "-r",
            "requirements.yaml",
        ]
        assert mock_run.call_args[1]["env"] == FAKE_ENV


class TestRunPlaybook:
    @patch("cluster_forge.provisioner.subprocess.run")
    def test_runs_known_playbook(self, mock_run) -> None:
        run_playbook(FAKE_CMD, FAKE_ENV, "dev", "setup-node")
        args = mock_run.call_args[0][0]
        assert "ansible-playbook" in args
        assert "inventories/dev/hosts.yaml" in args
        assert "playbooks/setup_node.yaml" in args

    @patch("cluster_forge.provisioner.subprocess.run")
    def test_check_mode_adds_flags(self, mock_run) -> None:
        run_playbook(FAKE_CMD, FAKE_ENV, "prod", "setup-gateway", check_mode=True)
        args = mock_run.call_args[0][0]
        assert "--check" in args
        assert "--diff" in args

    def test_unknown_playbook_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown playbook"):
            run_playbook(FAKE_CMD, FAKE_ENV, "dev", "nonexistent")

    @patch("cluster_forge.provisioner.subprocess.run")
    def test_all_playbooks_callable(self, mock_run) -> None:
        for key in PLAYBOOK_COMMANDS:
            run_playbook(FAKE_CMD, FAKE_ENV, "dev", key)
        assert mock_run.call_count == len(PLAYBOOK_COMMANDS)


class TestPing:
    @patch("cluster_forge.provisioner.subprocess.run")
    def test_pings_all_hosts(self, mock_run) -> None:
        ping(FAKE_CMD, FAKE_ENV, "prod")
        args = mock_run.call_args[0][0]
        assert "ansible" in args
        assert "inventories/prod/hosts.yaml" in args
        assert "all" in args
        assert "-m" in args
        assert "ping" in args


class TestLint:
    @patch("cluster_forge.provisioner.subprocess.run")
    def test_lints_playbooks(self, mock_run) -> None:
        lint(FAKE_CMD, FAKE_ENV)
        args = mock_run.call_args[0][0]
        assert "ansible-lint" in args
        assert "playbooks/" in args
