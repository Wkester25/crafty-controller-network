import datetime
import logging
import mimetypes
import os
import pathlib
import re
import shutil
import ssl
import time
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from urllib.error import URLError
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

import certifi
from urllib3.util import SSLContext

from app.classes.helpers.cryptography_helper import CryptoHelper
from app.classes.helpers.helpers import Helpers
from app.classes.models.server_permissions import PermissionsServers
from app.classes.shared.websocket_manager import WebSocketManager

if TYPE_CHECKING:
    import io

logger = logging.getLogger(__name__)

mimetypes.init(files=[])
BACKING_UP_FILE_STR = "backing up file"
ERROR_BACKING_UP_FILE_STR = "Error backing up file"
FILE_PATH = "file path"
SERVER_DETAIL = "/panel/server_detail"
PLAIN_TEXT = "text/plain"
BLAKE3_HASH_LENGTH_BYTES = 128


class SnapshotFileTypes(Enum):
    """The types of files that need to be restored by the snapshot backups."""

    FILES = "files"
    CHUNKS = "chunks"


@dataclass(frozen=True)
class BackupPercentageBroadcast:
    """Give names and types to the backup progress websocket broadcast."""

    id: str | None
    percent: int
    complete: bool

    def as_dict(self) -> dict[str, str | int | bool | None]:
        """Output the BackupPercentageBroadcast as a dict."""
        return {"id": self.id, "percent": self.percent, "complete": self.complete}


# Ruff does not like the use of a boolean as a parameter. This is a warning if in the
# case of "True" or "False" being unclear. For this, "use_compression" is fairly
# explicit when saying "True", or "False". So we are disabling the lint check for FBT001
# in all such cases. see:
# https://docs.astral.sh/ruff/rules/boolean-type-hint-positional-argument/


