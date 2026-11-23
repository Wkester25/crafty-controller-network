import logging
import re
import uuid
from pathlib import Path

import requests

from app.classes.installers.hytale import InstallerResult
from app.classes.steamcmd.steamcmd import SteamCMD

logger = logging.getLogger(__name__)


class UpdateManager:
    def __init__(self, import_helper, helper, file_helper):
        self.import_helper = import_helper
        self.helper = helper
        self.file_helper = file_helper
        self.update_available = False

    def update_hytale(self, server_path: Path, server_id: uuid.UUID) -> InstallerResult:
        """Updates a Hytale server to the latest version.

        Args:
            server_path (Path): filesystem location of the server data
            server_id (uuid.UUID): Crafty ID for the server

        Returns:
            InstallerResult: SUCCESS, or the reason the update failed.
        """
        return self.import_helper.run_hytale_install(server_path, server_id)

    def update_mc_java(self, current_executable: Path, update_url: str) -> bool:
        jar_dir = Path(current_executable).parent
        jar_file_name = Path(current_executable).name

        return self.file_helper.ssl_get_file(update_url, jar_dir, jar_file_name)

    def update_steam_cmd(self, server_path: Path):
        try:
            # Set our storage locations
            steamcmd_path = Path(server_path, "steamcmd_files")
            gamefiles_path = Path(server_path, "gameserver_files")
            app_id = SteamCMD.find_app_id(gamefiles_path)

            # Ensure game and steam directories exist in server directory.
            self.helper.ensure_dir_exists(steamcmd_path)
            self.helper.ensure_dir_exists(gamefiles_path)

            # Set the SteamCMD install directory for next install.
            self.steam = SteamCMD(steamcmd_path)

            # Install the game server files.
            self.steam.app_update(app_id, gamefiles_path, validate=True)
            downloaded = True
        except ValueError as e:
            logger.critical(
                f"Failed to update SteamCMD Server \n App ID find failed: \n{e}"
            )
            downloaded = False
        except Exception as e:
            logger.critical(f"Failed to update SteamCMD Server \n{e}")
            downloaded = False
        return downloaded

    def update_mc_bedrock(self, server_path: Path, server_id: uuid.UUID):
        # downloads zip from remote url
        downloaded = False
        try:
            bedrock_url = self.helper.get_latest_bedrock_url()
            if bedrock_url:
                # Use the new method for secure download
                self.import_helper._download_bedrock_server(
                    server_path, server_id, bedrock_url, True
                )
                downloaded = True
        except Exception as e:
            logger.critical(f"Failed to download bedrock executable for update \n{e}")
        return downloaded

    def check_server_version(self, settings: dict):
        if not settings.get("update_watcher"):
            logger.debug("User has update watcher turned off. Killing out of function")
            self.update_available = False
            return
        current_hash = self.helper.crypto_helper.calculate_file_hash_sha256(
            str(
                Path(
                    str(settings.get("path")),
                    str(settings.get("executable")),
                )
            )
        )
        url_pattern = (
            r"^https:\/\/"
            r"(www\.)?"
            r"([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}"
            r"(\/[a-zA-Z0-9-._~:/?#\[\]@!$&'()*+,;=]*)?$"
        )
        try:  # Get hash from Big Bucket remote
            if re.match(
                url_pattern,
                str(settings.get("executable_update_url")),
            ):
                response = requests.get(
                    f"{settings.get('executable_update_url')}.sha256", timeout=1
                )
            else:
                self.update_available = False
                return logger.error(
                    "Server version check failed. Invalid url: %s",
                    settings.get("executable_update_url"),
                )
        except (TimeoutError, requests.exceptions.RequestException) as why:
            self.update_available = False
            return logger.exception(
                "Could not capture remote URL hash with error %s", why
            )
        remote_hash = None
        match response.status_code:
            case 200:
                remote_hash = response.text
            case 404:
                self.update_available = False
                return

        if remote_hash != current_hash:  # Compare hashes
            self.update_available = True
        else:
            self.update_available = False
