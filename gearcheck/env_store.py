"""Read/write the local .env file.

Everything secret lives here and nowhere else. Values are never logged; use
`mask_secret()` for passwords, or `mask()` for tokens and ids where seeing the
last few characters helps you tell one from another.

Lookup order, first existing file wins for both reading and writing:

  1. $GEARCHECK_ENV, if set
  2. .env beside this package
  3. .env in the main checkout, when we are running inside a git worktree
  4. ~/.gearcheck.env

Rule 3 matters because tools that spawn git worktrees (Claude Code among them)
run the code from a throwaway copy of the repo. An untracked .env never exists
there, and anything written to one is lost when the worktree is recycled — so
credentials would appear to vanish between sessions. Following the worktree's
own .git pointer back to the main checkout keeps one .env for all of them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
USER_ENV_PATH = Path.home() / ".gearcheck.env"

_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _main_checkout_root() -> Path | None:
    """The main checkout's root, when PROJECT_ROOT is a linked git worktree.

    A worktree's .git is a file reading `gitdir: <main>/.git/worktrees/<name>`,
    so the main root is three levels above that path.
    """
    git = PROJECT_ROOT / ".git"
    if not git.is_file():
        return None  # a real .git directory, or no repo at all
    try:
        text = git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.split(":", 1)[1].strip())
    if gitdir.parent.name != "worktrees" or gitdir.parent.parent.name != ".git":
        return None
    return gitdir.parent.parent.parent


def env_paths() -> list[Path]:
    """Every .env we would consult, most specific first, without duplicates."""
    candidates: list[Path] = []
    override = os.getenv("GEARCHECK_ENV")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(ENV_PATH)
    main_root = _main_checkout_root()
    if main_root is not None:
        candidates.append(main_root / ".env")
    candidates.append(USER_ENV_PATH)

    seen: set[str] = set()
    paths: list[Path] = []
    for path in candidates:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def active_path() -> Path:
    """The file we read from and write to: the first that exists, else ENV_PATH."""
    for path in env_paths():
        if path.exists():
            return path
    return ENV_PATH


def load() -> None:
    """Load every .env we can find into os.environ. Real env vars still win.

    Earlier files take precedence, since load_dotenv never overrides a key that
    is already set.
    """
    for path in env_paths():
        if path.exists():
            load_dotenv(path, override=False)


def get(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key, default)
    if value is not None:
        value = value.strip()
    return value or default


def set_values(values: dict[str, str]) -> None:
    """Insert or update keys in .env, preserving comments and unrelated lines."""
    env_path = active_path()
    lines = (
        env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
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

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass  # Best effort; Windows POSIX modes are advisory at most.

    for key, value in values.items():
        os.environ[key] = value


def mask(value: str | None) -> str:
    """Render a token or id as e.g. '****1a2b' — enough to tell two apart.

    Not for passwords: the last four characters of a short secret are a real
    fraction of it. Use `mask_secret()` for those.
    """
    if not value:
        return "<unset>"
    return "****" + value[-4:] if len(value) > 4 else "****"


def mask_secret(value: str | None) -> str:
    """Confirm a password exists without revealing any part of it."""
    if not value:
        return "<unset>"
    return f"<set, {len(value)} chars>"
