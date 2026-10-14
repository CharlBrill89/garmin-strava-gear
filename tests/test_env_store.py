"""Where env_store looks for .env, and how it masks secrets."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gearcheck import env_store


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Point every lookup at tmp_path so the real machine never interferes."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(env_store, "PROJECT_ROOT", project)
    monkeypatch.setattr(env_store, "ENV_PATH", project / ".env")
    monkeypatch.setattr(env_store, "USER_ENV_PATH", tmp_path / "home" / ".gearcheck.env")
    monkeypatch.delenv("GEARCHECK_ENV", raising=False)
    for key in ("GARMIN_EMAIL", "GARMIN_PASSWORD", "ONLY_IN_MAIN"):
        monkeypatch.delenv(key, raising=False)
    return project


def make_worktree(tmp_path: Path, project: Path) -> Path:
    """Give `project` a worktree-style .git file and return the main root."""
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (project / ".git").write_text(
        f"gitdir: {(main / '.git' / 'worktrees' / 'wt').as_posix()}\n", encoding="utf-8"
    )
    return main


class TestMainCheckoutRoot:
    def test_none_when_git_is_a_directory(self, isolated):
        (isolated / ".git").mkdir()
        assert env_store._main_checkout_root() is None

    def test_none_when_no_repo(self, isolated):
        assert env_store._main_checkout_root() is None

    def test_follows_worktree_pointer(self, tmp_path, isolated):
        main = make_worktree(tmp_path, isolated)
        assert env_store._main_checkout_root() == main

    def test_none_for_a_plain_gitdir_file(self, isolated):
        # A submodule's .git file points straight at a gitdir, not a worktree.
        (isolated / ".git").write_text("gitdir: ../.git/modules/thing\n", encoding="utf-8")
        assert env_store._main_checkout_root() is None

    def test_none_for_unreadable_content(self, isolated):
        (isolated / ".git").write_text("not a pointer\n", encoding="utf-8")
        assert env_store._main_checkout_root() is None


class TestEnvPaths:
    def test_project_then_user(self, isolated):
        assert env_store.env_paths() == [
            isolated / ".env",
            env_store.USER_ENV_PATH,
        ]

    def test_main_checkout_sits_between(self, tmp_path, isolated):
        main = make_worktree(tmp_path, isolated)
        assert env_store.env_paths() == [
            isolated / ".env",
            main / ".env",
            env_store.USER_ENV_PATH,
        ]

    def test_override_wins(self, tmp_path, isolated, monkeypatch):
        override = tmp_path / "elsewhere.env"
        monkeypatch.setenv("GEARCHECK_ENV", str(override))
        assert env_store.env_paths()[0] == override

    def test_no_duplicates(self, isolated, monkeypatch):
        monkeypatch.setenv("GEARCHECK_ENV", str(isolated / ".env"))
        paths = env_store.env_paths()
        assert len(paths) == len({str(p).casefold() for p in paths})


class TestLoad:
    def test_reads_main_checkout_from_a_worktree(self, tmp_path, isolated):
        main = make_worktree(tmp_path, isolated)
        (main / ".env").write_text("ONLY_IN_MAIN=yes\n", encoding="utf-8")
        env_store.load()
        assert os.environ["ONLY_IN_MAIN"] == "yes"

    def test_project_file_beats_main_checkout(self, tmp_path, isolated):
        main = make_worktree(tmp_path, isolated)
        (main / ".env").write_text("GARMIN_EMAIL=main@example.com\n", encoding="utf-8")
        (isolated / ".env").write_text("GARMIN_EMAIL=local@example.com\n", encoding="utf-8")
        env_store.load()
        assert os.environ["GARMIN_EMAIL"] == "local@example.com"

    def test_real_environment_still_wins(self, tmp_path, isolated, monkeypatch):
        (isolated / ".env").write_text("GARMIN_EMAIL=file@example.com\n", encoding="utf-8")
        monkeypatch.setenv("GARMIN_EMAIL", "shell@example.com")
        env_store.load()
        assert os.environ["GARMIN_EMAIL"] == "shell@example.com"

    def test_missing_files_are_fine(self, isolated):
        env_store.load()  # no .env anywhere


class TestActivePathAndWrites:
    def test_defaults_to_project(self, isolated):
        assert env_store.active_path() == isolated / ".env"

    def test_prefers_the_existing_main_checkout_file(self, tmp_path, isolated):
        main = make_worktree(tmp_path, isolated)
        (main / ".env").write_text("A=1\n", encoding="utf-8")
        assert env_store.active_path() == main / ".env"

    def test_writes_land_in_the_main_checkout(self, tmp_path, isolated):
        main = make_worktree(tmp_path, isolated)
        (main / ".env").write_text("A=1\n", encoding="utf-8")
        env_store.set_values({"B": "2"})
        assert "B=2" in (main / ".env").read_text(encoding="utf-8")
        assert not (isolated / ".env").exists()

    def test_preserves_comments_and_updates_in_place(self, isolated):
        (isolated / ".env").write_text("# keep me\nA=1\nB=2\n", encoding="utf-8")
        env_store.set_values({"A": "9"})
        text = (isolated / ".env").read_text(encoding="utf-8")
        assert "# keep me" in text
        assert "A=9" in text and "A=1" not in text
        assert "B=2" in text

    def test_creates_the_file_when_absent(self, isolated):
        env_store.set_values({"A": "1"})
        assert (isolated / ".env").read_text(encoding="utf-8").strip() == "A=1"


class TestMasking:
    def test_mask_shows_the_tail(self):
        assert env_store.mask("abcdefgh") == "****efgh"

    def test_mask_hides_short_values_entirely(self):
        assert env_store.mask("abcd") == "****"

    def test_mask_secret_reveals_nothing(self):
        secret = "hunter2-and-then-some"
        masked = env_store.mask_secret(secret)
        assert secret not in masked
        assert not any(part in masked for part in (secret[-4:], secret[:4]))
        assert str(len(secret)) in masked

    @pytest.mark.parametrize("empty", [None, ""])
    def test_unset(self, empty):
        assert env_store.mask(empty) == "<unset>"
        assert env_store.mask_secret(empty) == "<unset>"
