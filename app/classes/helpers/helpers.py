import base64
import contextlib
import ctypes
import glob
import gzip
import html
import itertools
import json
import logging
import os
import pathlib
import re
import secrets
import shlex
import shutil
import socket
import string
import subprocess
import sys
import time
import typing as t
import uuid
from contextlib import redirect_stderr, suppress
from datetime import datetime, timedelta, timezone
from socket import gethostname

import libgravatar
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from packaging import version as pkg_version

from app.classes.helpers import ansi
from app.classes.helpers import links
from app.classes.helpers.cryptography_helper import CryptoHelper
from app.classes.shared.console import Console
from app.classes.shared.installer import installer
from app.classes.shared.null_writer import NullWriter
from app.classes.shared.translation import Translation

with redirect_stderr(NullWriter()):
    import psutil

# winreg is only a package on windows-python. We will only import
# this on windows systems to avoid a module not found error
# this is only needed for windows java path shenanigans
if os.name == "nt":
    import winreg

logger = logging.getLogger(__name__)

PLAIN_TEXT = "text/plain"
BAT_TEXT = "application/x-bat"
ERROR_LOG_HIGHLIGHT = r'<span class="mc-log-error">\1</span>'
KEYWORD_LOG_HIGHLIGHT = r'<span class="mc-log-keyword">\1</span>'
INFO_LOG_HIGHLIGHT = r'<span class="mc-log-info">\1</span>'
WARN_LOG_HIGHLIGHT = r'<span class="mc-log-warn">\1</span>'
FATAL_LOG_HIGHLIGHT = r'<span class="mc-log-fatal">\1</span>'
TIME_LOG_HIGHLIGHT = r'<span class="mc-log-time">[\1]</span>'
TIMESTAMP_RE = re.compile(r"\[(\d\d:\d\d:\d\d)\]")

# What log_colors() applies, in order. Each pattern is paired with a lowercase
# literal it cannot match without, so lines missing it skip the pass.
LOG_REPLACEMENTS = (
    ("/info]", re.compile(r"(\[.+?/INFO\])", re.IGNORECASE), INFO_LOG_HIGHLIGHT),
    ("/warn]", re.compile(r"(\[.+?/WARN\])", re.IGNORECASE), WARN_LOG_HIGHLIGHT),
    ("/error]", re.compile(r"(\[.+?/ERROR\])", re.IGNORECASE), ERROR_LOG_HIGHLIGHT),
    ("/severe]", re.compile(r"(\[.+?/SEVERE\])", re.IGNORECASE), ERROR_LOG_HIGHLIGHT),
    ("/fatal]", re.compile(r"(\[.+?/FATAL\])", re.IGNORECASE), FATAL_LOG_HIGHLIGHT),
    (
        "[/",
        re.compile(r"(\w+?\[/\d+?\.\d+?\.\d+?\.\d+?\:\d+?\])", re.IGNORECASE),
        KEYWORD_LOG_HIGHLIGHT,
    ),
    (":", TIMESTAMP_RE, TIME_LOG_HIGHLIGHT),
    (" info]", re.compile(r"(\[.+? INFO\])", re.IGNORECASE), INFO_LOG_HIGHLIGHT),
    (" warn]", re.compile(r"(\[.+? WARN\])", re.IGNORECASE), WARN_LOG_HIGHLIGHT),
    (" error]", re.compile(r"(\[.+? ERROR\])", re.IGNORECASE), ERROR_LOG_HIGHLIGHT),
    (" severe]", re.compile(r"(\[.+? SEVERE\])", re.IGNORECASE), ERROR_LOG_HIGHLIGHT),
    (" fatal]", re.compile(r"(\[.+? FATAL\])", re.IGNORECASE), FATAL_LOG_HIGHLIGHT),
)

# The bracketed prefix a line opens with - "[16:58:26 INFO]:" or
# "[16:58:26] [Server thread/INFO]:". Anchored on the timestamp so other
# bracketed openings are left to the server's own colors.
LOG_PREFIX_RE = re.compile(r"^\[\d\d:\d\d:\d\d[^\]]*\](?:\s*\[[^\]]*\])*:?\s*")

# Java servers only emit color when they think they are attached to a
# terminal. Crafty reads them over a pipe, so ask for it explicitly. Users can
# drop these from a server's command.
TERMINAL_ANSI_ARGS = "-Dterminal.ansi=true -Dterminal.jline=false"


class ConsoleSettings(t.NamedTuple):
    """The settings that shape how one console line is rendered."""

    # The user's keywords as one alternation, or None if they set none.
    keywords: t.Optional[re.Pattern]
    ansi: bool
    prefix_colors: bool
    links: bool


MASTER_CONFIG = {
    "https_port": 8443,
    "language": "en_EN",
    "cookie_expire": 30,
    "show_errors": True,
    "history_max_age": 7,
    "stats_update_frequency_seconds": 30,
    "delete_default_json": False,
    "show_contribute_link": True,
    "virtual_terminal_lines": 70,
    "max_log_lines": 700,
    "max_audit_entries": 300,
    "disabled_language_files": [],
    "keywords": ["help", "chunk"],
    "ansi_terminal_colors": True,
    "ansi_terminal_prefix_colors": True,
    "terminal_clickable_links": True,
    "allow_nsfw_profile_pictures": False,
    "enable_user_self_delete": False,
    "reset_secrets_on_next_boot": False,
    "dir_size_poll_freq_minutes": 5,
    "crafty_logs_delete_after_days": 0,
    "big_bucket_repo": "https://jars.arcadiatech.org",
    "enable_otp_skew": False,
    "max_login_attempts": 3,
    "superMFA": False,
    "general_user_log_access": False,
    "base_url": "localhost:8443",
    "enable_passkey_auth": False,
    "passkey_rp_name": "Crafty Controller",
    "experimental": False,
    "silent_notif_fields": ["dashboard_columns", "server_order"],
    "time_range_presets": [
        {"hours": 1, "label": "Last Hour"},
        {"hours": 3, "label": "Last 3 Hours"},
        {"hours": 6, "label": "Last 6 Hours"},
        {"hours": 12, "label": "Last 12 Hours"},
        {"hours": 24, "label": "Last 24 Hours"},
        {"hours": 48, "label": "Last 2 Days"},
        {"hours": 168, "label": "Last 7 Days"},
    ],
    "sampling_tiers": [
        {"max_hours": 6, "sample_rate": 1},
        {"max_hours": 24, "sample_rate": 2},
        {"max_hours": 72, "sample_rate": 6},
    ],
    "sampling_fallback_divisor": 12,
    "trusted_proxies": [],
    "max_image_upload_size_mb": 5,
}

