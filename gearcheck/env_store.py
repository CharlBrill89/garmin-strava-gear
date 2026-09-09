"""Read/write the local .env file.

Everything secret lives here and nowhere else. Values are never logged; use
`mask()` if you need to show that a value exists.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def load() -> None:
    """Load .env into os.environ. Existing real env vars win."""
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)


def get(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key, default)
    if value is not None:
        value = value.strip()
    return value or default


def set_values(values: dict[str, str]) -> None:
    """Insert or update keys in .env, preserving comments and unrelated lines."""
    lines = (
        ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    )
    pending = dict(values)
    out: list[str] = []

    for line in lines:
        match = _KEY_RE.match(line)
        key = match.group(1) if match else None
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)

    if out and out[-1].strip():
        out.append("")
    for key, value in pending.items():
        out.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass  # Best effort; Windows POSIX modes are advisory at most.

    for key, value in values.items():
        os.environ[key] = value


def mask(value: str | None) -> str:
    """Render a secret as e.g. '****1a2b' so we can confirm presence without leaking."""
    if not value:
        return "<unset>"
    return "****" + value[-4:] if len(value) > 4 else "****"
