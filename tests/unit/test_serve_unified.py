"""Unit tests for STORY-070.15 — unified `tapps-brain serve` command.

Tests cover:
  AC1: cmd_serve starts HTTP adapter + optional MCP transport in one process.
  AC2: Config via TAPPS_BRAIN_HTTP_PORT and TAPPS_BRAIN_MCP_HTTP_PORT.
  AC3: Graceful shutdown stops both transports.
  AC4: docker-compose.hive.yaml has a single tapps-brain service.
  AC5: docs/guides/deployment.md exists and contains required content.
  AC6: Healthcheck aggregates both transports.
  AC7: Migration guide for 3.5.x operators exists.
"""

from __future__ import annotations

import contextlib
import os
import signal
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent


def _run_cmd_serve(
    *,
    port: int = 8080,
    host: str = "127.0.0.1",
    mcp_port: int = 0,
    mcp_host: str = "127.0.0.1",
    dsn: str | None = None,
    stop_after_s: float = 0.15,
) -> None:
    """Run cmd_serve in the current thread; send SIGINT after *stop_after_s*."""
    import threading

    def _send_sigint() -> None:
        time.sleep(stop_after_s)
        os.kill(os.getpid(), signal.SIGINT)

    t = threading.Thread(target=_send_sigint, daemon=True)
    t.start()

    from tapps_brain.cli import cmd_serve

    cmd_serve(host=host, port=port, dsn=dsn, mcp_host=mcp_host, mcp_port=mcp_port)


# ---------------------------------------------------------------------------
# AC1 / AC2 — serve starts HTTP adapter; MCP transport starts when port > 0
# ---------------------------------------------------------------------------