CONFIG_CATEGORIES = {
    "general": [
        "https_port",
        "language",
        "show_errors",
        "show_contribute_link",
        "disabled_language_files",
        "big_bucket_repo",
        "enable_user_self_delete",
        "base_url",
        "experimental",
        "max_image_upload_size_mb",
    ],
    "security": [
        "allow_nsfw_profile_pictures",
        "cookie_expire",
        "reset_secrets_on_next_boot",
        "enable_otp_skew",
        "superMFA",
        "max_login_attempts",
        "enable_passkey_auth",
        "passkey_rp_name",
        "trusted_proxies",
    ],
    "logs": [
        "max_log_lines",
        "max_audit_entries",
        "history_max_age",
        "crafty_logs_delete_after_days",
        "virtual_terminal_lines",
        "keywords",
        "ansi_terminal_colors",
        "ansi_terminal_prefix_colors",
        "terminal_clickable_links",
        "general_user_log_access",
        "silent_notif_fields",
    ],
    "monitoring": [
        "monitored_mounts",
        "dir_size_poll_freq_minutes",
        "stats_update_frequency_seconds",
        "time_range_presets",
        "sampling_tiers",
        "sampling_fallback_divisor",
    ],
    "miscellaneous": ["delete_default_json"],
}

try:
    import requests
    from argon2 import PasswordHasher
    from requests import get

except ModuleNotFoundError as err:
    logger.critical(f"Import Error: Unable to load {err.name} module", exc_info=True)
    print(f"Import Error: Unable to load {err.name} module")
    installer.do_install()


