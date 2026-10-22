"""Runs and supervises a local ``cloudflared`` process for a remotely managed
(token based) tunnel."""

import collections
import logging
import os
import platform
import shutil
import stat
import subprocess
import threading
import typing as t

import requests

logger = logging.getLogger(__name__)

RELEASE_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
COMMON_WINDOWS_PATHS = [
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
]


def release_asset_name() -> t.Optional[str]:
    """Name of the official release asset for this OS/arch, if one exists."""
    system = platform.system()
    machine = platform.machine().lower()
    arch = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "arm",
        "i386": "386",
        "i686": "386",
    }.get(machine)
    if not arch:
        return None
    if system == "Windows" and arch in ("amd64", "386"):
        return f"cloudflared-windows-{arch}.exe"
    if system == "Linux":
        return f"cloudflared-linux-{arch}"
    # macOS releases are .tgz archives; use `brew install cloudflared` there
    return None


class CloudflaredProcess:
    def __init__(self, bin_dir: str):
        self.bin_dir = bin_dir
        self.process: t.Optional[subprocess.Popen] = None
        self.log = collections.deque(maxlen=200)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- binary
    @property
    def bundled_path(self) -> str:
        name = "cloudflared.exe" if platform.system() == "Windows" else "cloudflared"
        return os.path.join(self.bin_dir, name)

    def find_binary(self, configured_path: str = "") -> t.Optional[str]:
        candidates = [configured_path, self.bundled_path, shutil.which("cloudflared")]
        if platform.system() == "Windows":
            candidates += COMMON_WINDOWS_PATHS
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        return None

    def download(self) -> str:
        asset = release_asset_name()
        if not asset:
            raise RuntimeError(
                "No prebuilt cloudflared for this platform. Install it manually "
                "and set its path."
            )
        os.makedirs(self.bin_dir, exist_ok=True)
        tmp_path = self.bundled_path + ".download"
        logger.info(f"Downloading cloudflared ({asset})")
        with requests.get(RELEASE_URL + asset, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        os.replace(tmp_path, self.bundled_path)
        if platform.system() != "Windows":
            mode = os.stat(self.bundled_path).st_mode
            os.chmod(self.bundled_path, mode | stat.S_IXUSR | stat.S_IXGRP)
        return self.bundled_path

    def version(self, binary: str) -> str:
        try:
            out = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=_no_window_flag(),
                check=False,
            )
            return (out.stdout or out.stderr).strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError, IndexError) as e:
            return f"unknown ({e})"

    # --------------------------------------------------------------- process
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, binary: str, token: str) -> None:
        with self._lock:
            if self.is_running():
                return
            env = os.environ.copy()
            # Pass the token via env so it does not show up in process listings
            env["TUNNEL_TOKEN"] = token
            cmd = [binary, "tunnel", "--no-autoupdate", "run"]
            self.log.clear()
            self.log.append("Starting cloudflared...")
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_no_window_flag(),
            )
            threading.Thread(
                target=self._reader, args=(self.process,), daemon=True,
                name="cloudflared_reader",
            ).start()
            logger.info(f"cloudflared started with PID {self.process.pid}")

    def _reader(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self.log.append(line)
                logger.debug(f"cloudflared: {line}")
        self.log.append(f"cloudflared exited with code {proc.wait()}")

    def stop(self) -> None:
        with self._lock:
            if not self.is_running():
                self.process = None
                return
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            logger.info("cloudflared stopped")
            self.process = None

    def is_connected(self) -> bool:
        """Best effort: cloudflared logs 'Registered tunnel connection' per edge."""
        if not self.is_running():
            return False
        return any("Registered tunnel connection" in line for line in self.log)


def _no_window_flag() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