class FileHelpers:
    """File operation and backup/restore functionality."""

    allowed_quotes: ClassVar[list[str]] = ['"', "'", "`"]
    BYTE_TRUE: bytes = bytes.fromhex("01")
    BYTE_FALSE: bytes = bytes.fromhex("00")
    SNAPSHOT_BACKUP_DATE_FORMAT_STRING: str = "%Y-%m-%d-%H-%M-%S"
    UNZIP_IGNORED_NAMES: ClassVar[list[str]] = [
        "server.properties",
        "permissions.json",
        "allowlist.json",
    ]

    def __init__(self, helper: Helpers) -> None:
        """Create a new filehelper."""
        self.helper: Helpers = helper
        self.add_mime_types()  # Add to account for yml, conf, properties, etc
        self.text_mime_prefixes = [
            "text/",
            "application/json",
            "application/xml",
            "application/javascript",
            "text/x-shellscript",
            "application/x-shellscript",
            "text/x-sh",
            "application/x-sh",
            "text/x-bat",
            "application/x-bat",
            "text/x-log",
            "text/plain",
            "text/html",
            "text/python",
            "text/skript",
            "text/css",
        ]

    def add_mime_types(self) -> None:
        """
        Add various mimetypes.

        Adding mimetypes to mime object when comparing file types in can_open
        """
        # Extend the default list
        mimetypes.add_type("text/yaml", ".yml")
        mimetypes.add_type("text/yaml", ".yaml")
        mimetypes.add_type("text/toml", ".toml")
        mimetypes.add_type(PLAIN_TEXT, ".ini")
        mimetypes.add_type(PLAIN_TEXT, ".conf")
        mimetypes.add_type(PLAIN_TEXT, ".properties")
        mimetypes.add_type(PLAIN_TEXT, ".prop")
        mimetypes.add_type(PLAIN_TEXT, ".env")
        mimetypes.add_type("application/x-bat", ".ps1")
        mimetypes.add_type("text/x-log", ".log")
        mimetypes.add_type("text/html", ".html")
        mimetypes.add_type(PLAIN_TEXT, ".txt")

    def write_text_file(self, path: Path, text: str):
        """Creates a text file at the path with the text

        Args:
            path (Path): Path for the file
            text (str): Text for the file
        """
        with open(path, "w", encoding="utf-8") as target:
            target.write(text)

    def probably_can_open_file(self, path: str) -> tuple:
        """
        Check various file factors to assume it can be read by the text editor.

        This is very computationally expensive and we should probably kill this.
        There are also some TOCTOU type issues. We should most likely let users try to
        open any kind of file and just tell the user it can't be opened on open rather
        than check beforehand.

        Args:
            path: Path to the file to check.

        """
        if Path(path).is_dir():
            return (False, None)
        mime = mimetypes.guess_type(path)
        return (mime[0] in self.text_mime_prefixes, mime[0])

    def ssl_get_file(
        self,
        url: str,
        out_path: str,
        out_file: str,
        headers: dict[str, str] | None = None,
    ) -> bool:
        """
        Download a file from a given URL.

        Uses HTTPS with SSL context verification, retries with exponential backoff and
        providing download progress feedback.

        Parameters
        ----------
            - url (str): The URL of the file to download. Must start with "https".
            - out_path (str): The local path where the file will be saved.
            - out_file (str): The name of the file to save the downloaded content as.
            - headers (dict, optional):
                A dictionary of HTTP headers to send with the request.

        Returns
        -------
            - bool: True if the download was successful, False otherwise.

        Raises
        ------
            - urllib.error.URLError: If a URL error occurs during the download.
            - ssl.SSLError: If an SSL error occurs during the download.
        Exception: If an unexpected error occurs during the download.

        Note:
        This method logs critical errors and download progress information.
        Ensure that the logger is properly configured to capture this information.

        """
        # Download settings. These are not changed by any call site in Crafty so taking
        # them out of function signature to reduce the number of parameters.
        max_retries = 3
        backoff_factor = 2

        if not url.lower().startswith("https"):
            logger.error("SSL File Get - Error: URL must start with https.")
            return False

        ssl_context = ssl.create_default_context(cafile=certifi.where())
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

        if not headers:
            headers = {
                "User-Agent": f"CraftyController/{self.helper.get_version_string()}",
                "Accept-Encoding": "gzip, delflate, br",
                "Connection": "keep-alive",
                "Accept": "*/*",
                "Cache-Control": "no-cache",
            }

        # Prevent file:// or ftp:// links from being opened. Urllib will happily open
        # files, and FTP given a link path.
        if not url.startswith(("http:", "https:")):
            return False

        # Check to mitigate S310 is done just above.
        req = urllib.request.Request(url, headers=headers)  # noqa: S310

        write_path = Path(out_path, out_file)
        attempt = 0

        logger.info("SSL File Get - Requesting remote", extra={"url": url})
        file_path_full = Path(out_path, out_file)
        logger.info(
            "SSL File Get - Download Destination",
            extra={"full file path": file_path_full},
        )

        while attempt < max_retries:
            try:
                return self._ssl_get_file_single_shot(req, ssl_context, write_path)
            # The noqa here is for try/except inside of a loop. Not performance critical
            # so blocking that inspection.
            except (  # noqa: PERF203
                URLError,
                ssl.SSLError,
            ) as e:
                attempt += 1
                logger.warning(
                    "SSL File Get",
                    extra={"attempt": attempt, "failed": e},
                )
                time.sleep(backoff_factor**attempt)
            # I do not think an error other than URLError or ssl.SSLError is possible
            # here but leaving this bare Except.
            except Exception as e:
                logger.critical("SSL File Get - Unexpected error", extra={"error": e})
                return False

        logger.error("SSL File Get - Maximum retries reached. Download failed.")
        return False

    @staticmethod
    def _ssl_get_file_single_shot(
        req: urllib.request.Request,
        ssl_context: SSLContext,
        write_path: Path,
    ) -> bool:
        """
        Single download attempt for ssl_file_get.

        This should not be used by itself. Use ssl_get_file instead

        Args:
            req: the request to be made.
            ssl_context: the ssl context for this download.
            write_path: Where the file should be written to.

        """
        # Validation of URL is done in ssl_get_file when the req is created. Safe to
        # disable the inspection here.
        with urllib.request.urlopen(req, context=ssl_context) as response:  # noqa: S310
            total_size = response.getheader("Content-Length")
            if total_size:
                total_size = int(total_size)
            downloaded = 0
            with write_path.open("wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)  # 1 MB
                    if not chunk:
                        break
                    file.write(chunk)
                    downloaded += len(chunk)
                    if total_size:
                        progress = (downloaded / total_size) * 100
                        logger.info(
                            "SSL File Get",
                            extra={"Download Progress": f"{progress:.2f}%"},
                        )
                        WebSocketManager().broadcast_page(
                            SERVER_DETAIL,
                            "download_progress",
                            round(progress, 1),
                        )
            return True

    @staticmethod
    def del_dirs(path: str | Path) -> bool:
        """
        Delete target directory.

        Args:
            path: Path to delete.

        """
        target_path = Path(path)
        clean = True
        for sub in target_path.iterdir():
            if sub.is_dir():
                # Delete folder if it is a folder
                FileHelpers.del_dirs(sub)
            else:
                # Delete file if it is a file:
                try:
                    sub.unlink()
                except Exception as e:
                    clean = False
                    logger.exception(
                        "Unable to delete file",
                        extra={"file": sub, "error": e},
                    )
        try:
            # This removes the top-level folder:
            target_path.rmdir()
        except Exception:
            logger.exception("Unable to remove top level")
            return False
        return clean

    @staticmethod
    def del_file(path: str | Path) -> bool:
        """
        Delete a target file.

        Args:
            path: the path to the file to delete

        """
        file_path = Path(path)
        logger.debug("Deleting file", extra={FILE_PATH: file_path})
        try:
            # Remove the file
            file_path.unlink()
        except OSError as why:
            logger.exception(
                "Unable to delete file",
                extra={FILE_PATH: file_path, "error": why},
            )
            return False
        return True

    def check_mime_types(self, file_path: Path) -> str | None:
        """
        Attempt to get a file's mime type.

        Args:
        file_path: Path to target file.

        """
        m_type, _value = mimetypes.guess_type(file_path)
        return m_type

    @staticmethod
    def copy_dir(
        src_path: str,
        dest_path: str,
        dirs_exist_ok: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """
        Copy a directory using shutil copytree.

        Args:
            src_path: Source path.
            dest_path: Destination path.
            dirs_exist_ok: Allows target dirs to exist, default False.

        Raises:
            OSError: If there is an error copying directories of if files exist.

        """
        # pylint: disable=unexpected-keyword-arg
        shutil.copytree(src_path, dest_path, dirs_exist_ok=dirs_exist_ok)

    @staticmethod
    def copy_file(src_path: str, dest_path: str) -> None:
        """
        Copy a file with shutil copy.

        Args:
            src_path: Source path.
            dest_path: Destination path.

        Raises:
            OSError: If there is any error copying the file.

        """
        shutil.copy(src_path, dest_path)

    @staticmethod
    def move_dir(src_path: str, dest_path: str) -> None:
        """
        Move a directory using shutil move.

        Args:
            src_path: Source path.
            dest_path: Destination path.

        Raises:
            OSError: If there is an error moving directory.

        """
        shutil.move(src_path, dest_path)

    @staticmethod
    def move_dir_exist(src_path: str, dest_path: str) -> None:
        """
        Move dir with target dirs already present.

        This function also deletes the source.

        Args:
            src_path: Source path.
            dest_path: Destination path.

        Raises:
            OSError: If there is an error copying the directory or deleting the source

        """
        FileHelpers.copy_dir(src_path, dest_path, dirs_exist_ok=True)
        FileHelpers.del_dirs(src_path)

    @staticmethod
    def move_file(src_path: str, dest_path: str) -> None:
        """
        Move a file with shutil move.

        Args:
            src_path: Source path
            dest_path: Destination path

        Raises:
            OSError: If there is an error moving the file.

        """
        shutil.move(src_path, dest_path)

    @staticmethod
    def make_archive(
        path_to_destination: Path,
        path_to_zip: Path,
        comment: str = "",
    ) -> bool:
        """
        Make a zip archive without compression.

        Extremely duplicated code with make_compressed_archive. These should be
        combined, we will need to look at the call sites.

        Args:
            path_to_destination: Where the zip should be saved to. .zip suffix not
                required.
            path_to_zip: The path to zip up.
            comment: Comment to be added to zip file.

        """
        if path_to_destination.suffix != ".zip":
            path_to_destination = path_to_destination.with_suffix(".zip")

        # Create zip file
        with ZipFile(path_to_destination, "w") as zip_file:
            # Add comment to zip file
            zip_file.comment = bytes(
                comment,
                "utf-8",
            )  # comments over 65535 bytes will be truncated

            for path in path_to_zip.rglob("*"):
                # Skip directories
                if path.is_dir():
                    continue

                try:
                    logger.info(BACKING_UP_FILE_STR, extra={FILE_PATH: path})
                    zip_file.write(path, path.relative_to(path_to_zip))
                # This set of errors should be everything that can be thrown here from
                # my research.
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    zipfile.error,
                ) as why:
                    logger.warning(
                        ERROR_BACKING_UP_FILE_STR,
                        extra={FILE_PATH: path, "error": why},
                    )

        return True

    @staticmethod
    def make_compressed_archive(
        path_to_destination: Path,
        path_to_zip: Path,
        comment: str = "",
    ) -> bool:
        """
        Make a zip archive with compression.

        Uses ZIP DEFLATED compression mode.

        Args:
            path_to_destination: Where the zip should be saved to. .zip suffix not
                required.
            path_to_zip: The path to zip up.
            comment: Comment to be added to zip file.

        """
        if path_to_destination.suffix != ".zip":
            path_to_destination = path_to_destination.with_suffix(".zip")

        # Create zip file
        with ZipFile(
            path_to_destination,
            mode="w",
            compression=ZIP_DEFLATED,
        ) as zip_file:
            # Add comment to zip file
            zip_file.comment = bytes(
                comment,
                "utf-8",
            )  # comments over 65535 bytes will be truncated

            for path in path_to_zip.rglob("*"):
                # Skip directories
                if path.is_dir():
                    continue

                try:
                    logger.info(BACKING_UP_FILE_STR, extra={FILE_PATH: path})
                    zip_file.write(path, path.relative_to(path_to_zip))
                # This set of errors should be everything that can be thrown here from
                # my research.
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    zipfile.error,
                ) as why:
                    logger.warning(
                        ERROR_BACKING_UP_FILE_STR,
                        extra={FILE_PATH: path, "error": why},
                    )

        return True

    def make_backup(  # pylint: disable=too-many-positional-arguments
        self,
        path_to_destination: str,
        path_to_zip,
        excluded_dirs: list[str],
        server_id,
        backup_id,
        comment="",
        compressed=None,
    ):
        # create a ZipFile object
        path_to_destination += ".zip"
        ex_replace: list[Path] = [
            Path(self.get_absolute_path(path_to_zip, p)).resolve().as_posix()
            for p in excluded_dirs
        ]
        path_to_zip: Path = Path(path_to_zip)

        total_bytes = 0
        dir_bytes = FileHelpers.get_dir_size(str(path_to_zip))
        results = {
            "percent": 0,
            "total_files": self.helper.human_readable_file_size(dir_bytes),
        }
        WebSocketManager().broadcast_page_params(
            SERVER_DETAIL,
            {"id": str(server_id)},
            "backup_status",
            results,
        )
        WebSocketManager().broadcast_page_params(
            "/panel/edit_backup",
            {"id": str(server_id)},
            "backup_status",
            results,
        )
        # Set the compression mode based on the `compressed` parameter
        compression_mode = ZIP_DEFLATED if compressed else ZIP_STORED
        with ZipFile(path_to_destination, "w", compression_mode) as zip_file:
            zip_file.comment = bytes(
                comment,
                "utf-8",
            )  # comments over 65535 bytes will be truncated
            for file in path_to_zip.rglob("*"):
                is_excluded = any(
                    file.is_relative_to(excluded_path) for excluded_path in ex_replace
                )
                if is_excluded or file.name == "crafty.sqlite" or file.is_dir():
                    continue

                try:
                    logger.info(BACKING_UP_FILE_STR, extra={FILE_PATH: file})
                    zip_file.write(file, file.relative_to(path_to_zip))
                # This set of errors should be everything that can be thrown here from
                # my research.
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    zipfile.error,
                ) as why:
                    logger.warning(
                        ERROR_BACKING_UP_FILE_STR,
                        extra={FILE_PATH: file, "error": why},
                    )

                try:
                    # add current file bytes to total bytes.
                    total_bytes += file.stat().st_size
                except OSError as why:
                    logger.debug(f"Failed to calculate file size with error {why}")
                    # calcualte percentage based off total size and current archive size
                percent = round((total_bytes / dir_bytes) * 100, 2)
                # package results
                results = {
                    "percent": percent,
                    "total_files": self.helper.human_readable_file_size(dir_bytes),
                    "backup_id": backup_id,
                }
                # send status results to page.
                WebSocketManager().broadcast_page_params(
                    SERVER_DETAIL,
                    {"id": str(server_id)},
                    "backup_status",
                    results,
                )
                WebSocketManager().broadcast_page_params(
                    "/panel/edit_backup",
                    {"id": str(server_id)},
                    "backup_status",
                    results,
                )
        return True

    def move_item_file_or_dir(self, old_dir: str, new_dir: str, item: str) -> None:
        """
        Move item to new location if it is either a file or a dir.

        Args:
            old_dir: Old location.
            new_dir: New location.
            item: File or directory name.

        Raises:
            shutil.Error: For any move errors that are encountered.

        """
        try:
            # Check if source item is a directory or a file.
            if Path(old_dir, item).is_dir():
                # Source item is a directory
                FileHelpers.move_dir_exist(
                    str(Path(old_dir) / item),
                    str(Path(new_dir) / item),
                )
            else:
                # Source item is a file.
                FileHelpers.move_file(
                    str(Path(old_dir) / item),
                    str(Path(new_dir) / item),
                )

        # Error raised by shutil if an error is encountered. Raising the same error if
        # encountered.
        except shutil.Error as why:
            err_msg = f"Error moving {old_dir} to {new_dir} with information: {why}"
            raise RuntimeError(err_msg) from why

    @staticmethod
    def restore_archive(archive_location: str, destination: str) -> None:
        """
        Restore zip file into specified destination.

        Args:
            archive_location: The zip to unzip.
            destination: The target location to unzip to.

        """
        with zipfile.ZipFile(archive_location, "r") as zip_ref:
            zip_ref.extractall(destination)

    @staticmethod
    def send_percentage(
        user: list[str],
        broadcast_data: BackupPercentageBroadcast,
    ) -> None:
        """
        Send a websocket percentage to given user(s).

        Args:
            user: List of user(s) to send broadcast to.
            broadcast_data: The information to be sent to the users.

        """
        for usr in user:
            WebSocketManager().broadcast_user(
                usr,
                "zip_status",
                broadcast_data.as_dict(),
            )

    def should_extract(
        self,
        file: str,
        base_include_path: str | None,
        excluded_files: list[str],
        server_update: bool,  # noqa: FBT001 see unzip file.
    ) -> bool:
        """
        Check a number of inclusions or exclusions against a given file.

        Checks to see if that file should be unpacked to the target directory.

        ** Base include path and excluded files should not be used in conjunction with
        eachother.

        Args:
            file (str): file name from Path zip object namelist
            base_include_path (str): string from root dir select that shows base path
            like 'server_files/myserver/' (should not be used with excluded_files)
            excluded_files (list): list of file exclusions (should not be used with base
            include path)
            server_update (bool): whether or not the method was called as a result
            of a server update process.

        Returns:
            bool: Whether or not the file from the list should be included in the
            unzipped archive.

        """
        if server_update and file in excluded_files:
            return False

        if not base_include_path:
            return True

        try:
            pathlib.PurePosixPath(file).relative_to(
                pathlib.PurePosixPath(base_include_path),
            )
        except ValueError:
            return False

        return True

    def get_archive_internal_name(
        self,
        file: str,
        base_include_path: str | None,
    ) -> str:
        """
        Get relative base path from an archive.

        If we have a base include path we will rewrite the internal zip object path
        to remove the /path/to/file/in/archive so we don't have nested folders when we
        unzip. This will return the relative path to the archive to avoid nesting.

        Args:
            file (str): file from namelist from zip object
            base_include_path (str): string from root dir select that shows base path
            like 'server_files/myserver/'

        Returns:
            str | PurePosixPath: returns the original file name or new file name

        """
        if base_include_path:  # Rewrite path of zip_ref_info if we have a base include
            try:
                rel = pathlib.PurePosixPath(file).relative_to(base_include_path)
                return str(rel)
            except ValueError:

                logger.debug("%s is not relative to %s", file, base_include_path)
        return str(file)

    @staticmethod
    def _normalize_websocket_recipients(
        server_id: str | None,
        user_id: str | None,
    ) -> list[str]:
        """
        Get a normalized list of users for unzip_file.

        Args:
            server_id: The server ID to use to get list of relevant users.
            user_id: The user ID of the user.

        """
        if not user_id:
            recipients = PermissionsServers.get_server_user_list(server_id)
        else:
            recipients: list[str] = [user_id]

        return recipients

    # Disabling the lint for too many position arguments. My notes are in the function.
    # This bad boy needs a rewrite, I don't understand the websockets enough to make
    # that happen at the moment.
    def unzip_file(  # noqa: PLR0913
        self,
        zip_path: str,
        destination_path: Path,
        server_id: str | None = None,
        server_update: bool = False,  # noqa: FBT001, FBT002
        proc_id: str | None = None,
        user_id: str | None = None,
        base_include_path: str | None = None,
    ) -> None:
        """
        Unzips zip file at zip_path.

        Unzips to location generated at new_dir based on zipcontents.

        Boat Note: I'm less convinced about the ruff exception on server_update, I'll
        leave it for now but happy to re-architect this function to resolve that later.

        Args:
            zip_path: Path to zip file to unzip.
            destination_path: Where the zip file should be unziped to.
            server_id: The ID of the server associated with this unzip, used for
                websocket broadcasts.
            server_update: Will skip ignored items list if not set to true. Used for
                updating bedrock servers.
            proc_id: Used for websocket broadcasts, not sure what this is.
            user_id: Used for websocket broadcasts.
            base_include_path: Used for file/path exclusions.

        Raises:
            OSError: If there are file permission or other issue that prevent file
            operations. All call sites should check for OSError.

        """
        # This function is not perfect, likely needs a rewrite.
        # I don't understand enough about this function to rewrite it at the moment.
        # It's trying to do too much, the functionality should be more
        # compartmentalized. Having this function also handle websockets is difficult.

        recipients = self._normalize_websocket_recipients(server_id, user_id)

        # I've unwrapped explicitly permission checks before the rest of this function.
        # Doing so before actually unzipping is a bit of a TOCTOU error and is better
        # handled by catching OSError around call sites rather than explicitly checking
        # it here. All call sites must then check for OSError on this function.

        # make sure the directory we're unzipping this to exists
        Helpers.ensure_dir_exists(destination_path)
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            files_list = zip_ref.namelist()
            for idx, file in enumerate(files_list):
                info = zip_ref.getinfo(file)
                # Skip directory entries
                if info.is_dir():
                    continue

                target = Path(destination_path, file).resolve()
                try:
                    self.helper.validate_traversal(destination_path, target)
                except ValueError:
                    self.send_percentage(
                        recipients,
                        BackupPercentageBroadcast(
                            id=proc_id,
                            percent=100,
                            complete=True,
                        ),
                    )
                    return logger.exception("Traversal detected. Dumping out.")

                # if the file is one of our ignored names we'll skip it
                if not self.should_extract(
                    file,
                    base_include_path,
                    self.UNZIP_IGNORED_NAMES,
                    server_update,
                ):
                    continue

                info.filename = self.get_archive_internal_name(
                    file,
                    base_include_path,
                )
                try:
                    zip_ref.extract(info, destination_path)
                except FileNotFoundError:
                    logger.exception(
                        "Could not extract file: %s to %s from archive %s",
                        file,
                        destination_path,
                        zip_path,
                    )
                percent = round((idx / len(files_list)) * 100)
                self.send_percentage(
                    recipients,
                    BackupPercentageBroadcast(
                        id=proc_id,
                        percent=percent,
                        complete=False,
                    ),
                )
        self.send_percentage(
            recipients,
            BackupPercentageBroadcast(id=proc_id, percent=100, complete=True),
        )

        return None

    @staticmethod
    def get_absolute_path(server_path: str, path: str) -> str:
        """
        Take requested path and returns absolute path.

        Args:
            server_path (str): requested server's root path
            path (str | path): requested file path

        Returns:
            _type_: Path

        """
        request_path = path
        if not Path(path).is_absolute():
            path = str(Path(server_path, request_path))

        return str(path)

    @staticmethod
    def get_chunk_path_from_hash(chunk_hash: bytes, repository_location: Path) -> Path:
        """
        Given chunk hash and repository location, gets full path to chunk in repo.

        Args:
            chunk_hash: Hash of chunk in bytes.
            repository_location: Path to the backup repository.

        Return: Path to chunk in repository.

        """
        hash_hex = CryptoHelper.bytes_to_hex(chunk_hash)
        if len(hash_hex) != BLAKE3_HASH_LENGTH_BYTES:
            err_msg = f"Provided hash is of incorrect length. Hash: {hash_hex}."
            raise ValueError(err_msg)
        return repository_location / "chunks" / hash_hex[:2] / hash_hex[-126:]

    @staticmethod
    def get_file_path_from_hash(file_hash: bytes, repository_location: Path) -> Path:
        """
        Get path to file manifest file in repository location.

        This is constructed given file hash and repository location.

        Args:
            file_hash: Hash of file.
            repository_location: Path to the backup repository.

        Returns: Path to file manifest file in the backup repository.

        """
        hash_hex: str = CryptoHelper.bytes_to_hex(file_hash)
        if len(hash_hex) != BLAKE3_HASH_LENGTH_BYTES:
            err_msg = f"Provided hash is of incorrect length. Hash: {hash_hex}"
            raise ValueError(err_msg)
        return repository_location / "files" / hash_hex[:2] / hash_hex[-126:]

    @staticmethod
    def discover_files(target_path: Path, exclusions: list[str]) -> list[Path]:
        """
        Return a list of all files in a target path, ignores empty directories.

        Args:
            target_path: Path to find all files in.
            exclusions: File path(s) to exclude from this file discovery.

        Returns: List of all files in target path.

        """
        # Check that target is a directory.
        if not target_path.is_dir():
            err_msg = f"{target_path} is not a directory."
            raise NotADirectoryError(err_msg)

        discovered_files = []
        excluded_dirs = []
        excluded_files = []

        for excl_dir in exclusions:
            temp_path = Path(excl_dir).resolve()
            if temp_path.is_file():
                excluded_files.append(temp_path)
            else:
                excluded_dirs.append(temp_path)

        # Use pathlib built in rglob to find all files.
        for p in target_path.rglob("*"):
            if p.is_dir():
                continue
            if p not in excluded_files and p.parents not in excluded_dirs:
                discovered_files.append(p)
        return discovered_files

    @staticmethod
    def list_log_files(log_dir: Path) -> list[dict]:
        """
        List the log files in a server's log directory.

        Covers the current log as well as rotated/archived ones, e.g.
        Minecraft's Log4j2 ``YYYY-MM-DD-N.log.gz`` archives.

        Args:
            log_dir: Directory to scan - the parent of the currently active
                log file, as resolved by Helpers.resolve_log_path.

        Returns: A list of dicts with ``name``, ``modified`` (epoch) and
            ``size`` (bytes), newest first. Empty list if the directory
            doesn't exist.

        """
        log_dir = Path(log_dir)
        if not log_dir.is_dir():
            return []

        files = []
        for entry in log_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix not in (".log", ".gz"):
                continue
            stat = entry.stat()
            files.append(
                {
                    "name": entry.name,
                    "modified": stat.st_mtime,
                    "size": stat.st_size,
                }
            )

        files.sort(key=lambda item: item["modified"], reverse=True)
        return files

    @staticmethod
    def group_log_files_by_date(files: list[dict]) -> list[dict]:
        """
        Group log files by the calendar date in their filename.

        Takes the output of list_log_files and groups it so a day with
        several rotations (e.g. Log4j2's ``2026-09-04-1.log.gz``,
        ``2026-09-04-2.log.gz``) can be viewed as a single combined log
        instead of opened one by one.

        Args:
            files: list of dicts as returned by list_log_files, each with
                an additional ``active`` bool set by the caller.

        Returns: list of dicts with ``date``, ``files`` (names, oldest
            first), ``size`` (summed bytes), ``modified`` (newest mtime in
            the group) and ``active``, sorted newest date first.

        """
        date_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})")

        groups = {}
        for entry in files:
            match = date_pattern.match(entry["name"])
            # Files with no date in their name (e.g. the active log) fall
            # back to their own mtime so they still get a sensible group.
            date = (
                match.group(1)
                if match
                else datetime.datetime.fromtimestamp(entry["modified"]).strftime(
                    "%Y-%m-%d"
                )
            )
            group = groups.setdefault(
                date,
                {"date": date, "files": [], "size": 0, "modified": 0, "active": False},
            )
            group["files"].append(entry)
            group["size"] += entry["size"]
            group["modified"] = max(group["modified"], entry["modified"])
            group["active"] = group["active"] or entry["active"]

        result = list(groups.values())
        for group in result:
            # Oldest first, so reading the group's files in order reconstructs
            # the day's timeline.
            group["files"].sort(key=lambda entry: entry["modified"])
            group["files"] = [entry["name"] for entry in group["files"]]

        result.sort(key=lambda group: group["modified"], reverse=True)
        return result

    @staticmethod
    def tail_files(file_names: list, number_lines: int = 20) -> list[str]:
        """
        Tail the combined content of multiple log files.

        Used for e.g. every rotation from one calendar day, concatenated
        oldest-to-newest by modification time so a multi-file day reads as
        one continuous log.

        Args:
            file_names: paths to concatenate, in any order.
            number_lines: max lines to return, taken from the end of the
                combined content.

        Returns: The last ``number_lines`` lines of the combined content.

        """
        existing = [f for f in file_names if Helpers.check_file_exists(f)]
        if not existing:
            logger.warning(f"Unable to find file(s) to tail: {file_names}")
            return [f"Unable to find file(s) to tail: {file_names}"]

        existing.sort(key=os.path.getmtime)

        all_lines = []
        for file_name in existing:
            all_lines.extend(Helpers.tail_file(file_name, number_lines))

        return all_lines[-number_lines:]

    def clean_old_backups(self, num_to_keep: int, backup_repository_path: Path) -> None:
        """
        Remove all old backups from the backup repository.

        Based on number of backups to keep.

        Args:
            num_to_keep: Number of backups to keep. Keeps latest n.
            backup_repository_path: Path to the backup repository.

        """
        if num_to_keep <= 0:
            return

        # get list of manifest files in the backup repo.
        manifest_files_path: Path = backup_repository_path / "manifests"
        manifest_files_generator = manifest_files_path.rglob("*")
        # List used later to delete manifest files.
        manifest_files_list: list[Path] = []

        # Extract the datetimes from the filenames of the manifest files.
        manifests_datetime: list[datetime.datetime] = []
        for manifest_file in manifest_files_generator:
            manifest_files_list.append(manifest_file)
            manifests_datetime.append(
                datetime.datetime.strptime(
                    manifest_file.name.split(".")[0],
                    self.SNAPSHOT_BACKUP_DATE_FORMAT_STRING,
                ).astimezone(),
            )

        # sort list of manifests.
        # Oldest datetime events will be sorted first.
        manifests_datetime.sort()

        # Determine number of manifest files to remove
        # For example, we have 10, want to keep 7.
        # 10 - 7 = 3.
        num_to_remove = len(manifests_datetime) - num_to_keep

        # Return if we don't need to remove any files.
        if num_to_remove <= 0:
            return

        # Oldest first, delete n oldest files from list.
        for _ in range(num_to_remove):
            del manifests_datetime[0]

        # Delete manifest files that are no longer used.
        self.delete_unused_manifest_files(manifests_datetime, manifest_files_list)

        files_to_keep, chunks_to_keep = self.create_file_keepers_set(
            backup_repository_path,
            manifests_datetime,
        )

        # Delete unused files and chunks.
        self.delete_unused_items_from_repository(
            files_to_keep,
            backup_repository_path,
            SnapshotFileTypes.FILES,
        )
        self.delete_unused_items_from_repository(
            chunks_to_keep,
            backup_repository_path,
            SnapshotFileTypes.CHUNKS,
        )

    @staticmethod
    def delete_unused_items_from_repository(
        items_to_keep: set[bytes],
        backup_repository_path: Path,
        mode: SnapshotFileTypes,
    ) -> None:
        """
        Delete unused chunks for files from the backup repository.

        Switches type based on mode.

        Args:
            items_to_keep: Set of chunks or files to keep.
            backup_repository_path: Path to backup repository.
            mode: Which snapshot files to operate on.

        """
        # Mode False = files. True = chunks.
        if mode == SnapshotFileTypes.CHUNKS:
            item_manifests_path = backup_repository_path / "chunks"
        else:
            item_manifests_path = backup_repository_path / "files"
        item_generator = item_manifests_path.rglob("*")
        for item in item_generator:
            # Generator returns both directories and files. We can ignore directories.
            if item.is_dir():
                continue

            # Reconstruct item hash from item path.
            # Stored as first two octets as a directory and rest of hash as filename.
            item_hash: bytes = bytes.fromhex(str(item.parent.name) + str(item.name))

            # If item is not present in the ones that we want to keep, delete it.
            if item_hash not in items_to_keep:
                item.unlink()

    def delete_unused_manifest_files(
        self,
        manifest_files_to_keep: list[datetime.datetime],
        manifest_files_list: list[Path],
    ) -> None:
        """
        Delete unused backup manifest files from the backup repository.

        Args:
            manifest_files_to_keep: List of manifest files to keep. Datetime list of
                backups to keep.
            manifest_files_list: List of all files currently found in the backup
                repository.

        """
        # This is a little nasty.
        # Iterate over files found in the backup repository.
        for manifest_file in manifest_files_list:
            # If that file, converted to a datetime, is not present in the files_to_keep
            # list.
            if (
                datetime.datetime.strptime(
                    manifest_file.name.split(".")[0],
                    self.SNAPSHOT_BACKUP_DATE_FORMAT_STRING,
                ).astimezone()
                not in manifest_files_to_keep
            ):
                # Delete the file.
                manifest_file.unlink(missing_ok=True)

    def create_file_keepers_set(
        self,
        backup_repository_path: Path,
        keepers_datetime_list: list[datetime.datetime],
    ) -> tuple[set[bytes], set[bytes]]:
        """
        Create a set of files to keep from a given backup manifest files to keep.

        Args:
            backup_repository_path: Path to backup repository.
            keepers_datetime_list: List of manifest files to keep. Datetime list.

        Returns: Set of files to keep, set of chunks to keep.

        Raises:
            RuntimeError: If the manifest file can not be read, the manifest is not of
            the correct version number, down downstream file errors.

        """
        files_to_keep = set()
        for keeper_manifest_datetime in keepers_datetime_list:
            backup_time = keeper_manifest_datetime.strftime(
                self.SNAPSHOT_BACKUP_DATE_FORMAT_STRING,
            )
            # Open file
            manifest_file_path = (
                backup_repository_path / "manifests" / f"{backup_time}.manifest"
            )
            try:
                manifest_file: io.TextIOWrapper = manifest_file_path.open("r")
            except OSError as why:
                err_msg = f"Unable to open manifest file at {manifest_file_path}"
                raise RuntimeError(err_msg) from why

            # Check that manifest is readable with this version.
            if manifest_file.readline() != "00\n":
                manifest_file.close()
                err_msg = (
                    "Backup manifest is not of correct version."
                    f"Manifest: { manifest_file_path}."
                )
                raise RuntimeError(err_msg)

            for line in manifest_file:
                # Add hash to keep to output set.
                files_to_keep.add(CryptoHelper.b64_to_bytes(line.split(":")[0]))

            # Close this file.
            manifest_file.close()

        keeper_chunks = set()

        # Iterate over files to keep, and record all chunks to keep for those files.
        for file_to_keep in files_to_keep:
            file_chunks = self.get_keeper_chunks_file_file_hash(
                backup_repository_path,
                file_to_keep,
            )
            for chunk in file_chunks:
                keeper_chunks.add(chunk)
        return files_to_keep, keeper_chunks

    def get_keeper_chunks_file_file_hash(
        self,
        backup_repository_location: Path,
        file_hash: bytes,
    ) -> list[bytes]:
        """
        Get chunks that should be kept based on given file.

        Args:
            backup_repository_location: Path to the backup repository.
            file_hash: Hash of file.

        Returns: List of chunk hashes that should be kept.

        """
        file_manifest_path: Path = self.get_file_path_from_hash(
            file_hash,
            backup_repository_location,
        )

        # Open file and read keeper chunks.
        try:
            file_manifest_file = file_manifest_path.open("r")
        except OSError as why:
            err_msg = f"Unable to open file manifest file at {file_manifest_path}"
            raise RuntimeError(err_msg) from why

        if file_manifest_file.readline() != "00\n":
            file_manifest_file.close()
            err_msg = (
                f"File manifest file {file_manifest_path} is not of a readable version."
            )
            raise RuntimeError(err_msg)

        output: set[bytes] = set()

        for line in file_manifest_file:
            output.add(CryptoHelper.b64_to_bytes(line))

        return list(output)

    @staticmethod
    def get_local_path_with_base(desired_path: Path, base: Path) -> str:
        """
        Remove base from given path.

        Given:
            Path: /root/example.md
            Base: /root/
            Returns: example.md

        Args:
            desired_path: Path to file in base.
            base: Base file to remove from path.

        Returns: Local path to file.

        Raises:
            OSError if given a child path that is not in the parent.

        """
        # Check that file is contained in base, and the base is a directory.
        if base not in desired_path.parents:
            err_msg = f"{desired_path} is not a child of {base}."
            raise OSError(err_msg)

        return str(desired_path.resolve())[len(str(base.resolve())) + 1 :]

    def save_file(
        self,
        source_file: Path,
        repository_location: Path,
        file_hash: bytes,
        use_compression: bool,  # noqa: FBT001
    ) -> None:
        """
        Save given file to repository location.

        Will not save duplicate files or duplicate chunks. All errors resolve to
        RuntimeErrors.

        Args:
            source_file: Source file to save to the backup repository.
            repository_location: Path to the backup repository.
            file_hash: Hash of file.
            use_compression: If the file in the backup repository should be compressed.

        Raises:
            RuntimeError: if the given file has is of the incorrect length, the file can
            not be read, the file can can not be opned, or downstream file and chunk
            write errors.

        """
        # File is read and saved in 20mb chunks. Should allow memory use to stay low and
        # for files to be processed that are larger than available memory.
        try:
            file_manifest_file_location: Path = self.get_file_path_from_hash(
                file_hash,
                repository_location,
            )
        except ValueError as why:
            err_msg = "Provided file hash does not appear to be of improper length!"
            raise RuntimeError(err_msg) from why

        # Exit if file is already present in the backup repository. Ensure that we don't
        # try to save the save file twice.
        if file_manifest_file_location.exists():
            return
        file_manifest_file_location.parent.mkdir(parents=True, exist_ok=True)

        # Open source file and start saving chunks.
        try:
            source_file_obj = source_file.open("rb")
        except OSError as why:
            err_msg = f"Unable to read file at {source_file}."
            raise RuntimeError(err_msg) from why

        # Open target file manifest file to write chunks.
        try:
            file_manifest_file = file_manifest_file_location.open("w+")
        except OSError as why:
            source_file_obj.close()
            err_msg = (
                f"Unable to open file manifest file at {file_manifest_file_location}."
            )
            raise RuntimeError(err_msg) from why

        # Begin reading source and writing to manifest file.
        # Write file manifest file version number as first line.
        file_manifest_file.write("00\n")

        # Loop through file contents writing to both files until empty.
        while True:
            chunk = source_file_obj.read(10_000_000)

            if not chunk:
                # Completed reading source file, close out.
                source_file_obj.close()
                file_manifest_file.close()
                return

            # Write chunk to file manifest file.
            chunk_hash = CryptoHelper.blake2b_hash_bytes(chunk)
            chunk_hash_as_b64 = CryptoHelper.bytes_to_b64(chunk_hash)
            file_manifest_file.write(chunk_hash_as_b64 + "\n")

            try:
                self.save_chunk(chunk, repository_location, chunk_hash, use_compression)
            except RuntimeError as why:
                err_msg = f"Unable to save chunk with hash {chunk_hash}."
                raise RuntimeError(err_msg) from why

    def save_chunk(
        self,
        file_chunk: bytes,
        repository_location: Path,
        chunk_hash: bytes,
        use_compression: bool,  # noqa: FBT001
    ) -> None:
        """
        Save chunk to backup repository.

        Space is made in this version of the chunk for encryption, but that
        functionality is not yet present.

        Args:
            file_chunk: chunk data to save to file.
            repository_location: Path to repository.
            chunk_hash: hash of chunk.
            use_compression: If the chunk should be compressed before saving to file.

        Raises:
            RuntimeError: When there is an error saving the chunk to disk.

        """
        file_location = self.get_chunk_path_from_hash(chunk_hash, repository_location)

        # If chunk is already present, stop here. Don't save the chunk again.
        if file_location.exists():
            return

        # Create folder for chunk.
        file_location.parent.mkdir(parents=True, exist_ok=True)

        # Chunk version number.
        version = bytes.fromhex("00")

        # Check and apply compression, write compression byte.
        if use_compression:
            file_chunk = self.zlib_compress_bytes(file_chunk)
            compression = self.BYTE_TRUE
        else:
            compression = self.BYTE_FALSE

        # Placeholder to allow for encryption in future versions
        encryption = self.BYTE_FALSE
        nonce = bytes.fromhex("000000000000000000000000")

        # Create chunk
        output = version + encryption + nonce + compression + file_chunk

        # Save chunk to file
        try:
            with file_location.open("wb+") as file:
                file.write(output)
        except OSError as why:
            err_msg = f"Unable to save chunk to {file_location}"
            raise RuntimeError(err_msg) from why

    def read_file(
        self,
        file_hash: bytes,
        target_path: Path,
        backup_repo_path: Path,
    ) -> None:
        """
        Read file from file manifest, restores to target path.

        Args:
            file_hash: Hash of file to restore.
            target_path: Path to restore file to.
            backup_repo_path: Path to the backup repo.

        Raises:
            RuntimeError: When the given file hash is not of the correct length, If
            there is an issue opening the targeted file path, if the file manifest is
            not of the correct version, or if there is an error restoring a chunk to the
            target file.

        """
        # Get file manifest file path.
        try:
            source_file_manifest_path: Path = self.get_file_path_from_hash(
                file_hash,
                backup_repo_path,
            )
        except ValueError as why:
            err_msg = (
                f"Provided hash does not appear to be of proper length."
                f" Hash: {CryptoHelper.bytes_to_hex(file_hash)}"
            )
            raise RuntimeError(err_msg) from why

        # Ensure target folder exists.
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Open target.
        try:
            target_file: io.BufferedRandom = target_path.open("wb+")
            source_file_manifest = source_file_manifest_path.open("r")
        except OSError as why:
            err_msg = f"Error opening file at {target_path} for backup restore."
            raise RuntimeError(err_msg) from why

        # Ensure manifest version is of expected value.
        if source_file_manifest.readline() != "00\n":
            target_file.close()
            source_file_manifest.close()

            err_msg = f"File manifest is not of correct version. File: {file_hash}."

            raise RuntimeError(err_msg)

        # Iterate over file manifest and restore file.
        for line in source_file_manifest:
            chunk_hash: bytes = CryptoHelper.b64_to_bytes(line)
            try:
                target_file.write(self.read_chunk(chunk_hash, backup_repo_path))
            except RuntimeError as why:
                target_file.close()
                source_file_manifest.close()

                err_msg = f"Error restoring chunk with hash: {chunk_hash}."

                raise RuntimeError(err_msg) from why

        target_file.close()
        source_file_manifest.close()

    def read_chunk(self, chunk_hash: bytes, repo_path: Path) -> bytes:
        """
        Read data out of a data chunk. Set for version 00 chunks.

        This function does not currently handle encryption.

        Args:
            chunk_hash: Hash of chunk to get out of storage.
            repo_path: Path to the backup repository.

        Returns: Data in chunk.

        Raises:
            RuntimeError: If the given chunk can not be read, is not of version 00,
            or if decompression of the chunk fails.

        """
        # Get chunk path.
        chunk_path: Path = self.get_chunk_path_from_hash(chunk_hash, repo_path)

        # Attempt to read chunk
        try:
            chunk_file: io.BufferedReader = chunk_path.open("rb")
        except OSError as why:
            err_msg = (
                "Unable to read chunk with hash "
                f"{CryptoHelper.bytes_to_hex(chunk_hash)}.",
            )

            raise RuntimeError(err_msg) from why

        # confirm version byte is expected value.
        version: bytes = chunk_file.read(1)
        if version != bytes.fromhex("00"):
            # Chunk is of unexpected version here. Close chunk and panic out.
            chunk_file.close()
            err_msg = (
                "Chunk is of unexpected version. Unable to read."
                f" Version was {CryptoHelper.bytes_to_hex(version)}."
            )

            raise RuntimeError(err_msg)

        # Read encryption byte and none. Code not currently used.
        # One byte for use encryption byte and 12 bytes of nonce.
        _ = chunk_file.read(13)

        # Read compression byte.
        use_compression_byte: bytes = chunk_file.read(1)

        chunk_data: bytes = chunk_file.read()

        if use_compression_byte == self.BYTE_TRUE:
            try:
                chunk_data = self.zlib_decompress_bytes(chunk_data)
            except zlib.error as why:
                err_msg = (
                    "Unable to decompress chunk with"
                    f" hash: {CryptoHelper.bytes_to_hex(chunk_hash)}."
                )

                raise RuntimeError(err_msg) from why

        return chunk_data

    @staticmethod
    def zlib_compress_bytes(bytes_to_compress: bytes) -> bytes:
        """
        Compress given bytes with zlib.

        Args:
            bytes_to_compress: Bytes to compress.

        Return: Compressed bytes.

        """
        return zlib.compress(bytes_to_compress)

    @staticmethod
    def zlib_decompress_bytes(bytes_to_decompress: bytes) -> bytes:
        """
        Decompress given bytes with zlib.

        Args:
            bytes_to_decompress: Bytes to decompress.

        Returns: Decompressed bytes.

        Raises:
            zlib.error: The given bytes were not a zlib compressed blob.

        """
        return zlib.decompress(bytes_to_decompress)

    @staticmethod
    def get_dir_size(server_path: str) -> int:
        """
        Recursively calculates dir size. Returns size in bytes.

        Must calculate human readable based on returned data

        Args:
            server_path (str): Path to calculate size

        Returns:
            _type_: Integer

        """
        # because this is a recursive function, we will return bytes,
        # and set human readable later
        total = 0
        for entry in os.scandir(server_path):
            if entry.is_dir(follow_symlinks=False):
                total += FileHelpers.get_dir_size(entry.path)
            else:
                total += entry.stat(follow_symlinks=False).st_size
        return total

    @staticmethod
    def get_drive_free_space(file_location: Path) -> int:
        """Get the free storage available at a give file path."""
        _total, _used, free = shutil.disk_usage(file_location)
        return free

    @staticmethod
    def has_enough_storage(target_size: float, target_free_storage: float) -> bool:
        """Compare given free size and free storage for enough space."""
        return target_size <= target_free_storage
