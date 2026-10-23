import os
import subprocess

import pytest

from app.classes.shared import fork_updater as fu


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@x", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def install(tmp_path, monkeypatch):
    """crafty-root/crafty-4 checkout tracking a bare 'GitHub' repo."""
    monkeypatch.setattr(fu, "DEFAULTS_FILE", str(tmp_path / "no-defaults"))
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "checkout", "-q", "-b", "main")
    git(work, "commit", "-q", "--allow-empty", "-m", "first")
    git(work, "push", "-q", "origin", "main")

    app_dir = tmp_path / "crafty" / "crafty-4"
    git(tmp_path, "clone", "-q", "-b", "main", str(origin), str(app_dir))
    systemd = tmp_path / "systemd"
    systemd.mkdir()
    (systemd / f"{fu.UPDATE_UNIT}.path").write_text("")
    updater = fu.ForkUpdater(str(app_dir), systemd_dir=str(systemd))
    # Never touch the real systemd in tests
    monkeypatch.setattr(updater, "_systemctl_show", lambda unit, prop: "")
    return updater, work, systemd


def test_up_to_date(install):
    updater, _, _ = install
    result = updater.check()
    assert result["available"] == 0
    assert result["commits"] == []
    assert result["branch"] == "main"


def test_lists_new_commits(install):
    updater, work, _ = install
    git(work, "commit", "-q", "--allow-empty", "-m", "Merge official Crafty 4.12.0")
    git(work, "commit", "-q", "--allow-empty", "-m", "Fix tunnel restart")
    git(work, "push", "-q", "origin", "main")

    result = updater.check()
    assert result["available"] == 2
    assert [c["subject"] for c in result["commits"]] == [
        "Fix tunnel restart",
        "Merge official Crafty 4.12.0",
    ]
    status = updater.status()
    assert status["supported"] is True
    assert status["current"]["subject"] == "first"
    assert status["last_check"]["available"] == 2


def test_request_update_creates_file_once(install):
    updater, _, _ = install
    updater.request_update()
    assert os.path.exists(updater.request_path)
    assert updater.update_running()
    with pytest.raises(fu.UpdateError, match="already running"):
        updater.request_update()


def test_unsupported_without_path_unit(install):
    updater, _, systemd = install
    os.remove(systemd / f"{fu.UPDATE_UNIT}.path")
    status = updater.status()
    assert status["supported"] is False
    assert "install.sh" in status["reason"]
    with pytest.raises(fu.UpdateError):
        updater.request_update()
    assert not os.path.exists(updater.request_path)


def test_unsupported_without_git(tmp_path):
    app_dir = tmp_path / "crafty" / "crafty-4"
    app_dir.mkdir(parents=True)
    updater = fu.ForkUpdater(str(app_dir), systemd_dir=str(tmp_path))
    assert updater.status()["supported"] is False
    with pytest.raises(fu.UpdateError):
        updater.check()


def test_log_strips_colours(install):
    updater, _, _ = install
    with open(updater.log_path, "w", encoding="utf-8") as f:
        f.write("\x1b[1;34m==>\x1b[0m Already up to date (abc first)\n")
    assert updater.last_log() == ["==> Already up to date (abc first)"]


def test_auto_update_reported_from_timer(install):
    updater, _, systemd = install
    assert updater.status()["auto_update"]["enabled"] is False
    (systemd / f"{fu.UPDATE_UNIT}.timer").write_text("")
    assert updater.status()["auto_update"]["enabled"] is True
