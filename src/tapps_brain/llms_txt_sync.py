"""Synchronous llms.txt provider for brain-central doc lookup fallback."""

from __future__ import annotations

import httpx

_HTTP_OK = 200

_KNOWN_LLMS_TXT: dict[str, str] = {
    "fastapi": "https://fastapi.tiangolo.com/llms.txt",
    "pydantic": "https://docs.pydantic.dev/llms.txt",
    "anthropic": "https://docs.anthropic.com/llms.txt",
    "langchain": "https://python.langchain.com/llms.txt",
    "mcp": "https://modelcontextprotocol.io/llms.txt",
    "django": "https://docs.djangoproject.com/llms.txt",
    "flask": "https://flask.palletsprojects.com/llms.txt",
    "sqlalchemy": "https://docs.sqlalchemy.org/llms.txt",
    "docker": "https://docs.docker.com/llms.txt",
    "cloudflare": "https://developers.cloudflare.com/llms.txt",
    "mintlify": "https://mintlify.com/docs/llms.txt",
    "stripe": "https://docs.stripe.com/llms.txt",
    "pytest": "https://docs.pytest.org/llms.txt",
    "github-actions": "https://docs.github.com/llms.txt",
    "httpx": "https://www.python-httpx.org/llms.txt",
    "uvicorn": "https://www.uvicorn.org/llms.txt",
    "ruff": "https://docs.astral.sh/ruff/llms.txt",
    "uv": "https://docs.astral.sh/uv/llms.txt",
}


class LlmsTxtError(Exception):
    """Raised when llms.txt fetch fails."""


def extract_topic_section(content: str, topic: str) -> str:
    """Return the markdown section whose heading contains *topic*, or full body."""
    lines = content.splitlines()
    section_lines: list[str] = []
    in_section = False
    topic_lower = topic.lower()

    for line in lines:
        if line.startswith("#"):
            heading = line.lstrip("#").strip().lower()
            if topic_lower in heading:
                in_section = True
                section_lines.append(line)
                continue
            if in_section:
                break
        if in_section:
            section_lines.append(line)

    return "\n".join(section_lines) if section_lines else content


class SyncLlmsTxtClient:
    """Minimal synchronous llms.txt client (ADR-0014 degraded path)."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def resolve_url(self, library: str) -> str | None:
        lib_lower = library.strip().lower()
        if lib_lower in _KNOWN_LLMS_TXT:
            return _KNOWN_LLMS_TXT[lib_lower]
        guesses = [
            f"https://docs.{lib_lower}.dev/llms.txt",
            f"https://{lib_lower}.readthedocs.io/en/latest/llms.txt",
        ]
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            for url in guesses:
                try:
                    resp = client.head(url)
                    if resp.status_code == _HTTP_OK:
                        return url
                except httpx.HTTPError:
                    pass
                try:
                    resp = client.get(url)
                    if resp.status_code == _HTTP_OK:
                        return url
                except httpx.HTTPError:
                    continue
        return None

    def fetch(self, library: str, *, topic: str = "overview") -> tuple[str, str]:
        """Return ``(source_url, content)`` for *library*."""
        url = self.resolve_url(library)
        if not url:
            raise LlmsTxtError(f"No llms.txt URL for {library}")
        full_url = url.replace("llms.txt", "llms-full.txt")
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            for try_url in (full_url, url):
                try:
                    resp = client.get(try_url)
                    if resp.status_code == _HTTP_OK:
                        content = resp.text.strip()
                        if not content:
                            continue
                        if topic != "overview":
                            content = extract_topic_section(content, topic).strip()
                        if content:
                            return try_url, content
                except httpx.HTTPError:
                    continue
        raise LlmsTxtError(f"llms.txt fetch failed for {library}")
