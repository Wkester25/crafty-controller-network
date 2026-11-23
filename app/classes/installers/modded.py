import os
import re
import glob
import shlex
import logging
import subprocess
from pathlib import Path

from app.classes.helpers.helpers import TERMINAL_ANSI_ARGS
from app.classes.models.server_permissions import EnumPermissionsServer

logger = logging.getLogger(__name__)


class ModdedInstaller:
    def __init__(self, helper, servers_controller, websocket_manager):
        self.helper = helper
        self.servers_controller = servers_controller
        self.websocket_manager = websocket_manager

    def install(self, server_path: str | Path, new_id, server_obj):
        self._run_installer(server_obj, server_path, new_id)

        try:
            parsed = self._parse_version(server_obj.execution_command)
            if not parsed:
                return

            loader, major, minor, sub, version_str = parsed

            if self._is_old_version(major, minor):
                self._handle_old(server_obj, loader, version_str)

            elif self._is_mid_version(major, minor, sub, loader):
                if not self._handle_mid(server_obj, loader):
                    return

            else:
                self._handle_new(server_obj, version_str)

        except Exception:
            logger.debug("Could not configure Forge server.", exc_info=True)

    def _run_installer(self, server_obj, server_path, new_id):
        install_command = shlex.split(server_obj.execution_command)
        process = subprocess.Popen(
            install_command,
            cwd=server_path,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        while process.poll() is None:
            line = process.stdout.readline().strip()
            if not line:
                continue

            if self.websocket_manager().clients:
                self.websocket_manager().broadcast_page_params(
                    "/panel/server_detail",
                    {"id": new_id},
                    "vterm_new_line",
                    {"line": line + "<br />"},
                    required_permission=EnumPermissionsServer.TERMINAL,
                )

    def _parse_version(self, command: str):
        match = re.search(
            r"(forge|neoforge)-installer-([0-9\.]+)(?:-([0-9\.]+)-[a-zA-Z]+)?\.jar",
            command,
        )
        if not match:
            return None

        loader, version_str, _ = match.groups()

        parts = [int(p) for p in version_str.split(".")]
        major = parts[0]
        minor = parts[1] if len(parts) > 1 else 0
        sub = parts[2] if len(parts) > 2 else 0

        return loader, major, minor, sub, version_str

    def _is_old_version(self, major, minor):
        return major <= 1 and minor < 17

    def _is_mid_version(self, major, minor, sub, loader):
        return (major <= 1 and minor <= 20 and sub < 3) or loader == "neoforge"

    def _get_memory(self, command: str):
        match = re.search(r"-Xms([A-Z0-9\.]+) -Xmx([A-Z0-9\.]+)", command)
        return match.groups() if match else ("1G", "1G")

    def _handle_old(self, server_obj, loader, version):
        file_path = glob.glob(f"{server_obj.path}/{loader}-{version}*.jar")[0]
        file_name = re.search(r"(forge[-0-9.]+\.jar)", file_path).group(1)

        server_obj.executable = file_name

        xms, xmx = self._get_memory(server_obj.execution_command)

        server_obj.execution_command = (
            f"java {TERMINAL_ANSI_ARGS} -Xms{xms} -Xmx{xmx} "
            f'-jar "{file_name}" nogui'
        )
        self.servers_controller.update_server(server_obj)

    def _handle_mid(self, server_obj, loader):
        run_file = "run.bat" if self.helper.is_os_windows() else "run.sh"
        run_path = os.path.join(server_obj.path, run_file)

        if not os.path.isfile(run_path):
            logger.error("Forge install can't read script files.")
            return False

        try:
            with open(run_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            logger.exception("Failed to read Forge run script.")
            return False

        match = re.search(
            r"java @([a-zA-Z0-9_\.]+) @([a-z./\-]+)"
            r"([0-9.\-]+(?:-[a-zA-Z0-9]+)?)/\b([a-z_0-9]+\.txt)\b( .{2,4})?",
            text,
        )

        if not match:
            return False

        arg1, path, version, txt, extra = match.groups()
        exec_path = f"{path}{version}/"

        server_obj.executable = f"{exec_path}{loader}-{version}-server.jar"

        server_obj.execution_command = (
            f"java {TERMINAL_ANSI_ARGS} @{arg1} @{exec_path}{txt} nogui {extra or ''}"
        )
        self.servers_controller.update_server(server_obj)

        return True

    def _handle_new(self, server_obj, version):
        file_path = glob.glob(f"{server_obj.path}/forge-{version}*.jar")[0]
        file_name = re.search(r"(forge-[\-0-9.]+-shim\.jar)", file_path).group(1)

        server_obj.executable = file_name

        xms, xmx = self._get_memory(server_obj.execution_command)

        server_obj.execution_command = (
            f"java {TERMINAL_ANSI_ARGS} -Xms{xms} -Xmx{xmx} "
            f'-jar "{file_name}" nogui'
        )
        self.servers_controller.update_server(server_obj)
