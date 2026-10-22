import datetime
import html
import io
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import redirect_stderr
import queue
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import peewee
from apscheduler.jobstores.base import ConflictingIdError, JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler

# OpenMetrics/Prometheus Imports
from prometheus_client import CollectorRegistry, Gauge, Info

# TZLocal is set as a hidden import on win pipeline
from tzlocal import get_localzone

from app.classes.helpers.file_helpers import FileHelpers
from app.classes.helpers.helpers import Helpers
from app.classes.models.management import HelpersManagement, HelpersWebhooks
from app.classes.models.server_permissions import (
    EnumPermissionsServer,
    PermissionsServers,
)
from app.classes.models.server_stats import HelperServerStats
from app.classes.models.servers import HelperServers, Servers
from app.classes.models.users import HelperUsers
from app.classes.network.network_manager import NetworkManager
from app.classes.remote_stats.nitrado_ping import NitradoPing
from app.classes.remote_stats.ping import ping, ping_raknet
from app.classes.remote_stats.stats import Stats
from app.classes.shared.console import Console
from app.classes.shared.null_writer import NullWriter
from app.classes.shared.update_mgr import UpdateManager
from app.classes.shared.websocket_manager import WebSocketManager
from app.classes.web.webhooks.webhook_factory import WebhookFactory

with redirect_stderr(NullWriter()):
    import psutil
    from psutil import NoSuchProcess

logger = logging.getLogger(__name__)
SUCCESSMSG = "SUCCESS! Forge install completed"
SERVER_DETAIL_URL = "/panel/server_detail"
EULA_FILE = "eula.txt"


def extract_backup_info(res) -> dict:
    if not isinstance(res, dict):
        return {}
    return {
        "backup_name": res.get("backup_name"),
        "backup_size": str(res.get("backup_size")),
        "backup_link": res.get("backup_link"),
        "backup_status": res.get("backup_status"),
        "backup_error": res.get("backup_error"),
    }


def build_event_data(server, command, event_type, backup_info):
    event_data = {
        "server_name": server.name,
        "server_id": server.server_id,
        "command": command,
        "event_type": event_type,
        **backup_info,
    }
    return event_data


def process_webhook(swebhook, server, command, event_type, res):
    webhook = HelpersWebhooks.get_webhook_by_id(swebhook.id)
    webhook_provider = WebhookFactory.create_provider(webhook["webhook_type"])

    backup_info = extract_backup_info(res)
    event_data = build_event_data(server, command, event_type, backup_info)
    event_data = webhook_provider.add_time_variables(event_data)

    if res is not False and swebhook.enabled:
        webhook_provider.send(
            server_name=server.name,
            title=webhook["name"],
            url=webhook["url"],
            message_template=webhook["body"],
            event_data=event_data,
            color=webhook["color"],
            bot_name=webhook["bot_name"],
        )


def send_webhook(event_type: str, res, command: str, args):
    server = args[0]
    server_webhooks = HelpersWebhooks.get_webhooks_by_server(server.server_id, True)
    for swebhook in server_webhooks:
        if event_type in str(swebhook.trigger).split(","):
            logger.info(
                f"Found callback for event {event_type} for server {server.server_id}"
            )
            process_webhook(swebhook, server, command, event_type, res)


def callback(called_func):
    # Usage of @callback on method
    # definition to run a webhook check
    # on method completion
    def wrapper(*args, **kwargs):
        res = None
        logger.debug("Checking for callbacks")
        try:
            res = called_func(*args, **kwargs)  # Calls and runs the function
        finally:
            event_type = called_func.__name__

            # For send_command, Retrieve command from args or kwargs
            command = args[1] if len(args) > 1 else kwargs.get("command", "")

            if event_type in WebhookFactory.get_monitored_events():
                send_webhook(event_type, res, command, args)
        return res

    return wrapper


class ServerOutBuf:
    lines = {}

    def __init__(self, helper, proc, server_id):
        self.helper = helper
        self.proc = proc
        self.server_id = str(server_id)
        # Buffers text for virtual_terminal_lines config number of lines
        self.max_lines = self.helper.get_setting("virtual_terminal_lines")
        self.line_buffer = ""
        ServerOutBuf.lines[self.server_id] = []

    def start_reader(self):
        self._queue = queue.Queue()

        def reader():
            text_wrapper = io.TextIOWrapper(
                self.proc.stdout,
                encoding="UTF-8",
                errors="ignore",
                newline=None,
                line_buffering=True,
            )

            while True:
                line = text_wrapper.readline()
                if not line:
                    break
                self._queue.put(line)

        t = threading.Thread(target=reader, daemon=True)
        t.start()

    def process_line(self, line):
        linetemp = line.rstrip("\n")
        new_lines = linetemp.split("\n")

        for tmp in new_lines:
            ServerOutBuf.lines[self.server_id].append(tmp)

        self.new_line_handler(linetemp)

        # Limit list length to self.max_lines:
        if len(ServerOutBuf.lines[self.server_id]) > self.max_lines:
            x = len(ServerOutBuf.lines[self.server_id]) - self.max_lines
            del ServerOutBuf.lines[self.server_id][:x]

    def check(self, batch_size=20, timeout=0.1):

        buffer = []
        self.start_reader()

        while True:
            # Check if new data available
            # rlist, _, _ = select.select([fd], [], [], timeout)

            try:
                line = self._queue.get(timeout=timeout, block=True)
                buffer.append(line)

                if len(buffer) >= batch_size:
                    self.process_line("".join(buffer))
                    buffer.clear()

            except queue.Empty:
                # If timeout then flush
                if buffer:
                    self.process_line("".join(buffer))
                    buffer.clear()

            if self.proc.poll() is not None and self._queue.empty():
                if buffer:
                    self.process_line("".join(buffer))
                break

    def new_line_handler(self, new_line):
        new_line = re.sub("(\x1b\\[(0;)?\\d*[A-z]?(;\\d)?m?)", " ", new_line)
        new_line = re.sub("[A-z]{2}\b\b", "", new_line)
        highlighted = self.helper.log_colors(html.escape(new_line))

        logger.debug("Broadcasting new virtual terminal line")

        if len(WebSocketManager().clients) > 0:
            WebSocketManager().broadcast_page_params(
                SERVER_DETAIL_URL,
                {"id": self.server_id},
                "vterm_new_line",
                {"line": highlighted + "<br />"},
                required_permission=EnumPermissionsServer.TERMINAL,
            )


