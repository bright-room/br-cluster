from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cluster_forge.cli import (
    _compose_cmd,
    _read_secrets,
    _resolve_servers,
    main,
)
from cluster_forge.models import Inventory


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    secret_dir = tmp_path / ".secret" / "dev"
    secret_dir.mkdir(parents=True)
    (secret_dir / "1password-credentials.json").write_text('{"key": "value"}')
    (secret_dir / ".connect_token").write_text("fake-token\n")
    monkeypatch.setattr("cluster_forge.cli.REPO_ROOT", tmp_path)
    return tmp_path


class TestComposeCmd:
    def test_returns_docker_compose_args(self) -> None:
        cmd = _compose_cmd("dev")
        assert cmd[0:2] == ["docker", "compose"]
        assert "-f" in cmd
        assert "-p" in cmd
        assert "dev-cluster-forge" in cmd

    def test_prod_project_name(self) -> None:
        cmd = _compose_cmd("prod")
        assert "prod-cluster-forge" in cmd


class TestReadSecrets:
    def test_reads_credentials(self, fake_secret_dir: Path) -> None:
        session, token = _read_secrets("dev")
        expected = base64.b64encode(b'{"key": "value"}').decode()
        assert session == expected
        assert token == "fake-token"

    def test_missing_credentials_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("cluster_forge.cli.REPO_ROOT", tmp_path)
        with pytest.raises(Exception, match="Missing credentials"):
            _read_secrets("dev")


class TestResolveServers:
    def test_returns_all_servers(self, sample_inventory: Inventory) -> None:
        result = _resolve_servers(sample_inventory, "dev", None)
        assert len(result) == 3

    def test_filters_by_name(self, sample_inventory: Inventory) -> None:
        result = _resolve_servers(sample_inventory, "dev", "br-node1")
        assert len(result) == 1
        assert result[0].name == "br-node1"

    def test_unknown_env_raises(self, sample_inventory: Inventory) -> None:
        with pytest.raises(Exception, match="Unknown environment"):
            _resolve_servers(sample_inventory, "staging", None)

    def test_unknown_server_raises(self, sample_inventory: Inventory) -> None:
        with pytest.raises(Exception, match="Unknown server"):
            _resolve_servers(sample_inventory, "dev", "nonexistent")


class TestBootstrapCommand:
    @patch("cluster_forge.cli.load_inventory")
    @patch("cluster_forge.cli.fetch_server_ssh_info")
    @patch("cluster_forge.cli.write_ssh_keys")
    @patch("cluster_forge.cli.generate_ssh_config")
    @patch("cluster_forge.cli.ConnectClient")
    @patch("cluster_forge.cli.subprocess.run")
    def test_bootstrap_runs_staged_startup(
        self,
        mock_run,
        mock_client_cls,
        mock_gen_config,
        mock_write_keys,
        mock_fetch_info,
        mock_load_inv,
        runner: CliRunner,
        fake_secret_dir: Path,
        sample_inventory,
    ) -> None:
        mock_load_inv.return_value = sample_inventory
        mock_fetch_info.return_value = MagicMock()
        result = runner.invoke(main, ["bootstrap", "--env", "dev"])
        assert result.exit_code == 0
        assert "Bootstrap (dev) complete" in result.output
        # docker compose called twice: connect first, then ansible-runner
        assert mock_run.call_count == 2
        first_call_args = mock_run.call_args_list[0][0][0]
        assert "op-connect-api" in first_call_args
        assert "op-connect-sync" in first_call_args
        second_call_args = mock_run.call_args_list[1][0][0]
        assert "ansible-runner" in second_call_args


class TestCleanCommand:
    @patch("cluster_forge.cli.subprocess.run")
    def test_clean_stops_containers(
        self, mock_run, runner: CliRunner, fake_secret_dir: Path
    ) -> None:
        result = runner.invoke(main, ["clean", "--env", "dev"])
        assert result.exit_code == 0
        assert "Done." in result.output
        args = mock_run.call_args[0][0]
        assert "down" in args
        assert "-v" in args

    @patch("cluster_forge.cli.subprocess.run")
    def test_clean_without_all_keeps_generated_dir(
        self,
        mock_run,
        runner: CliRunner,
        fake_secret_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen_dir = fake_secret_dir / ".generated"
        gen_dir.mkdir()
        (gen_dir / "dummy").write_text("x")
        monkeypatch.setattr("cluster_forge.cli.GENERATED_DIR", gen_dir)
        monkeypatch.setattr("cluster_forge.cli.CLOUD_INIT_DIR", gen_dir / "ci")
        result = runner.invoke(main, ["clean", "--env", "dev"])
        assert result.exit_code == 0
        assert gen_dir.exists()

    @patch("cluster_forge.cli.subprocess.run")
    def test_clean_all_removes_generated_dir(
        self,
        mock_run,
        runner: CliRunner,
        fake_secret_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        gen_dir = fake_secret_dir / ".generated"
        gen_dir.mkdir()
        (gen_dir / "dummy").write_text("x")
        monkeypatch.setattr("cluster_forge.cli.GENERATED_DIR", gen_dir)
        monkeypatch.setattr("cluster_forge.cli.CLOUD_INIT_DIR", gen_dir / "ci")
        result = runner.invoke(main, ["clean", "--env", "dev", "--all"])
        assert result.exit_code == 0
        assert not gen_dir.exists()


class TestProvisionCommands:
    @patch("cluster_forge.cli.provisioner_mod.run_playbook")
    def test_provision_run(
        self, mock_run, runner: CliRunner, fake_secret_dir: Path
    ) -> None:
        result = runner.invoke(main, ["provision", "run", "--env", "dev", "setup-node"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][2] == "dev"
        assert call_args[0][3] == "setup-node"

    @patch("cluster_forge.cli.provisioner_mod.ping")
    def test_provision_ping(
        self, mock_ping, runner: CliRunner, fake_secret_dir: Path
    ) -> None:
        result = runner.invoke(main, ["provision", "ping", "--env", "dev"])
        assert result.exit_code == 0
        mock_ping.assert_called_once()

    @patch("cluster_forge.cli.provisioner_mod.lint")
    def test_provision_lint(
        self, mock_lint, runner: CliRunner, fake_secret_dir: Path
    ) -> None:
        result = runner.invoke(main, ["provision", "lint", "--env", "dev"])
        assert result.exit_code == 0
        mock_lint.assert_called_once()

    @patch("cluster_forge.cli.provisioner_mod.runner_setup")
    def test_provision_setup(
        self, mock_setup, runner: CliRunner, fake_secret_dir: Path
    ) -> None:
        result = runner.invoke(main, ["provision", "setup", "--env", "dev"])
        assert result.exit_code == 0
        mock_setup.assert_called_once()