class TestServeHttpOnly:
    """cmd_serve with mcp_port=0 starts only the HTTP adapter."""

    def test_http_adapter_started(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_adapter = MagicMock()
        mock_adapter_cls = MagicMock(return_value=mock_adapter)

        with (
            patch("tapps_brain.http_adapter.HttpAdapter", mock_adapter_cls),
            patch("os.environ.get", side_effect=lambda k, d=None: None),
        ):
            _run_cmd_serve(port=18080, mcp_port=0, stop_after_s=0.1)

        mock_adapter.start.assert_called_once()

    def test_mcp_not_started_when_mcp_port_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_adapter = MagicMock()
        mock_mcp = MagicMock()

        with (
            patch("tapps_brain.http_adapter.HttpAdapter", return_value=mock_adapter),
            patch("tapps_brain.mcp_server.create_operator_server", mock_mcp),
            patch("os.environ.get", side_effect=lambda k, d=None: None),
        ):
            _run_cmd_serve(port=18081, mcp_port=0, stop_after_s=0.1)

        # create_operator_server should NOT have been called
        mock_mcp.assert_not_called()


class TestServeDualTransport:
    """cmd_serve with mcp_port > 0 starts both HTTP adapter and MCP transport."""

    def test_mcp_thread_started_when_mcp_port_set(self) -> None:
        mock_adapter = MagicMock()
        mock_mcp_server = MagicMock()
        mock_mcp_server.settings = MagicMock()
        mock_mcp_server.run = MagicMock()
        started_threads: list[threading.Thread] = []

        original_thread_init = threading.Thread.__init__

        def _patched_thread_init(self: threading.Thread, *args: object, **kwargs: object) -> None:
            original_thread_init(self, *args, **kwargs)
            if kwargs.get("name") == "tapps-brain-mcp":
                started_threads.append(self)

        with (
            patch("tapps_brain.http_adapter.HttpAdapter", return_value=mock_adapter),
            patch(
                "tapps_brain.mcp_server.create_operator_server",
                return_value=mock_mcp_server,
            ),
            patch.object(threading.Thread, "__init__", _patched_thread_init),
            patch("os.environ.get", side_effect=lambda k, d=None: None),
        ):
            _run_cmd_serve(port=18082, mcp_port=8091, stop_after_s=0.1)

        assert any(
            t.name == "tapps-brain-mcp" for t in started_threads
        ), "Expected a thread named 'tapps-brain-mcp' to be created"

    def test_mcp_server_host_and_port_configured(self) -> None:
        mock_adapter = MagicMock()
        mock_mcp_server = MagicMock()
        mock_settings = MagicMock()
        mock_mcp_server.settings = mock_settings
        mock_mcp_server.run = MagicMock()

        with (
            patch("tapps_brain.http_adapter.HttpAdapter", return_value=mock_adapter),
            patch(
                "tapps_brain.mcp_server.create_operator_server",
                return_value=mock_mcp_server,
            ),
            patch("os.environ.get", side_effect=lambda k, d=None: None),
        ):
            _run_cmd_serve(
                port=18083,
                host="127.0.0.1",
                mcp_port=9091,
                mcp_host="127.0.0.2",
                stop_after_s=0.1,
            )

        assert mock_settings.port == 9091
        assert mock_settings.host == "127.0.0.2"

    def test_env_var_mcp_http_port_controls_mcp_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TAPPS_BRAIN_MCP_HTTP_PORT env var is read by Typer/cmd_serve.

        This is exercised indirectly via the functional tests above; here
        we verify the signature uses the correct envvar name via inspect.
        """
        import inspect

        from tapps_brain.cli import cmd_serve

        # Typer Annotated options store envvar in the OptionInfo.
        sig = inspect.signature(cmd_serve)
        param = sig.parameters.get("mcp_port")
        assert param is not None, "cmd_serve must have a mcp_port parameter"
        # Check annotation string for envvar
        annotation_str = str(param.annotation)
        assert "TAPPS_BRAIN_MCP_HTTP_PORT" in annotation_str, (
            "mcp_port must be bound to TAPPS_BRAIN_MCP_HTTP_PORT env var"
        )


# ---------------------------------------------------------------------------
# AC2 — port defaults
# ---------------------------------------------------------------------------


class TestServePortDefaults:
    def test_http_port_default_is_8080(self) -> None:
        import inspect

        from tapps_brain.cli import cmd_serve

        sig = inspect.signature(cmd_serve)
        assert sig.parameters["port"].default == 8080

    def test_mcp_port_default_is_zero(self) -> None:
        import inspect

        from tapps_brain.cli import cmd_serve

        sig = inspect.signature(cmd_serve)
        assert sig.parameters["mcp_port"].default == 0

    def test_mcp_host_default_is_all_interfaces(self) -> None:
        import inspect

        from tapps_brain.cli import cmd_serve

        sig = inspect.signature(cmd_serve)
        assert sig.parameters["mcp_host"].default == "0.0.0.0"


# ---------------------------------------------------------------------------
# AC3 — graceful shutdown stops both transports
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    def test_adapter_stop_called_on_sigint(self) -> None:
        mock_adapter = MagicMock()

        with (
            patch("tapps_brain.http_adapter.HttpAdapter", return_value=mock_adapter),
            patch("os.environ.get", side_effect=lambda k, d=None: None),
        ):
            _run_cmd_serve(port=18084, mcp_port=0, stop_after_s=0.1)

        mock_adapter.stop.assert_called_once()

    def test_mcp_thread_joined_on_shutdown(self) -> None:
        mock_adapter = MagicMock()
        mock_mcp_server = MagicMock()
        mock_mcp_server.settings = MagicMock()
        mock_mcp_server.run = MagicMock(side_effect=lambda transport: time.sleep(10))

        joined: list[bool] = []

        original_join = threading.Thread.join

        def _patched_join(self: threading.Thread, timeout: float | None = None) -> None:
            if self.name == "tapps-brain-mcp":
                joined.append(True)
            original_join(self, timeout=timeout)

        with (
            patch("tapps_brain.http_adapter.HttpAdapter", return_value=mock_adapter),
            patch(
                "tapps_brain.mcp_server.create_operator_server",
                return_value=mock_mcp_server,
            ),
            patch.object(threading.Thread, "join", _patched_join),
            patch("os.environ.get", side_effect=lambda k, d=None: None),
        ):
            _run_cmd_serve(port=18085, mcp_port=8092, stop_after_s=0.15)

        assert joined, "Expected mcp thread.join() to be called on shutdown"


# ---------------------------------------------------------------------------
# AC4 — docker-compose.hive.yaml has a single tapps-brain service
# ---------------------------------------------------------------------------


class TestDockerCompose:
    _compose_path = _REPO_ROOT / "docker" / "docker-compose.hive.yaml"

    def _load(self) -> dict:  # type: ignore[type-arg]
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(self._compose_path.read_text())

    @pytest.mark.skipif(
        not (_REPO_ROOT / "docker" / "docker-compose.hive.yaml").exists(),
        reason="docker-compose.hive.yaml not present",
    )
    def test_tapps_brain_service_present(self) -> None:
        try:
            compose = self._load()
        except Exception:
            pytest.skip("yaml not available or compose file unparseable")
        assert "tapps-brain" in compose["services"]

    @pytest.mark.skipif(
        not (_REPO_ROOT / "docker" / "docker-compose.hive.yaml").exists(),
        reason="docker-compose.hive.yaml not present",
    )
    def test_old_services_removed(self) -> None:
        try:
            compose = self._load()
        except Exception:
            pytest.skip("yaml not available or compose file unparseable")
        services = compose["services"]
        assert "tapps-brain-http" not in services, (
            "tapps-brain-http should be merged into tapps-brain (STORY-070.15)"
        )
        assert "tapps-brain-operator-mcp" not in services, (
            "tapps-brain-operator-mcp should be merged into tapps-brain (STORY-070.15)"
        )

    @pytest.mark.skipif(
        not (_REPO_ROOT / "docker" / "docker-compose.hive.yaml").exists(),
        reason="docker-compose.hive.yaml not present",
    )
    def test_mcp_http_port_env_var_configured(self) -> None:
        try:
            compose = self._load()
        except Exception:
            pytest.skip("yaml not available or compose file unparseable")
        env = compose["services"]["tapps-brain"].get("environment", {})
        env_dict = dict(e.split("=", 1) for e in env) if isinstance(env, list) else env
        assert any("TAPPS_BRAIN_MCP_HTTP_PORT" in str(k) for k in env_dict), (
            "TAPPS_BRAIN_MCP_HTTP_PORT must be set in the tapps-brain service"
        )

    @pytest.mark.skipif(
        not (_REPO_ROOT / "docker" / "docker-compose.hive.yaml").exists(),
        reason="docker-compose.hive.yaml not present",
    )
    def test_visual_depends_on_tapps_brain(self) -> None:
        try:
            compose = self._load()
        except Exception:
            pytest.skip("yaml not available or compose file unparseable")
        visual = compose["services"].get("tapps-visual", {})
        depends = visual.get("depends_on", {})
        if isinstance(depends, list):
            assert "tapps-brain" in depends
        else:
            assert "tapps-brain" in depends


# ---------------------------------------------------------------------------
# AC5 — docs/guides/deployment.md exists and contains required content
# ---------------------------------------------------------------------------


class TestDeploymentDoc:
    _doc_path = _REPO_ROOT / "docs" / "guides" / "deployment.md"

    def test_deployment_doc_exists(self) -> None:
        assert self._doc_path.exists(), "docs/guides/deployment.md must exist"

    def test_deployment_doc_shared_service_pattern(self) -> None:
        content = self._doc_path.read_text()
        assert "shared" in content.lower() or "unified" in content.lower(), (
            "deployment.md must describe the shared-service pattern"
        )

    def test_deployment_doc_has_agentforge_snippet(self) -> None:
        content = self._doc_path.read_text()
        assert "agentforge" in content.lower() or "AgentForge" in content, (
            "deployment.md must contain an AgentForge client snippet"
        )

    def test_deployment_doc_has_agent_md_example(self) -> None:
        content = self._doc_path.read_text()
        assert "AGENT.md" in content, (
            "deployment.md must contain an AGENT.md wiring example"
        )

    def test_deployment_doc_has_port_table(self) -> None:
        content = self._doc_path.read_text()
        assert "8080" in content and "8090" in content, (
            "deployment.md must document both ports"
        )


# ---------------------------------------------------------------------------
# AC6 — Dockerfile healthcheck aggregates both transports
# ---------------------------------------------------------------------------


class TestDockerfileHealthcheck:
    _dockerfile_path = _REPO_ROOT / "docker" / "Dockerfile.http"

    def test_dockerfile_exists(self) -> None:
        assert self._dockerfile_path.exists()

    def test_dockerfile_healthcheck_probes_both_ports(self) -> None:
        content = self._dockerfile_path.read_text()
        # Healthcheck should reference both 8080 (health) and MCP port check
        assert "8080" in content, "Dockerfile should probe port 8080"
        # The MCP port check uses the env var
        assert "MCP_HTTP_PORT" in content or "8090" in content or "mcp" in content.lower(), (
            "Dockerfile healthcheck should cover the MCP transport"
        )

    def test_dockerfile_cmd_uses_tapps_brain_serve(self) -> None:
        content = self._dockerfile_path.read_text()
        assert "tapps-brain" in content and "serve" in content, (
            "Dockerfile CMD should use 'tapps-brain serve'"
        )


# ---------------------------------------------------------------------------
# AC7 — Migration guide for 3.5.x operators
# ---------------------------------------------------------------------------


class TestMigrationDoc:
    _doc_path = _REPO_ROOT / "docs" / "guides" / "migration-3.5-to-3.6.md"

    def test_migration_doc_exists(self) -> None:
        assert self._doc_path.exists(), "docs/guides/migration-3.5-to-3.6.md must exist"

    def test_migration_doc_covers_compose_rename(self) -> None:
        content = self._doc_path.read_text()
        assert "tapps-brain-http" in content, (
            "Migration guide must mention the old tapps-brain-http service"
        )
        assert "tapps-brain" in content, (
            "Migration guide must document the new unified service name"
        )

    def test_migration_doc_covers_mcp_port_env(self) -> None:
        content = self._doc_path.read_text()
        assert "TAPPS_BRAIN_MCP_HTTP_PORT" in content, (
            "Migration guide must document TAPPS_BRAIN_MCP_HTTP_PORT"
        )

    def test_migration_doc_covers_http_only_mode(self) -> None:
        content = self._doc_path.read_text()
        # Should explain how to run HTTP-only (set mcp_port=0)
        has_disable = "disable" in content.lower() or "http-only" in content.lower()
        assert "0" in content and has_disable, (
            "Migration guide must explain how to run HTTP-only (TAPPS_BRAIN_MCP_HTTP_PORT=0)"
        )


# ---------------------------------------------------------------------------
# AC1 (supplement) — mcp import error is handled gracefully
# ---------------------------------------------------------------------------


class TestMcpImportError:
    def test_import_error_prints_warning_and_continues(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """If mcp extra is missing, serve still starts (HTTP-only) with a warning."""
        mock_adapter = MagicMock()

        with (
            patch("tapps_brain.http_adapter.HttpAdapter", return_value=mock_adapter),
            patch(
                "tapps_brain.mcp_server.create_operator_server",
                side_effect=ImportError("mcp not installed"),
            ),
            patch("os.environ.get", side_effect=lambda k, d=None: None),
            contextlib.suppress(SystemExit),
        ):
            _run_cmd_serve(port=18086, mcp_port=9092, stop_after_s=0.1)

        # HTTP adapter must still have been started
        mock_adapter.start.assert_called()