class Helpers:
    allowed_quotes = ['"', "'", "`"]

    def __init__(self):
        self.root_dir = os.path.abspath(os.path.curdir)
        self.read_annc = False
        # (config mtime, ConsoleSettings)
        self._console_settings_cache = None
        self.config_dir = os.path.join(self.root_dir, "app", "config")
        self.webroot = os.path.join(self.root_dir, "app", "frontend")
        self.servers_dir = os.path.join(self.root_dir, "servers")
        self.backup_path = os.path.join(self.root_dir, "backups")
        self.migration_dir = os.path.join(self.root_dir, "app", "migrations")
        self.dir_migration = False

        self.session_file = os.path.join(self.root_dir, "app", "config", "session.lock")
        self.settings_file = os.path.join(self.root_dir, "app", "config", "config.json")

        self.ensure_dir_exists(os.path.join(self.root_dir, "app", "config", "db"))
        self.db_path = os.path.join(
            self.root_dir, "app", "config", "db", "crafty.sqlite"
        )

        self.big_bucket_steamapps_cache = os.path.join(
            self.config_dir, "steamapps.json"
        )
        self.big_bucket_minecraft_cache = os.path.join(
            self.config_dir, "bigbucket.json"
        )
        self.big_bucket_hytale_cache = os.path.join(self.config_dir, "hytale.json")
        self.credits_cache = os.path.join(self.config_dir, "credits.json")

        self.passhasher = PasswordHasher()
        self.exiting = False

        self.translation = Translation(self)
        self.update_available = False
        self.migration_notifications = []
        self.ignored_names = ["crafty_managed.txt"]
        self.crafty_starting = False
        self.minimum_password_length = 8
        self.crypto_helper = CryptoHelper(self)

        self.theme_list = self.load_themes()

    @staticmethod
    def auto_installer_fix(ex):
        logger.critical(f"Import Error: Unable to load {ex.name} module", exc_info=True)
        print(f"Import Error: Unable to load {ex.name} module")
        installer.do_install()

    def check_remote_version(self):
        """
        Check if the remote version is newer than the local version
        Returning remote version if it is newer, otherwise False.
        """
        try:
            # Get tags from Gitlab, select the latest and parse the semver
            response = get(
                "https://gitlab.com/api/v4/projects/20430749/repository/tags", timeout=1
            )
            if response.status_code == 200:
                remote_version = pkg_version.parse(json.loads(response.text)[0]["name"])

                # Get local version data from the file and parse the semver
                local_version = pkg_version.parse(self.get_version_string())

                if remote_version > local_version:
                    return remote_version

        except Exception as e:
            logger.exception(f"Unable to check for new crafty version! \n{e}")
        return False

    @staticmethod
    def get_latest_bedrock_url():
        """
        Get latest bedrock executable url \n\n
        returns url if successful, False if not
        """
        url = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"
        headers = {
            "Accept-Encoding": "identity",
            "Accept-Language": "en",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/104.0.0.0 Safari/537.36"
            ),
        }
        try:
            # Get minecraft server download page
            # (hopefully the don't change the structure)
            response = get(url, headers=headers, timeout=1)

            response_data = response.json()["result"]

            if "links" not in response_data:
                raise KeyError("Unable to find links key in Bedrock response payload")
            bedrock_data = {}
            for link in response_data["links"]:
                dtype = link["downloadType"]
                url = link["downloadUrl"]
                match = re.match(r"serverBedrock(Preview)?(Windows|Linux)", dtype)
                if match:
                    preview = "preview" if match.group(1) else "stable"
                    operating_system = match.group(2).lower()
                    bedrock_data[f"{operating_system}_{preview}"] = url
            if os.name == "nt":
                return bedrock_data["windows_stable"]

            return bedrock_data["linux_stable"]

        except Exception as e:
            logger.exception(f"Unable to resolve remote bedrock download url! \n{e}")
            raise e

    def get_execution_java(self, value, execution_command):
        if self.is_os_windows():
            execution_list = shlex.split(execution_command, posix=False)
        else:
            execution_list = shlex.split(execution_command, posix=True)
        if (
            not any(value in path for path in self.find_java_installs())
            and value != "java"
        ):
            return
        if value != "java":
            if self.is_os_windows():
                execution_list[0] = '"' + value + '/bin/java"'
            else:
                execution_list[0] = '"' + value + '"'
        else:
            execution_list[0] = "java"
        execution_command = ""
        for item in execution_list:
            execution_command += item + " "

        return execution_command

    def detect_java(self):
        if len(self.find_java_installs()) > 0:
            return True

        # We'll use this as a fallback for systems
        # That do not properly setup reg keys or
        # Update alternatives
        if self.is_os_windows():
            if shutil.which("java.exe"):
                return True
        else:
            if shutil.which("java"):
                return True

        return False

    @staticmethod
    def find_java_installs():
        # If we're windows return oracle java versions,
        # otherwise java vers need to be manual.
        if os.name == "nt":
            # Adapted from LeeKamentsky >>>
            # https://github.com/LeeKamentsky/python-javabridge/blob/master/javabridge/locate.py
            jdk_key_paths = (
                "SOFTWARE\\JavaSoft\\JDK",
                "SOFTWARE\\JavaSoft\\Java Development Kit",
            )
            java_paths = []
            for jdk_key_path in jdk_key_paths:
                try:
                    with suppress(OSError), winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE, jdk_key_path
                    ) as kjdk:
                        for i in itertools.count():
                            version = winreg.EnumKey(kjdk, i)
                            kjdk_current = winreg.OpenKey(
                                winreg.HKEY_LOCAL_MACHINE,
                                jdk_key_path,
                            )
                            kjdk_current = winreg.OpenKey(
                                winreg.HKEY_LOCAL_MACHINE,
                                jdk_key_path + "\\" + version,
                            )
                            kjdk_current_values = dict(  # pylint: disable=consider-using-dict-comprehension
                                [
                                    winreg.EnumValue(kjdk_current, i)[:2]
                                    for i in range(winreg.QueryInfoKey(kjdk_current)[1])
                                ]
                            )
                            java_paths.append(kjdk_current_values["JavaHome"])
                except OSError as e:
                    if e.errno == 2:
                        continue
                    raise
            return java_paths

        # If we get here we're linux so we will use one of the following
        # * Debian: 'update-alternatives'
        # * Arch: 'archlinux-java'
        # * Unknown: <manual input>

        # Sometimes u-a will be in /sbin on some distros (which is annoying.)
        ua_path = "/usr/bin/update-alternatives"
        if not os.path.exists(ua_path):
            logger.warning("update-alternatives not found! Trying /sbin")
            ua_path = "/usr/sbin/update-alternatives"
        if os.path.exists(ua_path):
            # Debian Linux
            try:
                paths = subprocess.check_output(
                    [ua_path, "--list", "java"], encoding="utf8"
                )

                if re.match("^(/[^/ ]*)+/?$", paths):
                    return paths.split("\n")
            except Exception as e:
                logger.exception(f"Java Detect Error: {e}")
            return []  # handle all execution paths

        logger.warning("sbin/update-alternatives not found! Trying archlinux-java")
        ua_path = "/usr/bin/archlinux-java"
        if not os.path.exists(ua_path):
            logger.warning("archlinux-java not found! Trying /sbin")
            ua_path = "/usr/sbin/archlinux-java"
        if os.path.exists(ua_path):
            # Arch Linux
            try:
                paths = subprocess.check_output([ua_path, "status"], encoding="utf8")

                if matched := re.findall("(\\bjava-\\d+-[\\w/]+\\b)", paths):
                    return [
                        os.path.join(os.path.sep, "usr", "lib", "jvm", m, "bin", "java")
                        for m in matched
                    ]
            except Exception as e:
                logger.exception(f"Java Detect Error: {e}")
        return []  # handle all execution paths

    @staticmethod
    def float_to_string(gbs: float):
        s = str(float(gbs) * 1000).rstrip("0").rstrip(".")
        return s

    @staticmethod
    def check_file_perms(path):
        try:
            with open(path, "r", encoding="utf-8"):
                logger.info(f"{path} is readable")
            return True
        except (PermissionError, FileNotFoundError):
            return False

    @staticmethod
    def is_file_older_than_x_days(file, days=1):
        if Helpers.check_file_exists(file):
            file_time = os.path.getmtime(file)
            # Check against 24 hours
            return (time.time() - file_time) / 3600 > 24 * days
        logger.error(f"{file} does not exist")
        return True

    def get_servers_root_dir(self):
        return self.servers_dir

    @staticmethod
    def which_java():
        # Adapted from LeeKamentsky >>>
        # https://github.com/LeeKamentsky/python-javabridge/blob/master/javabridge/locate.py
        jdk_key_paths = (
            "SOFTWARE\\JavaSoft\\JDK",
            "SOFTWARE\\JavaSoft\\Java Development Kit",
        )
        for jdk_key_path in jdk_key_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, jdk_key_path) as kjdk:
                    kjdk_values = (
                        dict(  # pylint: disable=consider-using-dict-comprehension
                            [
                                winreg.EnumValue(kjdk, i)[:2]
                                for i in range(winreg.QueryInfoKey(kjdk)[1])
                            ]
                        )
                    )
                    current_version = kjdk_values["CurrentVersion"]
                    kjdk_current = winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE, jdk_key_path + "\\" + current_version
                    )
                    kjdk_current_values = (
                        dict(  # pylint: disable=consider-using-dict-comprehension
                            [
                                winreg.EnumValue(kjdk_current, i)[:2]
                                for i in range(winreg.QueryInfoKey(kjdk_current)[1])
                            ]
                        )
                    )
                    return kjdk_current_values["JavaHome"]
            except OSError as e:
                if e.errno == 2:
                    continue
                raise

    @staticmethod
    def check_internet():
        try:
            requests.get("https://ntp.org", timeout=1)
            return True
        except Exception:
            try:
                logger.error("ntp.org ping failed. Falling back to google")
                requests.get("https://google.com", timeout=1)
                return True
            except Exception:
                return False

    @staticmethod
    def check_address_status(address):
        try:
            response = requests.get(address, timeout=2)
            return (
                response.status_code // 100 == 2
            )  # Check if the status code starts with 2
        except requests.RequestException:
            return False

    @staticmethod
    def check_port(server_port):
        try:
            ip = get("https://api.ipify.org", timeout=1).content.decode("utf8")
        except Exception as e:
            logger.info(f"Unable to connect to api.ipify.org, \
                        falling back to google.com: {e}")
            ip = "google.com"
        a_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        a_socket.settimeout(20.0)

        location = (ip, server_port)
        result_of_check = a_socket.connect_ex(location)

        a_socket.close()

        return result_of_check == 0

    @staticmethod
    def check_server_conn(server_port):
        a_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        a_socket.settimeout(10.0)
        ip = "127.0.0.1"

        location = (ip, server_port)
        result_of_check = a_socket.connect_ex(location)
        a_socket.close()

        return result_of_check == 0

    def create_pass(self):
        # Maximum length of password needed
        max_len = 64

        # Declare string of the character that we need in our password
        digits = string.digits
        locase = string.ascii_lowercase
        upcase = string.ascii_uppercase
        symbols = "!@#$%^&*"  # Reducing to avoid issues with ([]{}<>,'`) etc

        # Combine all the character strings above to form one string
        combo = digits + upcase + locase + symbols

        # Randomly select at least one character from each character set above
        rand_digit = secrets.choice(digits)
        rand_upper = secrets.choice(upcase)
        rand_lower = secrets.choice(locase)
        rand_symbol = secrets.choice(symbols)

        # Combine the character randomly selected above
        temp_pass = rand_digit + rand_upper + rand_lower + rand_symbol

        # Fill the rest of the password length by selecting randomly char list
        for _ in range(max_len - 4):
            temp_pass += secrets.choice(combo)

        # Shuffle the temporary password to prevent predictable patterns
        temp_pass_list = list(temp_pass)
        secrets.SystemRandom().shuffle(temp_pass_list)

        # Form the password by concatenating the characters
        password = "".join(temp_pass_list)

        # Return completed password
        return password

    @staticmethod
    def cmdparse(cmd_in):
        # Parse a string into arguments
        cmd_out = []  # "argv" output array
        cmd_index = (
            -1
        )  # command index - pointer to the argument we're building in cmd_out
        new_param = True  # whether we're creating a new argument/parameter
        esc = False  # whether an escape character was encountered
        quote_char = None  # if we're dealing with a quote, save the quote type here.
        # Nested quotes to be dealt with by the command
        for char in cmd_in:  # for character in string
            if (
                new_param
            ):  # if set, begin a new argument and increment the command index.
                # Continue the loop.
                if char == " ":
                    continue
                cmd_index += 1
                cmd_out.append("")
                new_param = False
            if esc:  # if we encountered an escape character on the last loop,
                # append this char regardless of what it is
                if char not in Helpers.allowed_quotes:
                    cmd_out[cmd_index] += "\\"
                cmd_out[cmd_index] += char
                esc = False
            else:
                if char == "\\":  # if the current character is an escape character,
                    # set the esc flag and continue to next loop
                    esc = True
                elif (
                    char == " " and quote_char is None
                ):  # if we encounter a space and are not dealing with a quote,
                    # set the new argument flag and continue to next loop
                    new_param = True
                elif (
                    char == quote_char
                ):  # if we encounter the character that matches our start quote,
                    # end the quote and continue to next loop
                    quote_char = None
                elif quote_char is None and (
                    char in Helpers.allowed_quotes
                ):  # if we're not in the middle of a quote and we get a quotable
                    # character, start a quote and proceed to the next loop
                    quote_char = char
                else:  # else, just store the character in the current arg
                    cmd_out[cmd_index] += char
        return cmd_out

    def get_setting(self, key, default_return=False):
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if key in data.keys():
                return data.get(key)

            logger.error(f'Config File Error: Setting "{key}" does not exist')
            Console.error(f'Config File Error: Setting "{key}" does not exist')

        except Exception as e:
            logger.critical(
                f"Config File Error: Unable to read {self.settings_file} due to {e}"
            )
            Console.critical(
                f"Config File Error: Unable to read {self.settings_file} due to {e}"
            )

        return default_return

    def set_settings(self, data):
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

        except Exception as e:
            logger.critical(
                f"Config File Error: Unable to read {self.settings_file} due to {e}"
            )
            Console.critical(
                f"Config File Error: Unable to read {self.settings_file} due to {e}"
            )
            return False

        return True

    @staticmethod
    def get_categorized_settings(all_settings):
        # Start with empty dicts for each defined category
        categorized = {cat: {} for cat in CONFIG_CATEGORIES}

        miscellaneous = {}

        for key, value in all_settings.items():
            added = False
            for category, keys in CONFIG_CATEGORIES.items():
                if key in keys:
                    categorized[category][key] = value
                    added = True
                    break
            if not added:
                miscellaneous[key] = value

        # Add miscellaneous last
        if miscellaneous:
            for key, value in miscellaneous.items():
                categorized["miscellaneous"][key] = value

        return categorized

    @staticmethod
    def get_master_config():
        # Let's get the mounts and only show the first one by default
        mounts = Helpers.get_all_mounts()
        if len(mounts) != 0:
            mounts = mounts[0]
        # Make changes for users' local config.json files here. As of 4.0.20
        # Config.json was removed from the repo to make it easier for users
        # To make non-breaking changes to the file.
        MASTER_CONFIG["monitored_mounts"] = mounts
        return MASTER_CONFIG

    def get_all_settings(self):
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)

        except Exception as e:
            data = {}
            logger.critical(
                f"Config File Error: Unable to read {self.settings_file} due to {e}"
            )
            Console.critical(
                f"Config File Error: Unable to read {self.settings_file} due to {e}"
            )

        return data

    @staticmethod
    def get_all_mounts():
        mounts = []
        for item in psutil.disk_partitions(all=False):
            mounts.append(item.mountpoint)

        return mounts

    @staticmethod
    def is_subdir(child_path: str, parent_path: str) -> bool:
        """
        Checks if given child_path is a subdirectory of given parent_path. Returns True
        or False.

        Args:
            child_path: Child path to check.
            parent_path: Possible parent path of child path.

        Returns:
            True if child_path is a subdirectory of parent_path. Otherwise, False.

        """
        server_path = os.path.realpath(child_path)
        root_dir = os.path.realpath(parent_path)

        try:
            relative = os.path.relpath(server_path, root_dir)
            if relative.startswith(os.pardir):
                return False

        except ValueError:
            # Windows will crash out if two paths are on different Drives We can happily
            # return false if this is the case. Since two different drives will not be
            # relative to each-other.
            return False

        # If all checks pass, child path must be a child of parent.
        return True

    def set_setting(self, key, new_value):
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if key in data.keys():
                data[key] = new_value
                with open(self.settings_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                return True

            logger.error(f'Config File Error: Setting "{key}" does not exist')
            Console.error(f'Config File Error: Setting "{key}" does not exist')

        except Exception as e:
            logger.critical(
                f"Config File Error: Unable to read {self.settings_file} due to {e}"
            )
            Console.critical(
                f"Config File Error: Unable to read {self.settings_file} due to {e}"
            )
        return False

    def load_themes(self):
        theme_list = []
        themes_path = os.path.join(self.webroot, "static", "assets", "css", "themes")
        theme_files = [
            file
            for file in os.listdir(themes_path)
            if os.path.isfile(os.path.join(themes_path, file))
        ]
        for theme in theme_files:
            theme_list.append(theme.split(".css")[0])
        return theme_list

    def get_themes(self):
        return self.theme_list

    @staticmethod
    def get_local_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # doesn't even have to be reachable
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    def get_version(self):
        version_data = {}
        try:
            with open(
                os.path.join(self.config_dir, "version.json"), "r", encoding="utf-8"
            ) as f:
                version_data = json.load(f)

        except Exception as e:
            Console.critical(f"Unable to get version data! \n{e}")

        return version_data

    def check_migrations(self) -> None:
        if self.read_annc is False:
            self.read_annc = True
            for file in os.listdir(
                os.path.join(self.root_dir, "app", "migrations", "status")
            ):
                with open(
                    os.path.join(self.root_dir, "app", "migrations", "status", file),
                    "r",
                    encoding="utf-8",
                ) as notif_file:
                    file_json = json.load(notif_file)
                    for notif in file_json:
                        if not file_json[notif].get("status"):
                            self.migration_notifications.append(file_json[notif])

    def get_announcements(self, lang=None):
        try:
            data = []
            response = requests.get("https://craftycontrol.com/notify", timeout=2)
            data = json.loads(response.content)
            if not lang:
                lang = self.get_setting("language")
            self.check_migrations()
            for migration_warning in self.migration_notifications:
                if not migration_warning.get("status"):
                    data.append(
                        {
                            "id": migration_warning.get("pid"),
                            "title": self.translation.translate(
                                "notify",
                                f"{migration_warning.get('type')}_title",
                                lang,
                            ),
                            "date": "",
                            "desc": self.translation.translate(
                                "notify",
                                f"{migration_warning.get('type')}_desc",
                                lang,
                            ),
                            "link": "",
                        }
                    )
            if self.update_available:
                data.append(self.update_available)
            return data
        except Exception as e:
            logger.exception(f"Failed to fetch notifications with error: {e}")
            if self.update_available:
                data = [self.update_available]
            else:
                return False

    def get_version_string(self):
        version_data = self.get_version()
        major = version_data.get("major", "?")
        minor = version_data.get("minor", "?")
        sub = version_data.get("sub", "?")

        # set some defaults if we don't get version_data from our helper
        version = f"{major}.{minor}.{sub}"
        return str(version)

    @staticmethod
    def get_utc_now() -> datetime:
        return datetime.fromtimestamp(time.time(), tz=timezone.utc)

    def encode_pass(self, password):
        return self.passhasher.hash(password)

    def verify_pass(self, password, currenthash):
        try:
            self.passhasher.verify(currenthash, password)
            return True
        except:
            return False

    @staticmethod
    def strip_console_control(line):
        """Strip ANSI escape sequences and control characters from a line."""
        return ansi.strip_ansi(line)

    def console_settings(self):
        """Read the terminal rendering settings, cached against config.json.

        ``get_setting`` re-parses config.json on every call, too expensive to
        do per console line. Keying the cache on the file's mtime costs one
        stat and stays correct when the config is edited.

        :return: A :class:`ConsoleSettings`.
        """
        try:
            stamp = os.stat(self.settings_file).st_mtime_ns
        except OSError:
            stamp = None

        cached = self._console_settings_cache
        if cached is not None and cached[0] == stamp:
            return cached[1]

        keywords = self.get_setting("keywords") or []

        pattern = None
        if keywords:
            # One alternation beats one re.sub per keyword per line. Users
            # can put anything in this list, so escape it all if it does not
            # compile rather than let it kill every terminal line.
            joined = "|".join(f"(?:{keyword})" for keyword in keywords)
            try:
                pattern = re.compile(f"({joined})", flags=re.IGNORECASE)
            except re.error:
                joined = "|".join(re.escape(keyword) for keyword in keywords)
                pattern = re.compile(f"({joined})", flags=re.IGNORECASE)

        settings = ConsoleSettings(
            keywords=pattern,
            ansi=self.get_setting("ansi_terminal_colors", True),
            prefix_colors=self.get_setting("ansi_terminal_prefix_colors", True),
            links=self.get_setting("terminal_clickable_links", True),
        )
        self._console_settings_cache = (stamp, settings)
        return settings

    @staticmethod
    def console_text_renderer(settings):
        """Build the filter that marks up one run of escaped console text.

        Links are found first, so a keyword inside a URL cannot split it.
        Built once per line so the settings are not re-checked per text run.

        :param settings: The :class:`ConsoleSettings` to render with.
        :return: A callable taking HTML escaped text with no markup in it.
        """
        pattern = settings.keywords

        def highlight_keywords(escaped_text):
            if pattern is None:
                return escaped_text
            return pattern.sub(KEYWORD_LOG_HIGHLIGHT, escaped_text)

        if not settings.links:
            return highlight_keywords
        return lambda escaped_text: links.linkify(
            escaped_text, text_filter=highlight_keywords
        )

    def console_line_to_html(self, raw_line):
        """Render one raw console line as HTML for the virtual terminal.

        A line carrying ANSI colors is rendered from those colors. A line
        without them falls back to Crafty's regex highlighting.

        :param raw_line: The line as the server printed it, unescaped.
        :return: An HTML fragment.
        """
        settings = self.console_settings()
        render_text = self.console_text_renderer(settings)

        if not ansi.has_ansi(raw_line):
            return self._log_colors(html.escape(raw_line), render_text)

        if not settings.ansi:
            return self._log_colors(html.escape(ansi.strip_ansi(raw_line)), render_text)

        prefix_html = ""
        skip_chars = 0
        if settings.prefix_colors:
            # Crafty colors the prefix; the renderer picks up after it, still
            # reading the escapes inside it so the message keeps its color.
            match = LOG_PREFIX_RE.match(ansi.strip_ansi(raw_line))
            if match is not None:
                prefix_html = self._log_colors(html.escape(match.group()), render_text)
                skip_chars = match.end()

        return prefix_html + ansi.ansi_to_html(
            raw_line, text_filter=render_text, skip_chars=skip_chars
        )

    def log_colors(self, line):
        """Color the log level, timestamp and keywords in a console line."""
        return self._log_colors(
            line, self.console_text_renderer(self.console_settings())
        )

    @staticmethod
    def _log_colors(line, render_text):
        """Runs on every line a server prints, so skip passes that cannot match.

        :param render_text: From :meth:`console_text_renderer`.
        """
        lowered = line.lower()

        for token, pattern, replacement in LOG_REPLACEMENTS:
            if token in lowered:
                line = pattern.sub(replacement, line)

        # Only the text between the tags added above came from the server.
        return links.apply_to_text(line, render_text)

    @staticmethod
    def validate_traversal(base_path, filename):
        logger.debug(f'Validating traversal ("{base_path}", "{filename}")')
        base = pathlib.Path(base_path).resolve()
        file = pathlib.Path(filename)
        fileabs = base.joinpath(file).resolve()
        common_path = pathlib.Path(os.path.commonpath([base, fileabs]))
        if base == common_path:
            return fileabs
        raise ValueError("Path traversal detected")

    @staticmethod
    def is_path_within(base_path, filename) -> bool:
        """Return True if filename resolves inside base_path.

        Boolean form of ``validate_traversal`` for ``if not`` checks.

        Args:
            base_path: Directory the path must stay within.
            filename: Path to check, absolute or relative to base_path.

        Returns:
            bool: ``True`` if the path stays inside base_path, else ``False``.
        """
        try:
            Helpers.validate_traversal(base_path, filename)
        except ValueError:
            return False
        return True

    @staticmethod
    def resolve_log_path(log_path):
        """Resolve a server log path that may be a glob pattern to a concrete file.

        Server types such as Hytale write a new timestamped log file on every boot
        (e.g. ``2026-02-18_01-21-32_server.log``) rather than reusing a fixed name like
        Minecraft's ``latest.log``. Storing a glob pattern in ``log_path`` lets the
        Logs tab always follow the active file.

        :param log_path: A path that may contain glob metacharacters (``* ? [``). It is
            expected to already be joined with the server directory by the caller.
        :return: The newest matching file by modification time as a
            :class:`pathlib.Path`. A path with no glob characters is returned unchanged.
            If the pattern matches nothing, it is returned as-is so downstream
            "file not found" handling still fires.
        """
        log_path = str(log_path)

        # Check for magic chars, return early if none present
        if not re.search(r"[*?[]", log_path):
            return pathlib.Path(log_path)

        matches = glob.glob(log_path)
        if not matches:
            return pathlib.Path(log_path)

        # Newest by mtime reflects the file currently being written, which stays
        # correct even if the naming scheme changes or files are copied in.
        return pathlib.Path(max(matches, key=os.path.getmtime))

    @staticmethod
    def tail_file(file_name, number_lines=20):
        if not Helpers.check_file_exists(file_name):
            logger.warning(f"Unable to find file to tail: {file_name}")
            return [f"Unable to find file to tail: {file_name}"]

        if str(file_name).endswith(".gz"):
            # Gzip archives can't be seeked into by byte offset like a plain
            # text file, so decompress fully and keep only the last N lines.
            try:
                with gzip.open(file_name, "rt", encoding="utf-8") as f:
                    return f.readlines()[-number_lines:]
            except OSError as e:
                logger.warning(
                    f"Unable to read gzip file:{file_name} - due to error: {e}"
                )
                return [f"Unable to read gzip file: {file_name}"]

        # length of lines is X char here
        avg_line_length = 255

        # create our buffer number - number of lines * avg_line_length
        line_buffer = number_lines * avg_line_length

        # open our file
        with open(file_name, "r", encoding="utf-8") as f:
            # seek
            f.seek(0, 2)

            # get file size
            fsize = f.tell()

            # set pos @ last n chars
            # (buffer from above = number of lines * avg_line_length)
            f.seek(max(fsize - line_buffer, 0), 0)

            # read file til the end
            try:
                lines = f.readlines()

            except Exception as e:
                logger.warning(
                    f"Unable to read a line in the file:{file_name} - due to error: {e}"
                )

        # now we are done getting the lines, let's return it
        return lines

    @staticmethod
    def check_writeable(path: str):
        filename = os.path.join(path, "tempfile.txt")
        try:
            with open(filename, "w", encoding="utf-8"):
                pass
            os.remove(filename)

            logger.info(f"{filename} is writable")
            return True

        except Exception as e:
            logger.critical(f"Unable to write to {path} - Error: {e}")
            return False

    @staticmethod
    def check_root():
        if Helpers.is_os_windows():
            return ctypes.windll.shell32.IsUserAnAdmin() == 1
        return os.geteuid() == 0

    def ensure_logging_setup(self):
        log_file = os.path.join(os.path.curdir, "logs", "commander.log")
        session_log_file = os.path.join(os.path.curdir, "logs", "session.log")

        logger.info("Checking app directory writable")

        writeable = Helpers.check_writeable(self.root_dir)

        # if not writeable, let's bomb out
        if not writeable:
            logger.critical(f"Unable to write to {self.root_dir} directory!")
            sys.exit(1)

        # ensure the log directory is there
        try:
            with suppress(FileExistsError):
                os.makedirs(os.path.join(self.root_dir, "logs"))
        except Exception as e:
            Console.error(f"Failed to make logs directory with error: {e} ")

        # ensure the log file is there
        try:
            with open(log_file, "a", encoding="utf-8"):
                pass
        except Exception as e:
            Console.critical(f"Unable to open log file! {e}")
            sys.exit(1)

        # del any old session.lock file as this is a new session
        try:
            with contextlib.suppress(FileNotFoundError):
                os.remove(session_log_file)
        except Exception as e:
            Console.error(f"Deleting logs/session.log failed with error: {e}")

    @staticmethod
    def get_time_as_string():
        now = datetime.now()
        return now.strftime("%m/%d/%Y, %H:%M:%S")

    @staticmethod
    def calc_percent(source_path, dest_path):
        # calculates percentable of zip from drive. Not with compression.
        # (For backups and support logs)
        source_size = 0
        files_count = 0
        for path, _dirs, files in os.walk(source_path):
            for file in files:
                full_path = os.path.join(path, file)
                source_size += os.stat(full_path).st_size
                files_count += 1
        try:
            dest_size = os.path.getsize(str(dest_path))
            percent = round((dest_size / source_size) * 100, 1)
        except:
            percent = 0
        if percent >= 0:
            results = {"percent": percent, "total_files": files_count}
        else:
            results = {"percent": 0, "total_files": files_count}
        return results

    @staticmethod
    def check_file_exists(path: str):
        logger.debug(f"Looking for path: {path}")

        if os.path.exists(path) and os.path.isfile(path):
            logger.debug(f"Found path: {path}")
            return True
        return False

    @staticmethod
    def human_readable_file_size(num: int, suffix="B"):
        for unit in ["", "K", "M", "G", "T", "P", "E", "Z"]:
            if abs(num) < 1024.0:
                # pylint: disable=consider-using-f-string
                return "%3.1f%s%s" % (num, unit, suffix)
            num /= 1024.0
            # pylint: disable=consider-using-f-string
        return "%.1f%s%s" % (num, "Y", suffix)

    @staticmethod
    def check_path_exists(path: str):
        if not path:
            return False
        logger.debug(f"Looking for path: {path}")

        if os.path.exists(path):
            logger.debug(f"Found path: {path}")
            return True
        return False

    def get_gravatar_image(self, email):
        profile_url = "/static/assets/images/faces-clipart/pic-3.png"
        # http://en.gravatar.com/site/implement/images/#rating
        if self.get_setting("allow_nsfw_profile_pictures"):
            rating = "x"
        else:
            rating = "g"

        # Get grvatar hash for profile pictures
        if self.check_internet() and email != "default@example.com" and email:
            gravatar = libgravatar.Gravatar(libgravatar.sanitize_email(email))
            url = gravatar.get_image(
                size=80,
                default="404",
                force_default=False,
                rating=rating,
                filetype_extension=False,
                use_ssl=True,
            )  # + "?d=404"
            try:
                if requests.head(url, timeout=1).status_code != 404:
                    profile_url = url
            except Exception as e:
                logger.debug(f"Could not pull resource from Gravatar with error {e}")

        return profile_url

    @staticmethod
    def get_file_contents(path: str, lines=100):
        contents = ""

        if os.path.exists(path) and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f.readlines()[-lines:]:
                        contents = contents + line

                return contents

            except Exception as e:
                logger.exception(f"Unable to read file: {path}. \n Error: {e}")
                return False
        else:
            logger.error(
                f"Unable to read file: {path}. File not found, or isn't a file."
            )
            return False

    def create_session_file(self, ignore=False):
        if ignore and os.path.exists(self.session_file):
            os.remove(self.session_file)

        if os.path.exists(self.session_file):
            file_data = self.get_file_contents(self.session_file)
            try:
                data = json.loads(file_data)
                pid = data.get("pid")
                started = data.get("started")
                if psutil.pid_exists(pid):
                    Console.critical(
                        f"Another Crafty Controller agent seems to be running..."
                        f"\npid: {pid} \nstarted on: {started}"
                    )
                    logger.critical("Found running crafty process. Exiting.")
                    sys.exit(1)
                else:
                    logger.info(
                        "No process found for pid. Assuming "
                        "crafty crashed. Deleting stale session.lock"
                    )
                    os.remove(self.session_file)

            except Exception as e:
                logger.exception(
                    f"Failed to locate existing session.lock with error: {e} "
                )
                Console.error(
                    f"Failed to locate existing session.lock with error: {e} "
                )

                sys.exit(1)

        pid = os.getpid()
        now = datetime.now()

        session_data = {"pid": pid, "started": now.strftime("%d-%m-%Y, %H:%M:%S")}
        with open(self.session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=4)

    @staticmethod
    def list_dir_by_date(path: str, reverse=False):
        return [
            str(p)
            for p in sorted(
                pathlib.Path(path).iterdir(), key=os.path.getmtime, reverse=reverse
            )
        ]

    @staticmethod
    def get_human_readable_files_sizes(paths: list):
        sizes = []
        for p in paths:
            sizes.append(
                {
                    "path": p,
                    "size": Helpers.human_readable_file_size(os.stat(p).st_size),
                }
            )
        return sizes

    @staticmethod
    def base64_encode_string(fun_str: str):
        s_bytes = str(fun_str).encode("utf-8")
        b64_bytes = base64.encodebytes(s_bytes)
        return b64_bytes.decode("utf-8")

    @staticmethod
    def base64_decode_string(fun_str: str):
        s_bytes = str(fun_str).encode("utf-8")
        b64_bytes = base64.decodebytes(s_bytes)
        return b64_bytes.decode("utf-8")

    @staticmethod
    def create_uuid():
        return str(uuid.uuid4())

    @staticmethod
    def ensure_dir_exists(path):
        """
        ensures a directory exists

        Checks for the existence of a directory, if the directory isn't there,
        this function creates the directory

        Args:
            path (string): the path you are checking for

        """

        try:
            os.makedirs(path)
            logger.debug(f"Created Directory : {path}")
            return True

        # directory already exists - non-blocking error
        except FileExistsError:
            return True
        except PermissionError as e:
            logger.critical(f"Check generated exception due to permssion error: {e}")
            return False
        except FileNotFoundError as e:
            logger.critical(
                f"Check generated exception due to file does not exist error: {e}"
            )
            return False

    def create_self_signed_cert(self, cert_dir=None):
        """
        Creates a self-signed SSL certificate and private key, writing them to disk.

        Parameters:
            - cert_dir (str, optional): The directory where the certificate and key
            files will be created.
                If not provided, a default path under the config_dir is used.

        Returns:
            - bool: True if the certificate and key are created,
              False otherwise.

        Raises:
            - OSError: If there are issues creating files or directories.

        Exception:
            Any unexpected error that occurs during certificate or key generation.

        Note:
        This function generates an RSA key pair and an X.509 certificate using
        the cryptography library.
        The resulting files are saved in PEM format.
        """
        if cert_dir is None:
            cert_dir = os.path.join(self.config_dir, "web", "certs")

        # Ensure directory exists
        Helpers.ensure_dir_exists(cert_dir)

        cert_file = os.path.join(cert_dir, "commander.cert.pem")
        key_file = os.path.join(cert_dir, "commander.key.pem")

        logger.info(f"SSL Cert File is set to: {cert_file}")
        logger.info(f"SSL Key File is set to: {key_file}")

        # Don't create new files if we already have them.
        if Helpers.check_file_exists(cert_file) and Helpers.check_file_exists(key_file):
            logger.info("Cert and Key files already exists, not creating them.")
            return False

        Console.info("Generating a self signed SSL")
        logger.info("Generating a self signed SSL")

        # Generate private key
        logger.info("Generating a key pair. This might take a moment.")
        Console.info("Generating a key pair. This might take a moment.")
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
        )

        # Build certificate subject/issuer meta
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
                x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Indiana"),
                x509.NameAttribute(
                    NameOID.ORGANIZATION_NAME, "Arcadia Technology, LLC"
                ),
                x509.NameAttribute(
                    NameOID.ORGANIZATIONAL_UNIT_NAME, "Crafty Controller"
                ),
                x509.NameAttribute(NameOID.COMMON_NAME, gethostname()),
                x509.NameAttribute(NameOID.EMAIL_ADDRESS, "info@arcadiatech.org"),
            ]
        )

        # Build subject Alternative Names
        alt_names = [
            socket.gethostname(),
            f"*.{socket.gethostname()}",
            "localhost",
            "*.localhost",
            "*.local",
            "127.0.0.1",
        ]
        san = x509.SubjectAlternativeName([x509.DNSName(name) for name in alt_names])

        # Construct certificate
        cert_builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(secrets.randbelow(254) + 1)  # rand serial
            .not_valid_before(datetime.now(timezone.utc))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))  # 1 yr
            .add_extension(san, critical=False)
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
        )

        # Sign certificate
        certificate = cert_builder.sign(
            private_key=private_key, algorithm=hashes.SHA256()
        )

        # Write cert and priv key to disk
        with open(cert_file, "wb") as cert_file_handle:
            cert_file_handle.write(certificate.public_bytes(serialization.Encoding.PEM))
        with open(key_file, "wb") as key_file_handle:
            key_file_handle.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )

        logger.info("Self-signed certificate and key generation completed.")
        Console.info("Self-signed certificate and key generation completed.")
        return True

    @staticmethod
    def random_string_generator(size=6, chars=string.ascii_uppercase + string.digits):
        """
        Example Usage
        random_generator() = G8sjO2
        random_generator(3, abcdef) = adf
        """
        return "".join(secrets.choice(chars) for _ in range(size))

    @staticmethod
    def is_os_windows():
        return os.name == "nt"

    @staticmethod
    def is_env_docker():
        path = "/proc/self/cgroup"
        return (
            os.path.exists("/.dockerenv")
            or os.path.isfile(path)
            and any("docker" in line for line in open(path, encoding="utf-8"))
        )

    @staticmethod
    def wtol_path(w_path):
        l_path = w_path.replace("\\", "/")
        return l_path

    @staticmethod
    def ltow_path(l_path):
        w_path = l_path.replace("/", "\\")
        return w_path

    @staticmethod
    def get_os_understandable_path(path):
        return os.path.normpath(path)

    def find_default_password(self):
        default_file = os.path.join(self.root_dir, "app", "config", "default.json")
        data = {}

        if Helpers.check_file_exists(default_file):
            with open(default_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            del_json = self.get_setting("delete_default_json")

            if del_json:
                os.remove(default_file)

        return data

    @staticmethod
    def generate_zip_tree(folder, output=""):
        file_list = os.listdir(folder)
        file_list = sorted(file_list, key=str.casefold)
        output += f"""<ul class="tree-nested d-block" id="{folder}ul">"""
        for raw_filename in file_list:
            filename = html.escape(raw_filename)
            rel = os.path.join(folder, raw_filename)
            dpath = os.path.join(folder, filename)
            if os.path.isdir(rel):
                # lines below had too long warnings disabled for readability
                output += (
                    f"""<li class="tree-item" data-path="{dpath}">"""
                    + """\n<div id="{dpath}" data-path="{dpath}" data-name="{filename}" class="tree-caret tree-ctx-item tree-folder">"""  # pylint: disable=line-too-long
                    + """<input type="radio" name="root_path" value="{dpath}">"""
                    + """<span id="{dpath}span" class="files-tree-title" data-path="{dpath}" data-name="{filename}" onclick="getDirView(event)">"""  # pylint: disable=line-too-long
                    + """  <i class="text-info ph ph-folder"></i>"""
                    + """  <i class="text-info ph ph-folder-open"></i>"""
                    + """  {filename}"""
                    + """  </span>"""
                    + """</input></div><li>"""
                    + """\n"""
                )
        return output

    @staticmethod
    def generate_zip_dir(folder, output=""):
        file_list = os.listdir(folder)
        file_list = sorted(file_list, key=str.casefold)
        output += f"""<ul class="tree-nested d-block" id="{folder}ul">"""
        for raw_filename in file_list:
            filename = html.escape(raw_filename)
            rel = os.path.join(folder, raw_filename)
            dpath = os.path.join(folder, filename)
            if os.path.isdir(rel):
                # lines below had too long warnings disabled for readability
                output += (
                    f"""<li class="tree-item" data-path="{dpath}">"""
                    + """\n<div id="{dpath}" data-path="{dpath}" data-name="{filename}" class="tree-caret tree-ctx-item tree-folder">"""  # pylint: disable=line-too-long
                    + """<input type="radio" name="root_path" value="{dpath}">"""
                    + """<span id="{dpath}span" class="files-tree-title" data-path="{dpath}" data-name="{filename}" onclick="getDirView(event)">"""  # pylint: disable=line-too-long
                    + """  <i class="text-info ph ph-folder"></i>"""
                    + """  <i class="text-info ph ph-folder-open"></i>"""
                    + """  {filename}"""
                    + """  </span>"""
                    + """</input></div><li>"""
                )
        return output

    @staticmethod
    def remove_prefix(text, prefix):
        if text.startswith(prefix):
            return text[len(prefix) :]
        return text

    @staticmethod
    def get_lang_page(text) -> str:
        splitted = text.split("_")
        if len(splitted) != 2:
            return "en"
        lang, region = splitted
        if region == "EN":
            return "en"
        return lang + "-" + region

    @staticmethod
    def get_player_avatar(uuid_player):
        mojang_response = requests.get(
            f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid_player}",
            timeout=10,
        )
        if mojang_response.status_code == 200:
            uuid_profile = mojang_response.json()
            profile_properties = uuid_profile["properties"]
            for prop in profile_properties:
                if prop["name"] == "textures":
                    decoded_bytes = base64.b64decode(prop["value"])
                    decoded_str = decoded_bytes.decode("utf-8")
                    texture_json = json.loads(decoded_str)
                    skin_url = texture_json["textures"]["SKIN"]["url"]
                    skin_response = requests.get(skin_url, stream=True, timeout=10)
                    if skin_response.status_code == 200:
                        return base64.b64encode(skin_response.content)
        else:
            return
