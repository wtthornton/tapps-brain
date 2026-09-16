"""TAP-7328 — hive compose must publish the data-plane + dashboard ports to
loopback, not every interface.

``docker/docker-compose.hive.yaml`` previously published
``tapps-brain-http``'s 8080 and ``tapps-visual``'s 8088 with a bare
``"${VAR:-port}:container_port"`` port mapping, which Docker binds to
``0.0.0.0`` on the host. The operator MCP port (``:188``ish, 8090) already
used the correct ``${TAPPS_OPERATOR_MCP_BIND:-127.0.0.1}:...`` pattern —
this test pins both other ports to the same shape.

Uses a plain grep-shaped regex (not YAML parsing) to match VAL-15's
proofCommand exactly, so a passing test and a passing manual proof command
can never disagree. Real alternation ``(HTTP|VISUAL)`` — no ``\\|`` under
``-E``, which would silently match nothing (see lane program notes).
"""

from __future__ import annotations

import re
from pathlib import Path

_COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker" / "docker-compose.hive.yaml"

_PORT_LINE_RE = re.compile(r'^\s*-\s*".*TAPPS_(HTTP|VISUAL)_PORT.*"\s*$', re.MULTILINE)


def _port_lines() -> list[str]:
    text = _COMPOSE_PATH.read_text()
    return [m.group(0) for m in _PORT_LINE_RE.finditer(text)]


class TestHiveComposeLoopbackBinding:
    def test_probe_finds_both_port_lines(self) -> None:
        """Sanity: the probe regex itself must match something in this file —
        an empty match list means the probe is looking in the wrong place,
        not that the file is clean (program house-rule from the VAL-15
        grep-alternation incident)."""
        lines = _port_lines()
        assert len(lines) == 2, f"expected exactly 2 port lines, found {lines!r}"

    def test_http_and_visual_ports_bind_to_loopback_not_all_interfaces(self) -> None:
        for line in _port_lines():
            assert line.lstrip().startswith('- "127.0.0.1:') or re.match(
                r'^-\s*"\$\{TAPPS_BIND:-127\.0\.0\.1\}:', line.lstrip()
            ), f"port line binds to a non-loopback default: {line!r}"

    def test_operator_mcp_port_pattern_still_present(self) -> None:
        """Positive control: the pattern we're copying must still exist."""
        text = _COMPOSE_PATH.read_text()
        assert "${TAPPS_OPERATOR_MCP_BIND:-127.0.0.1}:${TAPPS_OPERATOR_MCP_PORT:-8090}:8090" in text
