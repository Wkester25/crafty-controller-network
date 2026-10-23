"""Check for / install updates of this fork from the web UI.

Only works on installs made by proxmox/install.sh: the code is a git checkout
and a systemd path unit (crafty-controller-update.path) runs the installer as
root when the request file appears. Crafty itself runs unprivileged, so all it
does here is read git state and create that file.
"""

import logging
import os
import re
import subprocess
import time
import typing as t

logger = logging.getLogger(__name__)

UPDATE_UNIT = "crafty-controller-update"
SYSTEMD_DIR = "/etc/systemd/system"
DEFAULTS_FILE = f"/etc/default/{UPDATE_UNIT}"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class UpdateError(Exception):
    pass


class ForkUpdater:
    def __init__(self, app_dir: str, systemd_dir: str = SYSTEMD_DIR):
        self.app_dir = app_dir
        crafty_root = os.path.dirname(os.path.abspath(app_dir))
        self.request_path = os.path.join(crafty_root, ".update-request")
        self.log_path = os.path.join(crafty_root, "last-update.log")
        self.systemd_dir = systemd_dir
        self.last_check: t.Optional[dict] = None

    # --------------------------------------------------------------- helpers
    def _run(self, *args: str, timeout: int = 15) -> str:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
        try:
            out = subprocess.run(
                args,
                cwd=self.app_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise UpdateError((e.stderr or e.stdout or str(e)).strip()) from e
        except (OSError, subprocess.TimeoutExpired) as e:
            raise UpdateError(str(e)) from e
        return out.stdout.strip()

    def _git(self, *args: str, timeout: int = 15) -> str:
        return self._run("git", *args, timeout=timeout)

    def unsupported_reason(self) -> t.Optional[str]:
        if not os.path.isdir(os.path.join(self.app_dir, ".git")):
            return "This install is not a git checkout made by proxmox/install.sh."
        if not os.path.isfile(os.path.join(self.systemd_dir, f"{UPDATE_UNIT}.path")):
            return (
                "The update service is not installed. Re-run proxmox/install.sh "
                "once to add it."
            )
        return None

    def branch(self) -> str:
        try:
            with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("BRANCH="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            pass
        try:
            return self._git("rev-parse", "--abbrev-ref", "HEAD")
        except UpdateError:
            return "main"

    def _systemctl_show(self, unit: str, prop: str) -> str:
        try:
            return self._run("systemctl", "show", unit, "-p", prop, "--value")
        except UpdateError:
            return ""

    def update_running(self) -> bool:
        if os.path.exists(self.request_path):
            return True
        state = self._systemctl_show(f"{UPDATE_UNIT}.service", "ActiveState")
        return state in ("activating", "active", "reloading")

    def last_log(self, lines: int = 40) -> t.List[str]:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().splitlines()
        except OSError:
            return []
        return [ANSI_RE.sub("", line) for line in content[-lines:]]

    # ---------------------------------------------------------------- public
    def status(self) -> dict:
        reason = self.unsupported_reason()
        data: t.Dict[str, t.Any] = {"supported": reason is None, "reason": reason}
        try:
            commit, date, subject = self._git(
                "log", "-1", "--format=%h%x09%cI%x09%s"
            ).split("\t", 2)
            data["current"] = {"commit": commit, "date": date, "subject": subject}
        except (UpdateError, ValueError):
            data["current"] = None
        if reason:
            return data

        timer = os.path.isfile(os.path.join(self.systemd_dir, f"{UPDATE_UNIT}.timer"))
        data.update(
            {
                "branch": self.branch(),
                "auto_update": {
                    "enabled": timer,
                    "next_run": (
                        self._systemctl_show(
                            f"{UPDATE_UNIT}.timer", "NextElapseUSecRealtime"
                        )
                        if timer
                        else None
                    ),
                },
                "update_running": self.update_running(),
                "last_log": self.last_log(),
                "last_check": self.last_check,
            }
        )
        return data

    def check(self) -> dict:
        """Fetches the branch and lists commits that are not installed yet."""
        if not os.path.isdir(os.path.join(self.app_dir, ".git")):
            raise UpdateError(self.unsupported_reason())
        branch = self.branch()
        remote_ref = f"refs/remotes/origin/{branch}"
        self._git(
            "fetch", "--quiet", "origin", f"+refs/heads/{branch}:{remote_ref}",
            timeout=60,
        )
        pending = []
        log = self._git(
            "log", "--format=%h%x09%cI%x09%s", "-n", "50", f"HEAD..{remote_ref}"
        )
        for line in log.splitlines():
            commit, date, subject = line.split("\t", 2)
            pending.append({"commit": commit, "date": date, "subject": subject})
        count = int(self._git("rev-list", "--count", f"HEAD..{remote_ref}") or 0)
        self.last_check = {
            "time": time.time(),
            "branch": branch,
            "available": count,
            "latest": self._git("rev-parse", "--short", remote_ref),
            "commits": pending,
        }
        return self.last_check

    def request_update(self) -> None:
        reason = self.unsupported_reason()
        if reason:
            raise UpdateError(reason)
        if self.update_running():
            raise UpdateError("An update is already running.")
        with open(self.request_path, "w", encoding="utf-8") as f:
            f.write(f"requested {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        logger.info("Update requested from the web UI")
