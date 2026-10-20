import os
import uuid
import time
import pathlib
import logging
import threading
from pathlib import Path

from app.classes.big_bucket.bigbucket import BigBucket
from app.classes.controllers.servers_controller import ServersController
from app.classes.helpers.helpers import Helpers
from app.classes.helpers.file_helpers import FileHelpers
from app.classes.shared.websocket_manager import WebSocketManager
from app.classes.steamcmd.steamcmd import SteamCMD
from app.classes.installers.modded import ModdedInstaller
from app.classes.installers.hytale import (
    FAILURE_TRANSLATION_KEYS,
    HytaleInstaller,
    InstallerResult,
)
from app.classes.models.server_permissions import PermissionsServers
from app.classes.models.users import HelperUsers

logger = logging.getLogger(__name__)

HYTALE_0UTPUT_NAME = "hytale.zip"


class ImportHelpers:
    allowed_quotes = ['"', "'", "`"]

    def __init__(self, helper, file_helper):
        self.file_helper: FileHelpers = file_helper
        self.helper: Helpers = helper
        self.big_bucket = BigBucket(helper)
        self.modded_installer = ModdedInstaller(
            self.helper, ServersController, WebSocketManager
        )
        self.hytale_installer = HytaleInstaller(
            self.helper, self.file_helper, WebSocketManager
        )

    def import_zipped_server(
        self,
        archive_path,
        new_server_dir,
        base_include_path,
        port,
        new_id,
        server_type,
        full_exe_path=None,
    ):
        import_thread = threading.Thread(
            target=self.import_threaded_zipped_server,
            daemon=True,
            args=(
                archive_path,
                new_server_dir,
                base_include_path,
                port,
                new_id,
                server_type,
                full_exe_path,
            ),
            name=f"{new_id}_import",
        )
        import_thread.start()

    def import_threaded_zipped_server(
        self,
        archive_path,
        new_server_dir,
        base_include_path,
        port,
        new_id,
        server_type,
        full_exe_path,
    ):
        try:
            self.file_helper.unzip_file(
                archive_path,
                new_server_dir,
                new_id,
                False,
                base_include_path=base_include_path,
            )
        except OSError as why:
            logger.exception(f"Error unzipping file: {why}")

        time.sleep(2)
        if (
            not self.helper.is_os_windows() and full_exe_path
        ):  # we only expect full jar path for bedrock
            if Helpers.check_file_exists(full_exe_path):
                os.chmod(full_exe_path, 0o2760)  # apply execute permissions

        self.file_helper.del_file(archive_path)

        has_properties = False
        for item in os.listdir(new_server_dir):
            if str(item) == "server.properties":
                has_properties = True
        if not has_properties and "minecraft" in server_type:
            logger.info(
                f"No server.properties found on zip file import. "
                f"Creating one with port selection of {str(port)}"
            )
            with open(
                os.path.join(new_server_dir, "server.properties"), "w", encoding="utf-8"
            ) as file:
                file.write(f"server-port={port}")
                file.close()
        time.sleep(5)
        ServersController.finish_import(new_id)
        WebSocketManager().broadcast_to_server_users(new_id, "send_start_reload", {})

    def download_steam_server(self, app_id, server_id, server_dir, server_exe):
        download_thread = threading.Thread(
            target=self._create_steam_server,
            daemon=True,
            args=(app_id, server_id, server_dir, server_exe),
            name=f"{server_id}_download",
        )
        download_thread.start()

    def _create_steam_server(self, app_id, server_id, server_dir, server_exe):
        if not server_exe:
            server_exe = "game.exe"  # replace with actual exe eventually

        # Initiate SteamCMD & game installing status.
        ServersController.set_import(server_id)

        # Set our storage locations
        steamcmd_path = os.path.join(server_dir, "steamcmd_files")
        gamefiles_path = os.path.join(server_dir, "gameserver_files")

        # Ensure game and steam directories exist in server directory.
        self.helper.ensure_dir_exists(steamcmd_path)
        self.helper.ensure_dir_exists(gamefiles_path)

        # Initialize SteamCMD
        self.steam = SteamCMD(steamcmd_path)

        # Install SteamCMD for managing game server files.
        self.steam.install()

        # Install the game server files.
        self.steam.app_update(app_id, gamefiles_path)

        # Set the server execuion command. TODO brainstorm how to approach.
        full_exe_path = os.path.join(steamcmd_path, server_exe)
        if Helpers.is_os_windows():
            server_command = f'"{full_exe_path}"'
        else:
            server_command = f"./{server_exe}"
        logger.debug("command: " + server_command)

        # Finalise SteamCMD & game installing status.
        ServersController.finish_import(server_id)
        WebSocketManager().broadcast_to_server_users(server_id, "send_start_reload", {})

    def download_threaded_bedrock_server(self, path, new_id):
        bedrock_url = Helpers.get_latest_bedrock_url()
        download_thread = threading.Thread(
            target=self._download_bedrock_server,
            daemon=True,
            args=(path, new_id, bedrock_url),
            name=f"{new_id}_download",
        )
        download_thread.start()

    def _download_bedrock_server(self, path, new_id, bedrock_url, server_update=False):
        """
        Downloads the latest Bedrock server, unzips it, sets necessary permissions.

        Parameters:
            path (str): The directory path to download and unzip the Bedrock server.
            new_id (str): The identifier for the new server import operation.

        This method handles exceptions and logs errors for each step of the process.
        """
        try:
            if bedrock_url:
                file_path = os.path.join(path, "bedrock_server.zip")
                success = self.file_helper.ssl_get_file(
                    bedrock_url, path, "bedrock_server.zip"
                )
                if not success:
                    logger.error("Failed to download the Bedrock server zip.")
                    ServersController.finish_import(new_id)
                    WebSocketManager().broadcast_to_server_users(
                        new_id,
                        "send_error",
                        {"error": "Failed to download the Bedrock server zip."},
                    )
                    return

                unzip_path = self.helper.wtol_path(file_path)
                destination_path = pathlib.Path(unzip_path).parents[0]
                # unzips archive that was downloaded.
                try:
                    self.file_helper.unzip_file(
                        unzip_path,
                        destination_path,
                        new_id,
                        server_update=server_update,
                    )
                except OSError as why:
                    logger.exception(f"Error unzipping file: {why}")
                # adjusts permissions for execution if os is not windows

                if not self.helper.is_os_windows():
                    os.chmod(os.path.join(path, "bedrock_server"), 0o0744)

                # we'll delete the zip we downloaded now
                os.remove(file_path)
            else:
                logger.error("Bedrock download URL issue!")
        except Exception as e:
            logger.critical(
                f"Failed to download bedrock executable during server creation! \n{e}"
            )
            raise e

        ServersController.finish_import(new_id)
        WebSocketManager().broadcast_to_server_users(new_id, "send_start_reload", {})

    def download_install_threaded_hytale(self, path, new_id):
        download_thread = threading.Thread(
            target=self._download_install_hytale,
            daemon=True,
            args=(path, new_id),
            name=f"{new_id}_download",
        )
        download_thread.start()

    def _download_install_hytale(self, server_path: str | Path, new_id: uuid.UUID):
        """Creates a Hytale server by downloading and running the installer.

        Runs on a daemon thread. Any failure is logged and surfaced to the user,
        and the import status is always cleared so the server does not stay stuck
        in the "Importing..." state with no feedback.

        Args:
            server_path (str | Path): filesystem location of the server data
            new_id (uuid.UUID): Crafty ID for the server
        """
        result = self.run_hytale_install(server_path, new_id)
        if result is InstallerResult.SUCCESS:
            WebSocketManager().broadcast_to_server_users(
                new_id, "send_start_reload", {}
            )
            return
        for user_id in PermissionsServers.get_server_user_list(new_id):
            WebSocketManager().broadcast_user(
                user_id,
                "send_error",
                {
                    "error": self.helper.translation.translate(
                        "error",
                        FAILURE_TRANSLATION_KEYS[result],
                        HelperUsers.get_user_lang_by_id(user_id),
                    )
                },
            )

    def run_hytale_install(
        self, server_path: str | Path, server_id: uuid.UUID
    ) -> InstallerResult:
        """Runs the Hytale installer silently and clears the import status.

        Args:
            server_path (str | Path): filesystem location of the server data
            server_id (uuid.UUID): Crafty ID for the server

        Returns:
            InstallerResult: SUCCESS, or the reason the install failed.
        """
        bb_cache = self.big_bucket.get_bucket_data(self.helper.big_bucket_hytale_cache)
        try:
            result = self.hytale_installer.install(bb_cache, server_path, server_id)
        except Exception:
            logger.exception("Failed to install Hytale server %s", server_id)
            result = InstallerResult.FAILED
        ServersController.finish_import(server_id)
        return result

    def download_threaded_exe(self, jar, server, version, path, server_id):
        update_thread = threading.Thread(
            name=f"server_download-{server_id}-{server}-{version}",
            target=self._download_exe,
            daemon=True,
            args=(jar, server, version, path, server_id),
        )
        update_thread.start()

    def _download_exe(self, jar, server, version, path, server_id):
        """
        Downloads a server JAR file and performs post-download actions including
        notifying users and setting import status.

        This method waits for the server registration to complete, retrieves the
        download URL for the specified server JAR file.

        Upon successful download, it either runs the installer for
        Forge servers or simply finishes the import process for other types. It
        notifies server users about the completion of the download.

        Parameters:
            - jar (str): The category of the JAR file to download.
            - server (str): The type of server software (e.g., 'forge', 'paper').
            - version (str): The version of the server software.
            - path (str): The local filesystem path where the JAR file will be saved.
            - server_id (str): The unique identifier for the server being updated or
                imported, used for notifying users and setting the import status.

        Returns:
            - bool: True if the JAR file was successfully downloaded and saved;
                False otherwise.

        The method ensures that the server is properly registered before proceeding
        with the download and handles exceptions by logging errors and reverting
        the import status if necessary.
        """
        # delaying download for server register to finish
        time.sleep(3)

        fetch_url = self.big_bucket.get_fetch_url(jar, server, version)
        if not fetch_url:
            return False

        # Make sure the server is registered before updating its stats
        while True:
            try:
                ServersController.set_import(server_id)
                WebSocketManager().broadcast_to_server_users(
                    server_id, "send_start_reload", {}
                )
                break
            except Exception as ex:
                logger.debug(f"Server not registered yet. Delaying download - {ex}")

        # Initiate Download
        jar_dir = os.path.dirname(path)
        jar_name = os.path.basename(path)
        logger.info(fetch_url)
        success = self.file_helper.ssl_get_file(fetch_url, jar_dir, jar_name)

        ServersController.finish_import(server_id)
        # Post-download actions
        if success:
            match server:
                case "forge-installer" | "neoforge-installer":
                    server_obj = ServersController.get_server_obj(server_id)
                    # If this is the newer Forge version, run the installer
                    self.modded_installer.install(jar_dir, server_id, server_obj)

            # Notify users
            WebSocketManager().broadcast_to_server_users(
                server_id, "notification", "Executable download finished"
            )
            time.sleep(3)  # Delay for user notification
            WebSocketManager().broadcast_to_server_users(
                server_id, "send_start_reload", {}
            )
        else:
            logger.error(f"Unable to save jar to {path} due to download failure.")

        return success