# **********************************************************************************
#                               Minecraft Server Class
# **********************************************************************************
class ServerInstance:
    server_object: Servers
    helper: Helpers
    file_helper: FileHelpers
    management_helper: HelpersManagement
    stats: Stats
    stats_helper: HelperServerStats

    def __init__(
        self,
        server_id,
        helper,
        management_helper,
        stats,
        file_helper,
        backup_mgr,
        import_helper,
    ):
        self.helper = helper
        self.file_helper = file_helper
        self.management_helper = management_helper
        self.backup_mgr = backup_mgr
        self.import_helper = import_helper
        # holders for our process
        self.process = None
        self.line = False
        self.start_time = None
        self.server_command = None
        self.server_path = None
        self.server_thread = None
        self.settings = {}
        self.updating = False
        self.server_id = server_id
        self.jar_update_url = None
        self.name = None
        self.is_crashed = False
        self.restart_count = 0
        self._game_port_cache = None
        self.stats = stats
        self.server_object = HelperServers.get_server_obj(self.server_id)
        self.stats_helper = HelperServerStats(self.server_id)
        self.last_backup_failed = False
        self.server_registry = CollectorRegistry()

        try:
            with open(
                os.path.join(
                    self.helper.root_dir,
                    "app",
                    "config",
                    "db",
                    "servers",
                    self.server_id,
                    "players_cache.json",
                ),
                "r",
                encoding="utf-8",
            ) as f:
                self.player_cache = list(json.load(f).values())
        except (OSError, json.decoder.JSONDecodeError):
            self.player_cache = []
        try:
            self.tz = get_localzone()
        except ZoneInfoNotFoundError as e:
            logger.exception(
                "Could not capture time zone from system. Falling back to Europe/London"
                f" error: {e}"
            )
            self.tz = ZoneInfo("Europe/London")
        self.server_scheduler = BackgroundScheduler(timezone=str(self.tz))
        self.dir_scheduler = BackgroundScheduler(timezone=str(self.tz))
        self.init_registries()
        self.server_scheduler.start()
        self.dir_scheduler.start()
        self.start_dir_calc_task()
        self.is_backingup = False
        # Reset crash and update at initialization
        self.stats_helper.server_crash_reset()
        self.stats_helper.set_update(False)
        # Start update watcher
        self.update_manager = UpdateManager(
            self.import_helper, self.helper, self.file_helper
        )
        self.server_scheduler.add_job(
            self.update_manager.check_server_version,
            "interval",
            hours=12,
            id=f"{str(self.server_id)}_update_watcher",
            args=[self.settings],
        )

    # **********************************************************************************
    #                               Minecraft Server Management
    # **********************************************************************************
    def update_server_instance(self):
        server_data: Servers = HelperServers.get_server_obj(self.server_id)
        self.server_path = server_data.path
        self.jar_update_url = server_data.executable_update_url
        self.name = server_data.server_name
        self.server_object = server_data
        self.stats_helper.select_database()
        self.reload_server_settings()

    def reload_server_settings(self):
        server_data = HelperServers.get_server_data_by_id(self.server_id)
        self.settings = server_data

    def do_server_setup(self, server_data_obj):
        server_id = server_data_obj["server_id"]
        server_name = server_data_obj["server_name"]
        auto_start = server_data_obj["auto_start"]

        logger.info(
            f"Creating Server object: {server_id} | "
            f"Server Name: {server_name} | "
            f"Auto Start: {auto_start}"
        )
        self.server_id = server_id
        self.name = server_name
        self.settings = server_data_obj
        # Check update relies on up to date information from self.settings.
        self.update_manager.check_server_version(self.settings)
        # Running it after instead of during init function

        self.record_server_stats()

        # build our server run command

        if server_data_obj["auto_start"]:
            delay = int(self.settings["auto_start_delay"])

            logger.info(f"Scheduling server {self.name} to start in {delay} seconds")
            Console.info(f"Scheduling server {self.name} to start in {delay} seconds")

            self.server_scheduler.add_job(
                self.run_scheduled_server,
                "interval",
                seconds=delay,
                id=str(self.server_id),
            )

    def run_scheduled_server(self):
        Console.info(f"Starting server ID: {self.server_id} - {self.name}")
        logger.info(f"Starting server ID: {self.server_id} - {self.name}")
        # Sets waiting start to false since we're attempting to start the server.
        self.stats_helper.set_waiting_start(False)
        self.run_threaded_server(None)

        # remove the scheduled job since it's ran
        return self.server_scheduler.remove_job(str(self.server_id))

    def run_threaded_server(self, user_id):
        # start the server
        self.server_thread = threading.Thread(
            target=self.start_server,
            daemon=True,
            args=(user_id,),
            name=f"{self.server_id}_server_thread",
        )
        self.server_thread.start()

    def check_startup_java(self):
        logger.info(
            "Detected nebulous java in start command. Replacing with full java path."
        )
        oracle_path = shutil.which("java")
        if oracle_path:
            # Checks for Oracle Java. Only Oracle Java's helper will cause a re-exec
            if "/Oracle/Java/" in str(self.helper.wtol_path(oracle_path)):
                logger.info(
                    "Oracle Java detected. Changing start command to avoid re-exec."
                )
                which_java_raw = self.helper.which_java()
                try:
                    java_path = which_java_raw + "\\bin\\java"
                except TypeError:
                    logger.warning(
                        "Could not find java in the registry even though"
                        " Oracle java is installed."
                        " Re-exec expected, but we have no"
                        " other options. CPU stats will not work for process."
                    )
                    java_path = ""
                if str(which_java_raw) != str(self.helper.get_servers_root_dir) or str(
                    self.helper.get_servers_root_dir
                ) in str(which_java_raw):
                    if java_path != "":
                        self.server_command[0] = java_path
                else:
                    logger.error(
                        "Possible attack detected. User attempted to exec "
                        "java binary from server directory."
                    )
                    raise PermissionError(
                        "Possible attack detected. User attempted to exec "
                        "java binary from server directory."
                    )

    def setup_server_run_command(self):
        # configure the server
        server_exec_path = Helpers.get_os_understandable_path(
            self.settings["executable"]
        )
        self.server_command = Helpers.cmdparse(self.settings["execution_command"])
        if self.helper.is_os_windows() and self.server_command[0] == "java":
            try:
                self.check_startup_java()
            except PermissionError:
                return
        self.server_path = Helpers.get_os_understandable_path(self.settings["path"])

        # let's do some quick checking to make sure things actually exists
        full_path = os.path.join(self.server_path, server_exec_path)
        if not Helpers.check_file_exists(full_path):
            logger.critical(
                f"Server executable path: {full_path} does not seem to exist"
            )
            Console.critical(
                f"Server executable path: {full_path} does not seem to exist"
            )

        if not Helpers.check_path_exists(self.server_path):
            logger.critical(f"Server path: {self.server_path} does not seem to exits")
            Console.critical(f"Server path: {self.server_path} does not seem to exits")

        if not Helpers.check_writeable(self.server_path):
            logger.critical(f"Unable to write/access {self.server_path}")
            Console.critical(f"Unable to write/access {self.server_path}")

    def can_server_start(self, user_id, user_lang):
        # Checks if user is currently attempting to move global server
        # dir
        if self.helper.dir_migration:
            WebSocketManager().broadcast_user(
                user_id,
                "send_error",
                {
                    "error": self.helper.translation.translate(
                        "error",
                        "migration",
                        user_lang,
                    )
                },
            )
            return False

        if self.stats_helper.get_import_status():
            if user_id:
                WebSocketManager().broadcast_user(
                    user_id,
                    "send_error",
                    {
                        "error": self.helper.translation.translate(
                            "error", "not-downloaded", user_lang
                        )
                    },
                )
            return False

        if self.check_running():
            logger.error("Server is already running - Cancelling Startup")
            Console.error("Server is already running - Cancelling Startup")
            return False

        if self.check_update():
            logger.error("Server is updating. Terminating startup.")
            return False
        return True

    def do_generic_start(self, user_id, user_lang):
        try:
            self.process = subprocess.Popen(
                self.server_command,
                cwd=self.server_path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except Exception as ex:
            # Checks for java on initial fail
            if not self.helper.detect_java():
                if user_id:
                    WebSocketManager().broadcast_user(
                        user_id,
                        "send_error",
                        {
                            "error": self.helper.translation.translate(
                                "error", "noJava", user_lang
                            ).format(self.name)
                        },
                    )
                return False
            logger.exception(
                f"Server {self.name} failed to start with error code: {ex}"
            )
            if user_id:
                WebSocketManager().broadcast_user(
                    user_id,
                    "send_error",
                    {
                        "error": self.helper.translation.translate(
                            "error", "start-error", user_lang
                        ).format(self.name, ex)
                    },
                )

    def do_minecraft_bedrock_start(self, user_id, user_lang):
        if Helpers.is_os_windows():
            try:
                self.process = subprocess.Popen(
                    self.server_command,
                    cwd=self.server_path,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
            except Exception as ex:
                logger.exception(
                    f"Server {self.name} failed to start with error code: {ex}"
                )
                if user_id:
                    WebSocketManager().broadcast_user(
                        user_id,
                        "send_error",
                        {
                            "error": self.helper.translation.translate(
                                "error", "start-error", user_lang
                            ).format(self.name, ex)
                        },
                    )
            return

        logger.info(
            f"Bedrock and Unix detected for server {self.name}. "
            f"Switching to appropriate execution string"
        )
        my_env = os.environ
        my_env["LD_LIBRARY_PATH"] = self.server_path
        try:
            self.process = subprocess.Popen(
                self.server_command,
                cwd=self.server_path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=my_env,
            )
        except Exception as ex:
            logger.exception(
                f"Server {self.name} failed to start with error code: {ex}"
            )
            if user_id:
                WebSocketManager().broadcast_user(
                    user_id,
                    "send_error",
                    {
                        "error": self.helper.translation.translate(
                            "error", "start-error", user_lang
                        ).format(self.name, ex)
                    },
                )

    def _get_env_file(self) -> dict:
        try:
            with open(
                Path(self.server_path, "env.json"), "r", encoding="utf-8"
            ) as env_file:
                return json.load(env_file)
        except (OSError, json.JSONDecodeError):
            logger.error("Failed to capture steamCMD env file. Returning empty dict")
            return {}

    def _validate_env_contents(self, value: dict, key: str) -> list:
        items_validated = []
        for item in value["contents"]:
            try:
                p = Helpers.validate_traversal(self.server_path, item)
                p = str(p).replace(":", "\\:")
                items_validated.append(p)
            except ValueError:
                logger.warning(
                    (
                        "Path traversal detected on server "
                        "%s for env %s value %s, skipping"
                    ),
                    self.server_id,
                    key,
                    item,
                )
        return items_validated

    def setup_steam_env(self, my_env):
        env_file_data = self._get_env_file()

        for key, value in env_file_data.items():
            is_path = "path" in key.lower()

            items = (
                self._validate_env_contents(value, key)
                if is_path
                else list(value["contents"])
            )

            existing = my_env.get(key)
            if existing:
                if value["mode"] == "append":
                    items = [existing, *items]
                elif value["mode"] == "prepend":
                    items = [*items, existing]

            separator = ":" if is_path else ","
            my_env[key] = separator.join(items)

        return True

    def do_steam_server_start(self, user_id, user_lang):
        my_env = os.environ
        env_mod = self.setup_steam_env(my_env)
        if env_mod:
            logger.debug(
                "Launching process for server %s with modified environment %s",
                self.server_id,
                my_env,
            )
        else:
            logger.debug(
                "Launching process for server %s with un-modified environment",
                self.server_id,
            )
        try:
            self.process = subprocess.Popen(
                self.server_command,
                cwd=self.server_path,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=my_env,
            )
        except Exception as ex:
            logger.exception(
                f"Server {self.name} failed to start with error code: {ex}"
            )
            if user_id:
                WebSocketManager().broadcast_user(
                    user_id,
                    "send_start_error",
                    {
                        "error": self.helper.translation.translate(
                            "error", "start-error", user_lang
                        ).format(self.name, ex)
                    },
                )

    def after_start(self, user_id, user_lang):
        self.is_crashed = False
        self.stats_helper.server_crash_reset()
        self.record_server_stats()
        check_internet_thread = threading.Thread(
            target=self.check_internet_thread,
            daemon=True,
            args=(
                user_id,
                user_lang,
            ),
            name=f"{self.name}_Internet",
        )
        check_internet_thread.start()
        # Checks if this is the servers first run.
        if not self.helper.check_file_exists(str(Path(self.server_path, ".crafty"))):
            loc_server_port = self.stats_helper.get_server_stats()["server_port"]
            # Sends port reminder message.
            WebSocketManager().broadcast_user(
                user_id,
                "send_error",
                {
                    "error": self.helper.translation.translate(
                        "error", "portReminder", user_lang
                    ).format(self.name, loc_server_port)
                },
            )
            server_users = PermissionsServers.get_server_user_list(self.server_id)
            for user in server_users:
                if user != user_id:
                    WebSocketManager().broadcast_user(user, "send_start_reload", {})
        else:
            WebSocketManager().broadcast_to_server_users(
                self.server_id, "send_start_reload", {}
            )
        # Write last start text file for first run tracking
        self.file_helper.write_text_file(
            Path(self.server_path, ".crafty"),
            f"{self.name} last started {self.start_time}",
        )
        # Register an shedule for polling server stats when running
        logger.info(f"Polling server statistics {self.name} every {5} seconds")
        Console.info(f"Polling server statistics {self.name} every {5} seconds")
        try:
            self.server_scheduler.add_job(
                self.realtime_stats,
                "interval",
                seconds=5,
                id="stats_" + str(self.server_id),
            )
        except ConflictingIdError:
            self.server_scheduler.remove_job("stats_" + str(self.server_id))
            self.server_scheduler.add_job(
                self.realtime_stats,
                "interval",
                seconds=5,
                id="stats_" + str(self.server_id),
            )
        logger.info(f"Saving server statistics {self.name} every {30} seconds")
        Console.info(f"Saving server statistics {self.name} every {30} seconds")
        try:
            self.server_scheduler.add_job(
                self.record_server_stats,
                "interval",
                seconds=30,
                id="save_stats_" + str(self.server_id),
            )
        except ConflictingIdError:
            self.server_scheduler.remove_job("save_stats_" + str(self.server_id))
            self.server_scheduler.add_job(
                self.record_server_stats,
                "interval",
                seconds=30,
                id="save_stats_" + str(self.server_id),
            )
        # Open router ports for this server if it uses UPnP
        NetworkManager.notify_server_change()

    @callback
    def start_server(self, user_id):
        # Clear cached game port so it's recomputed from current config
        self._game_port_cache = None

        if not user_id:
            user_lang = self.helper.get_setting("language")
        else:
            user_lang = HelperUsers.get_user_lang_by_id(user_id)

        if not self.can_server_start(user_id, user_lang):
            return

        logger.info(
            f"Start command detected. Reloading settings from DB for server {self.name}"
        )
        self.setup_server_run_command()
        # fail safe in case we try to start something already running

        logger.info(f"Launching Server {self.name} with command {self.server_command}")
        Console.info(f"Launching Server {self.name} with command {self.server_command}")

        # Checks for eula. Creates one if none detected.
        # If EULA is detected and not set to true we offer to set it true.
        e_flag = False
        if Helpers.check_file_exists(os.path.join(self.settings["path"], EULA_FILE)):
            with open(
                os.path.join(self.settings["path"], EULA_FILE), "r", encoding="utf-8"
            ) as f:
                line = f.readline().lower()
                e_flag = line in [
                    "eula=true",
                    "eula = true",
                    "eula= true",
                    "eula =true",
                ]
        if not e_flag and self.settings["type"] == "minecraft-java":
            if user_id:
                WebSocketManager().broadcast_user(
                    user_id, "send_eula_bootbox", {"id": self.server_id}
                )
            else:
                logger.error(
                    "Autostart failed due to EULA being false. "
                    "Agree not sent due to auto start."
                )
            return False
        if Helpers.is_os_windows():
            logger.info("Windows Detected")
        else:
            logger.info("Unix Detected")

        logger.info(
            f"Starting server in {self.server_path} with command: {self.server_command}"
        )

        match HelperServers.get_server_type_by_id(self.server_id):
            case "minecraft-java" | "hytale":
                self.do_generic_start(user_id, user_lang)
            case "minecraft-bedrock":
                self.do_minecraft_bedrock_start(user_id, user_lang)
            case "steam_cmd":
                self.do_steam_server_start(user_id, user_lang)

        out_buf = ServerOutBuf(self.helper, self.process, self.server_id)

        logger.debug(f"Starting virtual terminal listener for server {self.name}")
        threading.Thread(
            target=out_buf.check, daemon=True, name=f"{self.server_id}_virtual_terminal"
        ).start()

        self.is_crashed = False
        self.stats_helper.server_crash_reset()

        self.start_time = str(
            datetime.datetime.now(tz=ZoneInfo("Etc/UTC")).strftime("%Y-%m-%d %H:%M:%S")
        )

        if self.process.poll() is None:
            logger.info(f"Server {self.name} running with PID {self.process.pid}")
            Console.info(f"Server {self.name} running with PID {self.process.pid}")
            self.after_start(user_id, user_lang)
        else:
            logger.warning(
                f"Server PID {self.process.pid} died right after starting "
                f"- is this a server config issue?"
            )
            Console.critical(
                f"Server PID {self.process.pid} died right after starting "
                f"- is this a server config issue?"
            )

        if self.settings["crash_detection"]:
            logger.info(
                f"Server {self.name} has crash detection enabled "
                f"- starting watcher task"
            )
            Console.info(
                f"Server {self.name} has crash detection enabled "
                f"- starting watcher task"
            )

            self.server_scheduler.add_job(
                self.detect_crash, "interval", seconds=30, id=f"c_{self.server_id}"
            )

    def check_internet_thread(self, user_id, user_lang):
        if user_id and not Helpers.check_internet():
            WebSocketManager().broadcast_user(
                user_id,
                "send_error",
                {
                    "error": self.helper.translation.translate(
                        "error", "internet", user_lang
                    )
                },
            )

    def stop_crash_detection(self):
        # This is only used if the crash detection settings change
        # while the server is running.
        if self.check_running():
            logger.info(f"Detected crash detection shut off for server {self.name}")
            try:
                self.server_scheduler.remove_job("c_" + str(self.server_id))
            except JobLookupError:
                logger.error(
                    f"Removing crash watcher for server {self.name} failed. "
                    f"Assuming it was never started."
                )

    def start_crash_detection(self):
        # This is only used if the crash detection settings change
        # while the server is running.
        if self.check_running():
            logger.info(
                f"Server {self.name} has crash detection enabled "
                f"- starting watcher task"
            )
            Console.info(
                f"Server {self.name} has crash detection enabled "
                "- starting watcher task"
            )
            try:
                self.server_scheduler.add_job(
                    self.detect_crash, "interval", seconds=30, id=f"c_{self.server_id}"
                )
            except ConflictingIdError:
                logger.info(f"Job with id c_{self.server_id} already running...")

    def stop_threaded_server(self):
        self.stop_server()

        if self.server_thread:
            self.server_thread.join()

    @callback
    def stop_server(self):
        running = self.check_running()
        if not running:
            logger.info(f"Can't stop server {self.name} if it's not running")
            Console.info(f"Can't stop server {self.name} if it's not running")
            return
        if self.settings["crash_detection"]:
            # remove crash detection watcher
            logger.info(f"Removing crash watcher for server {self.name}")
            try:
                self.server_scheduler.remove_job("c_" + str(self.server_id))
            except JobLookupError:
                logger.error(
                    f"Removing crash watcher for server {self.name} failed. "
                    f"Assuming it was never started."
                )
        if self.settings["stop_command"]:
            logger.info(f"Stop command requested for {self.settings['server_name']}.")
            self.send_command(self.settings["stop_command"])
            self.write_player_cache()
        else:
            # windows will need to be handled separately for Ctrl+C
            self.process.terminate()
        i = 0

        # caching the name and pid number
        server_name = self.name
        server_pid = self.process.pid
        self.shutdown_timeout = self.settings["shutdown_timeout"]

        while running:
            i += 1
            ttk = int(self.shutdown_timeout - (i * 2))
            if i <= self.shutdown_timeout / 2:
                logstr = (
                    f"Server {server_name} is still running "
                    "- waiting 2s to see if it stops"
                    f"({ttk} "
                    f"seconds until force close)"
                )
                logger.info(logstr)
                Console.info(logstr)
            running = self.check_running()
            time.sleep(2)

            # if we haven't closed in 60 seconds, let's just slam down on the PID
            if i >= round(self.shutdown_timeout / 2, 0):
                logger.info(
                    f"Server {server_name} is still running - Forcing the process down"
                )
                Console.info(
                    f"Server {server_name} is still running - Forcing the process down"
                )
                self.kill()

        logger.info(f"Stopped Server {server_name} with PID {server_pid}")
        Console.info(f"Stopped Server {server_name} with PID {server_pid}")

        # massive resetting of variables
        self.cleanup_server_object()

        try:
            # remove the stats polling job since server is stopped
            logger.info("Cleaning up stats schedules for server %s", self.server_id)
            self.server_scheduler.remove_job("stats_" + str(self.server_id))
            self.server_scheduler.remove_job("save_stats_" + str(self.server_id))
        except JobLookupError as e:
            logger.exception(
                f"Could not remove job with id stats_{self.server_id} due"
                + f" to error: {e}"
            )
        self.record_server_stats()
        NetworkManager.notify_server_change()

        WebSocketManager().broadcast_to_server_users(
            self.server_id, "send_start_reload", {}
        )

    def restart_threaded_server(self, user_id):
        if self.is_backingup:
            logger.info(
                "Restart command detected. Supressing - server has"
                " backup shutdown enabled and server is currently backing up."
            )
            return
        # if not already running, let's just start
        if not self.check_running():
            self.run_threaded_server(user_id)
        else:
            logger.info(
                f"Restart command detected. Sending stop command to {self.server_id}."
            )
            self.stop_threaded_server()
            time.sleep(2)
            self.run_threaded_server(user_id)

    def cleanup_server_object(self):
        self.start_time = None
        self.restart_count = 0
        self.is_crashed = False
        self.updating = False
        self.process = None

    def check_running(self):
        # if process is None, we never tried to start
        if self.process is None:
            return False
        poll = self.process.poll()
        if poll is None:
            return True
        self.last_rc = poll
        return False

    @callback
    def send_command(self, command):
        if not self.check_running() and command.lower() != "start":
            logger.warning(f'Server not running, unable to send command "{command}"')
            return False
        Console.info(f"COMMAND TIME: {command}")
        logger.debug(f"Sending command {command} to server")

        # send it
        self.process.stdin.write(f"{command}\n".encode("utf-8"))
        self.process.stdin.flush()
        return True

    @callback
    def crash_detected(self, name):
        # clear the old scheduled watcher task
        self.server_scheduler.remove_job(f"c_{self.server_id}")
        # remove the stats polling job since server is stopped
        self.server_scheduler.remove_job("stats_" + str(self.server_id))
        self.server_scheduler.remove_job("save_stats_" + str(self.server_id))

        # the server crashed, or isn't found - so let's reset things.
        logger.warning(
            f"The server {name} seems to have vanished unexpectedly, did it crash?"
        )

        if self.settings["crash_detection"]:
            logger.warning(
                f"The server {name} has crashed and will be restarted. "
                f"Restarting server"
            )
            Console.critical(
                f"The server {name} has crashed and will be restarted. "
                f"Restarting server"
            )

            self.run_threaded_server(None)
            return True
        logger.critical(
            f"The server {name} has crashed, "
            f"crash detection is disabled and it will not be restarted"
        )
        Console.critical(
            f"The server {name} has crashed, "
            f"crash detection is disabled and it will not be restarted"
        )
        return False

    @callback
    def kill(self):
        logger.info(f"Terminating server {self.server_id} and all child processes")
        try:
            process = psutil.Process(self.process.pid)
        except NoSuchProcess:
            logger.info(f"Cannot kill {self.process.pid} as we cannot find that pid.")
            return
        # for every sub process...
        for proc in process.children(recursive=True):
            # kill all the child processes
            logger.info(f"Sending SIGKILL to server {proc.name}")
            proc.kill()
        # kill the main process we are after
        logger.info("Sending SIGKILL to parent")
        try:
            self.server_scheduler.remove_job("stats_" + str(self.server_id))
        except JobLookupError as e:
            logger.exception(
                f"Could not remove job with id stats_{self.server_id} due"
                + f" to error: {e}"
            )
        self.process.kill()

    def get_start_time(self):
        return self.start_time if self.check_running() else False

    def get_pid(self):
        return self.process.pid if self.process is not None else None

    def detect_crash(self):
        logger.info(f"Detecting possible crash for server: {self.name} ")

        running = self.check_running()

        # if all is okay, we set the restart count to 0 and just exit out
        if running:
            Console.debug("Successfully found process. Resetting crash counter to 0")
            self.restart_count = 0
            return
        # check the exit code -- This could be a fix for /stop
        if str(self.process.returncode) in self.settings["ignored_exits"].split(","):
            logger.warning(
                f"Process {self.process.pid} exited with code "
                f"{self.process.returncode}. This is considered a clean exit"
                f" supressing crash handling."
            )
            # cancel the watcher task
            self.server_scheduler.remove_job("c_" + str(self.server_id))
            self.server_scheduler.remove_job("stats_" + str(self.server_id))
            return

        self.stats_helper.sever_crashed()
        # if we haven't tried to restart more 3 or more times
        if self.restart_count <= 3:
            # start the server if needed
            server_restarted = self.crash_detected(self.name)

            if server_restarted:
                # add to the restart count
                self.restart_count = self.restart_count + 1

        # we have tried to restart 4 times...
        elif self.restart_count == 4:
            logger.critical(
                f"Server {self.name} has been restarted {self.restart_count}"
                f" times. It has crashed, not restarting."
            )
            Console.critical(
                f"Server {self.name} has been restarted {self.restart_count}"
                f" times. It has crashed, not restarting."
            )

            self.restart_count = 0
            self.is_crashed = True
            self.stats_helper.sever_crashed()

            # cancel the watcher task
            self.server_scheduler.remove_job("c_" + str(self.server_id))

    def remove_watcher_thread(self):
        logger.info("Removing old crash detection watcher thread")
        Console.info("Removing old crash detection watcher thread")
        self.server_scheduler.remove_job("c_" + str(self.server_id))

    def agree_eula(self, user_id):
        eula_file = os.path.join(self.server_path, EULA_FILE)
        with open(eula_file, "w", encoding="utf-8") as f:
            f.write("eula=true")
        self.run_threaded_server(user_id)

    def server_restore_threader(self, backup_id, backup_file, in_place=False):
        # import the server again based on zipfile
        backup_config = HelpersManagement.get_backup_config(backup_id)

        # This path gets resolved and checked for traversal before restore_starter
        # so that it remains async.
        # At this point this path cannot be trusted.
        backup_type = backup_config.get("backup_type", "zip_vault")
        if backup_type == "zip_vault":
            expected_backup_location = Path(
                backup_config["backup_location"], backup_config["backup_id"]
            )
        else:
            expected_backup_location = Path(
                backup_config["backup_location"], "snapshot_backups", "manifests"
            )

        expected_backup_location = expected_backup_location.resolve()

        try:
            Helpers.validate_traversal(expected_backup_location, backup_file)
        except ValueError as why:
            # Crash out on possible traversal.
            logger.exception(
                f"Possible backup traversal detected on restore request: {why}",
            )

            server_users = PermissionsServers.get_server_user_list(self.server_id)
            for user in server_users:
                WebSocketManager().broadcast_user(
                    user,
                    "send_error",
                    self.helper.translation.translate(
                        "notify", "restoreFailed", HelperUsers.get_user_lang_by_id(user)
                    ),
                )
            return

        backup_location = (expected_backup_location / backup_file).resolve()

        restore_thread = threading.Thread(
            target=self.backup_mgr.restore_starter,
            daemon=True,
            name=f"backup_{backup_config['backup_id']}",
            args=[backup_config, backup_location, self, in_place],
        )

        restore_thread.start()

    def server_backup_threader(self, backup_id=None):
        backup_config = self.get_backup_config(backup_id)
        # Check to see if we're already backing up
        if self.check_backup_by_id(backup_config["backup_id"]):
            return False

        if backup_config["before"]:
            logger.debug(
                "Found running server and send command option. Sending command"
            )
            self.send_command(backup_config["before"])
            # Pause to let command run
            time.sleep(5)

        backup_thread = threading.Thread(
            target=self.backup_server,
            daemon=True,
            name=f"backup_{backup_config['backup_id']}",
            args=[backup_config["backup_id"]],
        )
        logger.info(
            f"Starting Backup Thread for server {self.settings['server_name']}."
        )
        if self.server_path is None:
            self.server_path = Helpers.get_os_understandable_path(self.settings["path"])
            logger.info(
                "Backup Thread - Local server path not defined. "
                "Setting local server path variable."
            )

        try:
            backup_thread.start()
        except Exception as ex:
            logger.exception(f"Failed to start backup: {ex}")
            return False
        logger.info(f"Backup Thread started for server {self.settings['server_name']}.")

    @callback
    def backup_server(self, backup_id) -> dict:
        logger.info(f"Starting server {self.name} (ID {self.server_id}) backup")

        conf = HelpersManagement.get_backup_config(backup_id)
        self.was_running = False
        if conf["shutdown"]:
            logger.info(
                "Found shutdown preference. Delaying"
                + "backup start. Shutting down server."
            )
            if self.check_running():
                self.stop_server()
                self.was_running = True
        # Adjust the location to include the backup ID for destination.
        backup_location = os.path.join(conf["backup_location"], conf["backup_id"])

        # Check if the backup location even exists.
        if not backup_location:
            Console.critical("No backup path found. Canceling")
            backup_status = json.loads(
                HelpersManagement.get_backup_config(backup_id)["status"]
            )
            if backup_status["status"] == "Failed":
                last_backup_status = "❌"
                reason = backup_status["message"]
                return {
                    "backup_status": last_backup_status,
                    "backup_error": reason,
                }
        if conf["before"]:
            logger.debug(
                "Found running server and send command option. Sending command"
            )
            self.send_command(conf["before"])
            # Pause to let command run
            time.sleep(5)
        backup_name, backup_size = self.backup_mgr.backup_starter(conf, self)
        if conf["after"]:
            self.send_command(conf["after"])
        if conf["shutdown"] and self.was_running:
            logger.info(
                "Backup complete. User had shutdown preference. Starting server."
            )
            self.run_threaded_server(HelperUsers.get_user_id_by_name("system"))
        self.set_backup_status()

        # Return data for webhooks callback
        base_url = f"{self.helper.get_setting('base_url')}"
        size = backup_size
        backup_status = json.loads(
            HelpersManagement.get_backup_config(backup_id)["status"]
        )
        reason = backup_status["message"]
        if not backup_name:
            return {
                "backup_status": "failed",
                "backup_error": reason,
            }
        if backup_size:
            size = self.helper.human_readable_file_size(backup_size)
        url = (
            f"https://{base_url}/api/v2/servers/{self.server_id}"
            f"/backups/backup/{backup_id}/download/{html.escape(backup_name)}"
        )
        if conf["backup_type"] == "snapshot":
            size = 0
            url = (
                f"https://{base_url}/panel/edit_backup?"
                f"id={self.server_id}&backup_id={backup_id}"
            )
        backup_status = json.loads(
            HelpersManagement.get_backup_config(backup_id)["status"]
        )
        last_backup_status = "ok"
        reason = ""
        if backup_status["status"] == "Failed":
            last_backup_status = "failed"
            reason = backup_status["message"]
        return {
            "backup_name": backup_name,
            "backup_size": size,
            "backup_link": url,
            "backup_status": last_backup_status,
            "backup_error": reason,
        }

    def set_backup_status(self):
        backups = HelpersManagement.get_backups_by_server(self.server_id, True)
        alert = False
        for backup in backups:
            if json.loads(backup.status)["status"] == "Failed":
                alert = True
        self.last_backup_failed = alert

    def last_backup_status(self):
        return self.last_backup_failed

    @callback
    def server_upgrade(self):
        self.stats_helper.set_update(True)
        update_thread = threading.Thread(
            target=self.threaded_jar_update, daemon=True, name=f"exe_update_{self.name}"
        )
        update_thread.start()

    def write_player_cache(self):
        write_json = {}
        for item in self.player_cache:
            write_json[item["name"]] = item
        with open(
            os.path.join(
                self.helper.root_dir,
                "app",
                "config",
                "db",
                "servers",
                self.server_id,
                "players_cache.json",
            ),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(write_json, indent=4))
            logger.info("Cache file refreshed")

    def get_formatted_server_players(self) -> list:
        server_players = self.get_server_players()
        if len(server_players) == 0:
            return []
        if isinstance(server_players[0], dict):
            sp = server_players.copy()
            server_players = []
            for player in sp:
                server_players.append(player["Name"])
        return server_players

    def cache_players(self):
        if not self.check_running():
            return
        server_players = self.get_formatted_server_players()
        for p in self.player_cache[:]:
            if p["status"] == "Online" and p["name"] not in server_players:
                p["status"] = "Offline"
                p["last_seen"] = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
            elif p["name"] in server_players:
                self.player_cache.remove(p)
        for player in server_players:
            if player == "Anonymous Player":
                # Skip Anonymous Player
                continue
            if player in self.player_cache:
                self.player_cache.remove(player)
            self.player_cache.append(
                {
                    "name": player,
                    "status": "Online",
                    "last_seen": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                }
            )

    def check_update(self):
        return self.stats_helper.get_server_stats()["updating"]

    def _pre_update_checks(self, was_started: bool):
        server_users = PermissionsServers.get_server_user_list(self.server_id)
        # check to make sure a backup config actually exists before starting the update
        if len(self.management_helper.get_backups_by_server(self.server_id, True)) <= 0:
            WebSocketManager().broadcast_to_server_users(
                self.server_id,
                "notification",
                "Backup config does not exist for " + self.name + ". canceling update.",
            )
            logger.error(f"Back config does not exist for {self.name}. Update Failed.")
            self.stats_helper.set_update(False)
            return False
        # Get default backup configuration
        backup_config = HelpersManagement.get_default_server_backup(self.server_id)
        ws_params = {
            "isUpdating": self.check_update(),
            "server_id": self.server_id,
            "wasRunning": was_started,
        }
        if len(WebSocketManager().clients) > 0:
            # There are clients
            self.check_update()
            message = (
                '<a data-id="' + str(self.server_id) + '" class=""> UPDATING...</i></a>'
            )
            ws_params["string"] = message
        for user in server_users:
            WebSocketManager().broadcast_user_page(
                SERVER_DETAIL_URL, user, "update_button_status", ws_params
            )
        # start backup
        backup_result = self.backup_server(backup_config["backup_id"])
        if backup_result["backup_status"] == "failed":
            WebSocketManager().broadcast_to_server_users(
                self.server_id,
                "notification",
                f"Backup failed for {self.name}. Canceling update.",
            )
            self.stats_helper.set_update(False)
            return False
        return True

    def _after_update(self, downloaded: bool, was_started: bool):
        server_users = PermissionsServers.get_server_user_list(self.server_id)
        if downloaded:
            logger.info("Executable updated successfully. Starting Server")

            self.stats_helper.set_update(False)
            if len(WebSocketManager().clients) > 0:
                # There are clients
                self.check_update()
                WebSocketManager().broadcast_to_server_users(
                    self.server_id,
                    "notification",
                    f"Executable update finished for {self.name}",
                )
                # sleep so first notif can completely run
                time.sleep(3)
            for user in server_users:
                WebSocketManager().broadcast_user_page(
                    SERVER_DETAIL_URL,
                    user,
                    "update_button_status",
                    {
                        "isUpdating": self.check_update(),
                        "server_id": self.server_id,
                        "wasRunning": was_started,
                    },
                )
                WebSocketManager().broadcast_user_page(
                    user, "/panel/dashboard", "send_start_reload", {}
                )
            self.management_helper.add_to_audit_log_raw(
                "Alert",
                "-1",
                self.server_id,
                f"Executable update finished for {self.name}",
                self.settings["server_ip"],
            )
            if was_started:
                self.run_threaded_server(HelperUsers.get_user_id_by_name("system"))
        else:
            WebSocketManager().broadcast_to_server_users(
                self.server_id,
                "notification",
                (
                    f"Executable update failed for {self.name}"
                    ". Check log file for details."
                ),
            )
            logger.error("Executable download failed.")
            self.stats_helper.set_update(False)
        self.update_manager.check_server_version(
            self.settings
        )  # Check to make sure the update was
        # successful and that we match remote
        WebSocketManager().broadcast_to_server_users(
            self.server_id,
            "remove_spinner",
            {"server_id": self.server_id},
        )

    def threaded_jar_update(self):
        downloaded = False
        was_started = False
        # checks if server is running. Calls shutdown if it is running.
        if self.check_running():
            was_started = True
            logger.info(
                f"Server with PID {self.process.pid} is running. "
                f"Sending shutdown command"
            )
            self.stop_threaded_server()
        pre_success = self._pre_update_checks(was_started)
        if not pre_success:
            return
        current_executable = Path(
            Helpers.get_os_understandable_path(self.settings["path"]),
            self.settings["executable"],
        )
        server_type = HelperServers.get_server_type_by_id(self.server_id)
        # lets download the files
        match server_type:
            case "minecraft-java":
                downloaded = self.update_manager.update_mc_java(
                    current_executable, self.settings["executable_update_url"]
                )
            case "hytale":
                downloaded = self.update_manager.update_hytale(
                    self.settings["path"], self.server_id
                )
            case "steam_cmd":
                downloaded = self.update_manager.update_steam_cmd(self.settings["path"])
            case "minecraft-bedrock":  # Bedrock if nothing else
                downloaded = self.update_manager.update_mc_bedrock(
                    self.settings["path"], self.server_id
                )
        self._after_update(downloaded, was_started)

    def start_dir_calc_task(self):
        server_dt = HelperServers.get_server_data_by_id(self.server_id)
        self.server_size = Helpers.human_readable_file_size(
            self.file_helper.get_dir_size(server_dt["path"])
        )
        self.dir_scheduler.add_job(
            self.calc_dir_size,
            "interval",
            minutes=self.helper.get_setting("dir_size_poll_freq_minutes"),
            id=str(self.server_id) + "_dir_poll",
        )
        self.dir_scheduler.add_job(
            self.cache_players,
            "interval",
            seconds=5,
            id=str(self.server_id) + "_players_poll",
        )

    def calc_dir_size(self):
        server_dt = HelperServers.get_server_data_by_id(self.server_id)
        self.server_size = Helpers.human_readable_file_size(
            self.file_helper.get_dir_size(server_dt["path"])
        )

    # **********************************************************************************
    #                               Minecraft Servers Statistics
    # **********************************************************************************

    def realtime_stats(self):
        # only get stats if clients are connected.
        # no point in burning cpu
        if len(WebSocketManager().clients) > 0:
            servers_ping = []
            raw_ping_result = []
            raw_ping_result = self.get_raw_server_stats(self.server_id)

            if f"{raw_ping_result.get('icon')}" == "b''":
                raw_ping_result["icon"] = False

            servers_ping.append(
                {
                    "id": raw_ping_result.get("id"),
                    "started": raw_ping_result.get("started"),
                    "running": raw_ping_result.get("running"),
                    "cpu": raw_ping_result.get("cpu"),
                    "mem": raw_ping_result.get("mem"),
                    "mem_percent": raw_ping_result.get("mem_percent"),
                    "world_name": raw_ping_result.get("world_name"),
                    "world_size": raw_ping_result.get("world_size"),
                    "server_port": raw_ping_result.get("server_port"),
                    "game_port": raw_ping_result.get("game_port"),
                    "int_ping_results": raw_ping_result.get("int_ping_results"),
                    "online": raw_ping_result.get("online"),
                    "max": raw_ping_result.get("max"),
                    "players": raw_ping_result.get("players"),
                    "desc": raw_ping_result.get("desc"),
                    "version": raw_ping_result.get("version"),
                    "icon": raw_ping_result.get("icon"),
                    "crashed": self.is_crashed,
                    "count_players": self.server_object.count_players,
                }
            )

            WebSocketManager().broadcast_page_params(
                SERVER_DETAIL_URL,
                {"id": str(self.server_id)},
                "update_server_details",
                {
                    "id": raw_ping_result.get("id"),
                    "started": raw_ping_result.get("started"),
                    "running": raw_ping_result.get("running"),
                    "cpu": raw_ping_result.get("cpu"),
                    "mem": raw_ping_result.get("mem"),
                    "mem_raw": raw_ping_result.get("mem_raw"),
                    "mem_percent": raw_ping_result.get("mem_percent"),
                    "world_name": raw_ping_result.get("world_name"),
                    "world_size": raw_ping_result.get("world_size"),
                    "server_port": raw_ping_result.get("server_port"),
                    "int_ping_results": raw_ping_result.get("int_ping_results"),
                    "online": raw_ping_result.get("online"),
                    "max": raw_ping_result.get("max"),
                    "players": raw_ping_result.get("players"),
                    "desc": raw_ping_result.get("desc"),
                    "version": raw_ping_result.get("version"),
                    "icon": raw_ping_result.get("icon"),
                    "crashed": self.is_crashed,
                    "created": datetime.datetime.now().strftime("%Y/%m/%d, %H:%M:%S"),
                    "players_cache": self.player_cache,
                },
            )

            # self.record_server_stats()

            if len(servers_ping) > 0:
                try:
                    WebSocketManager().broadcast_page(
                        "/panel/dashboard", "update_server_status", servers_ping
                    )
                except RuntimeError:
                    Console.critical("Can't broadcast server status to websocket")

    def check_backup_by_id(self, backup_id: str) -> bool:
        # Check to see if we're already backing up
        for thread in threading.enumerate():
            if thread.getName() == f"backup_{backup_id}":
                Console.debug(f"Backup with id {backup_id} already running!")
                return True
        return False

    def _get_hytale_port(self) -> int:
        # Try to parse --bind 0.0.0.0:<port> from the execution command
        if self.settings["execution_command"]:
            bind_match = re.search(
                r"--bind\s+[\d.]+:(\d+)", self.settings["execution_command"]
            )
            if bind_match:
                game_port = int(bind_match.group(1))
            else:
                # Fallback: Hytale query port is game port + 3
                game_port = self.settings["server_port"] - 3
        else:
            game_port = self.settings["server_port"] - 3
        return game_port

    def _get_mc_java_port(self):
        game_port = self.settings["server_port"]
        # Try to read server-port from server.properties
        properties_path = os.path.join(self.settings["path"], "server.properties")
        try:
            with open(properties_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("server-port="):
                        game_port = int(line.split("=", 1)[1].strip())
                        break
        except FileNotFoundError:
            logger.warning(
                "server.properties not found at %s for server %s"
                " — unable to parse game port",
                properties_path,
                self.server_id,
            )
        except (ValueError, OSError) as e:
            logger.warning(
                "Failed to parse game port from %s for server %s: %s",
                properties_path,
                self.server_id,
                e,
            )
        return game_port

    def _get_game_port(self):
        """Derive the game port from server config, cached per server lifecycle.

        The monitoring/query port stored in the DB may differ from the port
        players actually connect to. The result is cached and cleared on
        server start/stop.
        """
        if self._game_port_cache is not None:
            return self._game_port_cache

        game_port = self.settings["server_port"]

        match self.settings["type"]:
            case "hytale":
                game_port = self._get_hytale_port()

            case "minecraft-java":
                game_port = self._get_mc_java_port()

        self._game_port_cache = game_port
        return game_port

    def get_backup_config(self, backup_id) -> dict:
        if not backup_id:
            return HelpersManagement.get_default_server_backup(self.server_id)
        return HelpersManagement.get_backup_config(backup_id)

    def get_servers_stats(self):
        server_type = HelperServers.get_server_type_by_id(self.server_id)
        server_stats = {}

        server_id = self.server_id
        logger.debug("Getting Stats for Server %s | %s...", self.name, server_id)
        server = HelperServers.get_server_data_by_id(server_id)

        # get our server object, settings and data dictionaries
        self.reload_server_settings()

        # process stats
        p_stats = Stats._try_get_process_stats(self.process, self.check_running())
        internal_ip = server["server_ip"]
        server_port = server["server_port"]
        server_name = server.get("server_name", f"ID#{server_id}")
        game_port = self._get_game_port()

        logger.debug(f"Pinging server '{server}' on {internal_ip}:{server_port}")
        if server_type in ("minecraft-bedrock", "raknet"):
            int_mc_ping = ping_raknet(internal_ip, int(server_port))
        elif server_type == "hytale":
            int_mc_ping = NitradoPing.ping(internal_ip, server_port)
        else:
            try:
                int_mc_ping = ping(internal_ip, int(server_port))
            except OSError:
                int_mc_ping = False

        int_data = False
        ping_data = {}

        # if we got a good ping return, let's parse it
        if int_mc_ping:
            int_data = True
            if server_type == "minecraft-bedrock":
                ping_data = Stats.parse_server_raknet_ping(int_mc_ping)
            elif server_type == "hytale":
                ping_data = NitradoPing.parse_ping_response(int_mc_ping)
            else:
                ping_data = Stats.parse_server_ping(int_mc_ping)
        # Makes sure we only show stats when a server is online
        # otherwise people have gotten confused.
        if self.check_running():
            server_stats = {
                "id": server_id,
                "started": self.get_start_time(),
                "running": self.check_running(),
                "cpu": p_stats.get("cpu_usage", 0),
                "mem": p_stats.get("memory_usage", 0),
                "mem_raw": p_stats.get("memory_usage_raw", 0),
                "mem_percent": p_stats.get("mem_percentage", 0),
                "world_name": server_name,
                "world_size": self.server_size,
                "server_port": server_port,
                "game_port": game_port,
                "int_ping_results": int_data,
                "online": ping_data.get("online", False),
                "max": ping_data.get("max", False),
                "players": ping_data.get("players", False),
                "desc": ping_data.get("server_description", False),
                "version": ping_data.get("server_version", False),
                "icon": ping_data.get("server_icon"),
            }
        else:
            server_stats = {
                "id": server_id,
                "started": self.get_start_time(),
                "running": self.check_running(),
                "cpu": p_stats.get("cpu_usage", 0),
                "mem": p_stats.get("memory_usage", 0),
                "mem_raw": p_stats.get("memory_usage_raw", 0),
                "mem_percent": p_stats.get("mem_percentage", 0),
                "world_name": server_name,
                "world_size": self.server_size,
                "server_port": server_port,
                "game_port": game_port,
                "int_ping_results": int_data,
                "online": False,
                "max": False,
                "players": False,
                "desc": False,
                "version": False,
                "icon": None,
            }

        return server_stats

    def get_server_players(self):
        server = HelperServers.get_server_data_by_id(self.server_id)
        server_type = HelperServers.get_server_type_by_id(self.server_id)
        logger.debug(f"Getting players for server {server['server_name']}")

        internal_ip = server["server_ip"]
        server_port = server["server_port"]

        logger.debug(f"Pinging {internal_ip} on port {server_port}")
        if server_type == "minecraft-java":
            int_mc_ping = ping(internal_ip, int(server_port))

            ping_data = {}

            # if we got a good ping return, let's parse it
            if int_mc_ping:
                ping_data = Stats.parse_server_ping(int_mc_ping)
                return ping_data["players"]
        elif server_type == "hytale":
            return NitradoPing.parse_ping_response(
                NitradoPing.ping(internal_ip, server_port)
            ).get("players", [])

        return []

    def get_raw_server_stats(self, server_id):
        server_type = HelperServers.get_server_type_by_id(server_id)
        int_data = False
        ping_data = {}

        try:
            server = HelperServers.get_server_obj(server_id)
        except peewee.DoesNotExist:
            return {
                "id": server_id,
                "started": False,
                "running": False,
                "cpu": 0,
                "mem": 0,
                "mem_percent": 0,
                "world_name": None,
                "world_size": None,
                "server_port": None,
                "game_port": None,
                "int_ping_results": False,
                "online": False,
                "max": False,
                "players": False,
                "desc": False,
                "version": False,
                "icon": False,
            }

        server_stats = {}
        if not server:
            return {}
        server_dt = HelperServers.get_server_data_by_id(server_id)

        logger.debug(f"Getting stats for server: {server_id}")

        # get our server object, settings and data dictionaries
        self.reload_server_settings()

        # world data
        server_name = server_dt["server_name"]

        # process stats
        p_stats = Stats._try_get_process_stats(self.process, self.check_running())

        internal_ip = server_dt["server_ip"]
        server_port = server_dt["server_port"]
        game_port = self._get_game_port()

        logger.debug(f"Pinging server '{self.name}' on {internal_ip}:{server_port}")
        if HelperServers.get_server_type_by_id(server_id) in (
            "minecraft-bedrock",
            "raknet",
        ):
            int_mc_ping = ping_raknet(internal_ip, int(server_port))
            if int_mc_ping:
                ping_data = Stats.parse_server_raknet_ping(int_mc_ping)
                int_data = True
        elif server_type == "hytale":
            int_mc_ping = NitradoPing.ping(internal_ip, server_port)
            if int_mc_ping:
                int_data = True
            ping_data = NitradoPing.parse_ping_response(int_mc_ping)
        else:
            int_mc_ping = ping(internal_ip, int(server_port))
            if int_mc_ping:
                ping_data = Stats.parse_server_ping(int_mc_ping)
                int_data = True
        # Makes sure we only show stats when a server is online
        # otherwise people have gotten confused.
        if self.check_running():
            server_stats = {
                "id": server_id,
                "started": self.get_start_time(),
                "running": self.check_running(),
                "cpu": p_stats.get("cpu_usage", 0),
                "mem": p_stats.get("memory_usage", 0),
                "mem_raw": p_stats.get("memory_usage_raw", 0),
                "mem_percent": p_stats.get("mem_percentage", 0),
                "world_name": server_name,
                "world_size": self.server_size,
                "server_port": server_port,
                "game_port": game_port,
                "int_ping_results": int_data,
                "online": ping_data.get("online", False),
                "max": ping_data.get("max", False),
                "players": ping_data.get("players", False),
                "desc": ping_data.get("server_description", False),
                "version": ping_data.get("server_version", False),
                "icon": ping_data.get("server_icon", False),
            }
        else:
            server_stats = {
                "id": server_id,
                "started": self.get_start_time(),
                "running": self.check_running(),
                "cpu": p_stats.get("cpu_usage", 0),
                "mem": p_stats.get("memory_usage", 0),
                "mem_raw": p_stats.get("memory_usage_raw", 0),
                "mem_percent": p_stats.get("mem_percentage", 0),
                "world_name": server_name,
                "world_size": self.server_size,
                "server_port": server_port,
                "game_port": game_port,
                "int_ping_results": int_data,
                "online": False,
                "max": False,
                "players": False,
                "desc": False,
                "version": False,
            }

        return server_stats

    def record_server_stats(self):
        server_stats = self.get_servers_stats()
        self.stats_helper.insert_server_stats(server_stats)

        self.cpu_usage.labels(f"{self.server_id}").set(server_stats.get("cpu"))
        self.mem_usage_percent.labels(f"{self.server_id}").set(
            server_stats.get("mem_percent")
        )
        self.minecraft_version.labels(f"{self.server_id}").info(
            {"version": f"{server_stats.get('version')}"}
        )
        self.online_players.labels(f"{self.server_id}").set(server_stats.get("online"))

        # delete old data
        max_age = self.helper.get_setting("history_max_age")
        now = datetime.datetime.now()
        minimum_to_exist = now - datetime.timedelta(days=max_age)

        self.stats_helper.remove_old_stats(minimum_to_exist)

    def init_registries(self):
        # REGISTRY Entries for Server Stats functions
        self.cpu_usage = Gauge(
            name="CPU_Usage",
            documentation="The CPU usage of the server",
            labelnames=["server_id"],
            registry=self.server_registry,
        )
        self.mem_usage_percent = Gauge(
            name="Mem_Usage",
            documentation="The Memory usage of the server",
            labelnames=["server_id"],
            registry=self.server_registry,
        )
        self.minecraft_version = Info(
            name="Minecraft_Version",
            documentation="The version of the minecraft of this server",
            labelnames=["server_id"],
            registry=self.server_registry,
        )

        self.online_players = Gauge(
            name="online_players",
            documentation="The number of players online for a server",
            labelnames=["server_id"],
            registry=self.server_registry,
        )

    def get_server_history(self):
        history = self.stats_helper.get_history_stats(self.server_id, 1)
        return history
