# pylint: disable=too-many-lines
import time
import datetime
import mimetypes
import os
import typing as t
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError
import httpx
import aiofiles
import nh3
import tornado.web

# TZLocal is set as a hidden import on win pipeline
from tzlocal import get_localzone

from app.classes.models.servers import Servers
from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.models.management import HelpersManagement
from app.classes.controllers.roles_controller import RolesController
from app.classes.helpers.helpers import Helpers, MASTER_CONFIG
from app.classes.shared.main_models import DatabaseShortcuts
from app.classes.shared.metrics_time_helper import MetricsTimeRangeHelper
from app.classes.shared.stats_helpers import StatsConverter
from app.classes.web.base_handler import BaseHandler
from app.classes.web.webhooks.webhook_factory import WebhookFactory

logger = logging.getLogger(__name__)
# You must put any new subpages in here
SUBPAGE_PERMS = {
    "term": EnumPermissionsServer.TERMINAL,
    "logs": EnumPermissionsServer.LOGS,
    "schedules": EnumPermissionsServer.SCHEDULE,
    "backup": EnumPermissionsServer.BACKUP,
    "files": EnumPermissionsServer.FILES,
    "config": EnumPermissionsServer.CONFIG,
    "admin_controls": EnumPermissionsServer.PLAYERS,
    "metrics": EnumPermissionsServer.LOGS,
    "webhooks": EnumPermissionsServer.CONFIG,
    "update_center": EnumPermissionsServer.CONFIG,
}

SCHEDULE_AUTH_ERROR_URL = "/panel/error?error=Unauthorized access To Schedules"

HUMANIZED_INDEX_FILE = "humanized_index.json"


class PanelHandler(BaseHandler):
    def get_user_roles(self) -> t.Dict[str, list]:
        user_roles = {}
        for user_id in self.controller.users.get_all_user_ids():
            user_roles_list = self.controller.users.get_user_roles_names(user_id)
            user_roles[user_id] = user_roles_list
        return user_roles

    def get_role_servers(self) -> t.List[RolesController.RoleServerJsonType]:
        servers = []
        for server in self.controller.servers.get_all_defined_servers():
            argument = self.get_argument(f"server_{server['server_id']}_access", "0")
            if argument == "0":
                continue

            permission_mask = "0" * len(EnumPermissionsServer)
            for permission in self.controller.server_perms.list_defined_permissions():
                argument = self.get_argument(
                    f"permission_{server['server_id']}_{permission.name}", "0"
                )
                if argument == "1":
                    permission_mask = self.controller.server_perms.set_permission(
                        permission_mask, permission, "1"
                    )

            servers.append(
                {"server_id": server["server_id"], "permissions": permission_mask}
            )
        return servers

    def get_perms_quantity(self) -> t.Tuple[str, dict]:
        permissions_mask: str = "000"
        server_quantity: dict = {}
        for (
            permission
        ) in self.controller.crafty_perms.list_defined_crafty_permissions():
            argument = int(
                float(
                    # pylint: disable=no-member
                    nh3.clean(self.get_argument(f"permission_{permission.name}", "0"))
                )
            )
            if argument:
                permissions_mask = self.controller.crafty_perms.set_permission(
                    permissions_mask, permission, argument
                )

            q_argument = int(
                float(
                    # pylint: disable=no-member
                    nh3.clean(self.get_argument(f"quantity_{permission.name}", "0"))
                )
            )
            if q_argument:
                server_quantity[permission.name] = q_argument
            else:
                server_quantity[permission.name] = 0
        return permissions_mask, server_quantity

    def get_perms(self) -> str:
        permissions_mask: str = "000"
        for (
            permission
        ) in self.controller.crafty_perms.list_defined_crafty_permissions():
            argument = self.get_argument(f"permission_{permission.name}", None)
            if argument is not None and argument == "1":
                permissions_mask = self.controller.crafty_perms.set_permission(
                    permissions_mask, permission, "1"
                )
        return permissions_mask

    def get_perms_server(self) -> str:
        permissions_mask: str = "00000000"
        for permission in self.controller.server_perms.list_defined_permissions():
            argument = self.get_argument(f"permission_{permission.name}", None)
            if argument is not None:
                permissions_mask = self.controller.server_perms.set_permission(
                    permissions_mask, permission, 1 if argument == "1" else 0
                )
        return permissions_mask

    def get_user_role_memberships(self) -> set:
        roles = set()
        for role in self.controller.roles.get_all_roles():
            if self.get_argument(f"role_{role.role_id}_membership", None) == "1":
                roles.add(role.role_id)
        return roles

    def check_subpage_perms(self, user_perms, subpage):
        if SUBPAGE_PERMS.get(subpage, False) in user_perms:
            return True
        return False

    def check_server_id(self):
        server_id = self.get_argument("id", None)

        api_key, _, exec_user = self.current_user
        superuser = exec_user["superuser"]

        # Commented out because there is no server access control for API keys,
        # they just inherit from the host user
        # if api_key is not None:
        #     superuser = superuser and api_key.full_access

        if server_id is None:
            self.redirect("/panel/error?error=Invalid Server ID")
            return None
        for server in self.controller.servers.failed_servers:
            if server_id == server["server_id"]:
                self.failed_server = True
                return server_id
        # Does this server exist?
        if not self.controller.servers.server_id_exists(server_id):
            self.redirect("/panel/error?error=Invalid Server ID")
            return None

        # Does the user have permission?
        if superuser:  # TODO: Figure out a better solution
            return server_id
        if api_key is not None:
            if not self.controller.servers.server_id_authorized_api_key(
                server_id, api_key
            ):
                logger.debug(
                    f"API key {api_key.name} (id: {api_key.token_id}) "
                    f"does not have permission"
                )
                self.redirect("/panel/error?error=Invalid Server ID")
                return None
        else:
            if not self.controller.servers.server_id_authorized(
                server_id, exec_user["user_id"]
            ):
                logger.debug(f'User {exec_user["user_id"]} does not have permission')
                self.redirect("/panel/error?error=Invalid Server ID")
                return None
        return server_id

    def get_earliest_metrics_date(self, server_id):
        """Get the earliest metrics date for Air Datepicker minDate restriction"""
        try:
            earliest = self.controller.servers.get_server_stats_earliest(server_id)
            if earliest is not None:
                return earliest.created.isoformat()
            return None
        except Exception as e:
            logger.warning(f"Failed to get earliest metrics date: {e}")
            return None

    # Server fetching, spawned asynchronously
    # TODO: Make the related front-end elements update with AJAX
    def fetch_server_data(self, page_data):
        total_players = 0
        for server in page_data["servers"]:
            total_players += len(
                self.controller.servers.get_server_instance_by_id(
                    server["server_data"]["server_id"]
                ).get_server_players()
            )
        page_data["num_players"] = total_players

        for server in page_data["servers"]:
            try:
                data = json.loads(server["int_ping_results"])
                server["int_ping_results"] = data
            except Exception as e:
                logger.exception(f"Failed server data for page with error: {e}")

        return page_data

    async def async_fetch_data(self, url: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(url, follow_redirects=True)
            return response.json()

    async def async_read_config(self, file_path: Path):
        async with aiofiles.open(file_path, "r") as file:
            data = await file.read()
        return data

    def _list_embed_images(self):
        embed_dir = os.path.join(
            self.controller.project_root,
            "app/frontend/static/assets/images/embed",
        )
        self.helper.ensure_dir_exists(embed_dir)
        images = []
        for item in os.listdir(embed_dir):
            mime_type, _ = mimetypes.guess_type(item)
            if mime_type and mime_type.startswith("image/"):
                images.append(item)
        return images

    def _list_logo_images(self, kind):
        """List uploaded logo image filenames for a given kind.

        Args:
            kind: "full" or "square" — selects the logos subfolder.

        Returns:
            list of image filenames present in that folder.
        """
        logo_dir = os.path.join(
            self.controller.project_root,
            "app/frontend/static/assets/images/logos",
            kind,
        )
        self.helper.ensure_dir_exists(logo_dir)
        images = []
        for item in os.listdir(logo_dir):
            mime_type, _ = mimetypes.guess_type(item)
            if mime_type and mime_type.startswith("image/"):
                images.append(item)
        return images

    @tornado.web.authenticated
    async def get(self, page):
        self.failed_server = False
        error = self.get_argument("error", "WTF Error!")

        template = "panel/denied.html"
        if self.helper.crafty_starting:
            page = "loading"

        now = time.time()
        formatted_time = str(
            datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
        )

        api_key, token_data, exec_user = self.current_user
        superuser = exec_user["superuser"]
        if api_key is not None:
            superuser = superuser and api_key.full_access

        if superuser:  # TODO: Figure out a better solution
            defined_servers = self.controller.servers.list_defined_servers()
            exec_user_role = {"Super User"}
            exec_user_crafty_permissions = (
                self.controller.crafty_perms.list_defined_crafty_permissions()
            )
        else:
            if api_key is not None:
                exec_user_crafty_permissions = (
                    self.controller.crafty_perms.get_api_key_permissions_list(api_key)
                )
            else:
                exec_user_crafty_permissions = (
                    self.controller.crafty_perms.get_crafty_permissions_list(
                        exec_user["user_id"]
                    )
                )
            logger.debug(exec_user["roles"])
            exec_user_role = set()
            for r in exec_user["roles"]:
                role = self.controller.roles.get_role(r)
                exec_user_role.add(role["role_name"])
            # get_auth_servers will throw an exception if run while Crafty is starting
            if not self.helper.crafty_starting:
                defined_servers = self.controller.servers.get_authorized_servers(
                    exec_user["user_id"]
                )
            else:
                defined_servers = []

        user_order = self.controller.users.get_user_by_id(exec_user["user_id"])
        user_order = user_order["server_order"].split(",")
        page_servers = []
        server_ids = []
        for server in defined_servers:
            server_ids.append(str(server.server_id))
            if str(server.server_id) not in user_order:
                # a little unorthodox, but this will cut out a loop.
                # adding servers to the user order that don't already exist there.
                user_order.append(str(server.server_id))
        for server_id in user_order[:]:
            for server in defined_servers[:]:
                if str(server.server_id) == str(server_id):
                    page_servers.append(
                        DatabaseShortcuts.get_data_obj(server.server_object)
                    )
                    user_order.remove(server_id)
                    defined_servers.remove(server)
                    break
        for server_id in user_order[:]:
            # remove IDs in list that user no longer has access to
            if str(server_id) not in server_ids:
                user_order.remove(server_id)
        defined_servers = page_servers

        try:
            tz = get_localzone()
        except ZoneInfoNotFoundError:
            logger.error(
                "Could not capture time zone from system. Falling back to Europe/London"
            )
            tz = "Europe/London"
        if exec_user["username"] == "anti-lockout-user":
            page = "panel_config"

        page_data: t.Dict[str, t.Any] = {
            # todo: make this actually pull and compare version data
            "mfa": token_data.get(
                "mfa"
            ),  # set value if the token has MFA set to true or not
            # for warning banner
            "password_auth_disabled": exec_user.get("disable_password_auth", False),
            "update_available": self.helper.update_available,
            "support_perm": self.helper.get_setting("general_user_log_access")
            or exec_user["superuser"],
            "docker": self.helper.is_env_docker(),
            "background": self.controller.cached_login,
            "brand": self.controller.cached_brand,
            "login_opacity": self.controller.management.get_login_opacity(),
            "serverTZ": tz,
            "monitored": self.helper.get_setting("monitored_mounts"),
            "version_data": self.helper.get_version_string(),
            "failed_servers": self.controller.servers.failed_servers,
            "user_data": exec_user,
            "user_role": exec_user_role,
            "user_crafty_permissions": exec_user_crafty_permissions,
            "crafty_permissions": {
                "Server_Creation": EnumPermissionsCrafty.SERVER_CREATION,
                "User_Config": EnumPermissionsCrafty.USER_CONFIG,
                "Roles_Config": EnumPermissionsCrafty.ROLES_CONFIG,
            },
            "server_stats": {
                "total": len(defined_servers),
                "running": len(self.controller.servers.list_running_servers()),
                "stopped": (
                    len(self.controller.servers.list_defined_servers())
                    - len(self.controller.servers.list_running_servers())
                ),
            },
            "menu_servers": defined_servers,
            "hosts_data": self.controller.management.get_latest_hosts_stats(),
            "show_contribute": self.helper.get_setting("show_contribute_link", True),
            "error": error,
            "time": formatted_time,
            "lang": self.controller.users.get_user_lang_by_id(exec_user["user_id"]),
            "lang_page": Helpers.get_lang_page(
                self.controller.users.get_user_lang_by_id(exec_user["user_id"])
            ),
            "super_user": superuser,
            "api_key": (
                {
                    "name": api_key.name,
                    "created": api_key.created,
                    "server_permissions": api_key.server_permissions,
                    "crafty_permissions": api_key.crafty_permissions,
                    "full_access": api_key.full_access,
                }
                if api_key is not None
                else None
            ),
            "superuser": superuser,
            "themes": self.helper.get_themes(),
        }
        try:
            page_data["hosts_data"]["disk_json"] = json.loads(
                page_data["hosts_data"]["disk_json"].replace("'", '"')
            )
        except:
            page_data["hosts_data"]["disk_json"] = {}
        if page == "unauthorized":
            template = "panel/denied.html"

        elif page == "error":
            template = "public/error.html"

        elif page == "credits":
            credits_default_local = await self.async_read_config(
                Path(self.helper.credits_cache)
            )
            try:
                credits_dict: dict = await self.async_fetch_data(
                    "https://craftycontrol.com/credits-v2"
                )
                if not credits_dict["staff"]:
                    logger.error("Issue with upstream Staff, using local.")
                    credits_dict: dict = json.loads(credits_default_local)
            except Exception as e:
                logger.exception("Request to credits bucket failed, using local. %s", e)
                credits_dict: dict = json.loads(credits_default_local)

            timestamp = credits_dict["lastUpdate"] / 1000.0
            page_data["patrons"] = credits_dict["patrons"]
            page_data["staff"] = credits_dict["staff"]

            # Filter Language keys to exclude joke prefix '*'
            clean_dict = {
                user.replace("*", ""): translation
                for user, translation in credits_dict["translations"].items()
            }
            page_data["translations"] = clean_dict

            # 0 Defines if we are using local credits file andd displays sadcat.
            if timestamp == 0:
                page_data["lastUpdate"] = "😿"
            else:
                page_data["lastUpdate"] = str(
                    datetime.datetime.fromtimestamp(timestamp).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                )
            template = "panel/credits.html"

        elif page == "contribute":
            template = "panel/contribute.html"

        elif page == "dashboard":
            page_data["first_log"] = self.controller.first_login
            if self.controller.first_login and exec_user["username"] == "admin":
                self.controller.first_login = False
            if superuser:  # TODO: Figure out a better solution
                try:
                    page_data["servers"] = (
                        self.controller.servers.get_all_servers_stats()
                    )
                except IndexError:
                    self.controller.servers.stats.record_stats()
                    page_data["servers"] = (
                        self.controller.servers.get_all_servers_stats()
                    )
            else:
                try:
                    user_auth = self.controller.servers.get_authorized_servers_stats(
                        exec_user["user_id"]
                    )
                except IndexError:
                    self.controller.servers.stats.record_stats()
                    user_auth = self.controller.servers.get_authorized_servers_stats(
                        exec_user["user_id"]
                    )
                logger.debug(f"ASFR: {user_auth}")
                page_data["servers"] = user_auth
                page_data["server_stats"]["running"] = len(
                    list(filter(lambda x: x["stats"]["running"], page_data["servers"]))
                )
                page_data["server_stats"]["stopped"] = (
                    len(page_data["servers"]) - page_data["server_stats"]["running"]
                )

            # set user server order
            user_order = self.controller.users.get_user_by_id(exec_user["user_id"])
            dashboard_cols_raw = user_order.get(
                "dashboard_columns",
                "server,actions,cpuUsage,memUsage,size,players,status",
            )
            if not dashboard_cols_raw or not dashboard_cols_raw.strip():
                dashboard_cols_raw = (
                    "server,actions,cpuUsage,memUsage,size,players,status"
                )
            page_data["dashboard_columns"] = dashboard_cols_raw.split(",")
            user_order = user_order["server_order"].split(",")
            page_servers = []
            server_ids = []
            un_used_servers = page_data["servers"]
            flag = 0
            for server_id in user_order[:]:
                for server in un_used_servers[:]:
                    if flag == 0:
                        server["stats"]["importing"] = (
                            self.controller.servers.get_import_status(
                                str(server["stats"]["server_id"]["server_id"])
                            )
                        )
                        server["stats"]["crashed"] = self.controller.servers.is_crashed(
                            str(server["stats"]["server_id"]["server_id"])
                        )
                        try:
                            server["stats"]["waiting_start"] = (
                                self.controller.servers.get_waiting_start(
                                    str(server["stats"]["server_id"]["server_id"])
                                )
                            )
                        except Exception as e:
                            logger.exception(
                                f"Failed to get server waiting to start: {e}"
                            )
                            server["stats"]["waiting_start"] = False

                    if str(server["server_data"]["server_id"]) == str(server_id):
                        page_servers.append(server)
                        un_used_servers.remove(server)
                        user_order.remove(server_id)
                        break
                # we only want to set these server stats values once.
                # We need to update the flag so it only hits that if once.
                flag += 1

            for server in un_used_servers:
                server_ids.append(str(server["server_data"]["server_id"]))
                if server not in page_servers:
                    page_servers.append(server)
            for server_id in user_order:
                # remove IDs in list that user no longer has access to
                if str(server_id) not in server_ids[:]:
                    user_order.remove(server_id)
            page_data["servers"] = page_servers
            for server in page_data["servers"]:
                server_obj = self.controller.servers.get_server_instance_by_id(
                    server["server_data"]["server_id"]
                )
                server["alert"] = server_obj.last_backup_failed
                server["update"] = (
                    server_obj.update_manager.update_available
                    and server["server_data"]["update_watcher"]
                )  # Only add update notify if user has the watcher enabled

            # num players is set to zero here. If we poll all servers while
            # dashboard is loading it takes FOREVER. We leave this to the
            # async polling once dashboard is served.
            page_data["num_players"] = 0

            template = "panel/dashboard.html"

        elif page == "server_detail":
            # pylint: disable=no-member
            subpage = nh3.clean(self.get_argument("subpage", ""))
            # pylint: enable=no-member

            server_id = self.check_server_id()
            # load page the user was on last
            server_subpage = self.controller.servers.server_subpage.get(server_id, "")
            if (
                subpage == ""
                and server_subpage != ""
                and self.check_subpage_perms(
                    self.controller.server_perms.get_user_id_permissions_list(
                        exec_user["user_id"], server_id
                    ),
                    server_subpage,
                )
            ):
                subpage = server_subpage
            else:
                self.controller.servers.server_subpage[server_id] = subpage
            if server_id is None:
                return
            if not self.failed_server:
                server_obj = self.controller.servers.get_server_instance_by_id(
                    server_id
                )
                page_data["backup_failed"] = server_obj.last_backup_failed
                page_data["update"] = server_obj.update_manager.update_available
                page_data["update_next_run"] = server_obj.server_scheduler.get_job(
                    f"{server_obj.server_id}_update_watcher"
                ).next_run_time.strftime("%m/%d/%Y, %H:%M:%S")
            server_obj = None

            if not self.failed_server:
                server = self.controller.servers.get_server_instance_by_id(server_id)
            # server_data isn't needed since the server_stats also pulls server data
            page_data["server_data"] = self.controller.servers.get_server_data_by_id(
                server_id
            )
            if not self.failed_server:
                page_data["server_stats"] = (
                    self.controller.servers.get_server_stats_by_id(server_id)
                )
            else:
                server_temp_obj = self.controller.servers.get_server_data_by_id(
                    server_id
                )
                page_data["server_stats"] = {
                    "server_id": {
                        "server_id": server_id,
                        "server_name": server_temp_obj["server_name"],
                        "server_uuid": server_temp_obj["server_id"],
                        "path": server_temp_obj["path"],
                        "log_path": server_temp_obj["log_path"],
                        "executable": server_temp_obj["executable"],
                        "execution_command": server_temp_obj["execution_command"],
                        "shutdown_timeout": server_temp_obj["shutdown_timeout"],
                        "stop_command": server_temp_obj["stop_command"],
                        "executable_update_url": server_temp_obj[
                            "executable_update_url"
                        ],
                        "auto_start_delay": server_temp_obj["auto_start_delay"],
                        "server_ip": server_temp_obj["server_ip"],
                        "server_port": server_temp_obj["server_port"],
                        "logs_delete_after": server_temp_obj["logs_delete_after"],
                        "auto_start": server_temp_obj["auto_start"],
                        "crash_detection": server_temp_obj["crash_detection"],
                        "show_status": server_temp_obj["show_status"],
                        "ignored_exits": server_temp_obj["ignored_exits"],
                        "count_players": server_temp_obj["count_players"],
                    },
                    "running": False,
                    "crashed": False,
                    "server_type": "N/A",
                    "cpu": "N/A",
                    "mem": "N/A",
                    "int_ping_results": [],
                    "version": "N/A",
                    "desc": "N/A",
                    "started": "False",
                }
            if not self.failed_server:
                page_data["importing"] = self.controller.servers.get_import_status(
                    server_id
                )
            else:
                page_data["importing"] = False
            page_data["server_id"] = server_id
            try:
                page_data["waiting_start"] = self.controller.servers.get_waiting_start(
                    server_id
                )
            except Exception as e:
                logger.exception(f"Failed to get server waiting to start: {e}")
                page_data["waiting_start"] = False
            page_data["permissions"] = {
                "Commands": EnumPermissionsServer.COMMANDS,
                "Terminal": EnumPermissionsServer.TERMINAL,
                "Logs": EnumPermissionsServer.LOGS,
                "Schedule": EnumPermissionsServer.SCHEDULE,
                "Backup": EnumPermissionsServer.BACKUP,
                "Files": EnumPermissionsServer.FILES,
                "Config": EnumPermissionsServer.CONFIG,
                "Players": EnumPermissionsServer.PLAYERS,
            }
            page_data["user_permissions"] = (
                self.controller.server_perms.get_user_id_permissions_list(
                    exec_user["user_id"], server_id
                )
            )
            if not self.failed_server:
                page_data["server_stats"]["crashed"] = (
                    self.controller.servers.is_crashed(server_id)
                )
            if not self.failed_server:
                page_data["server_stats"]["server_type"] = (
                    self.controller.servers.get_server_type_by_id(server_id)
                )

            if not subpage:
                for spage, perm in SUBPAGE_PERMS.items():
                    if perm in page_data["user_permissions"]:
                        subpage = spage
                        break
                # If we still don't have a subpage we're going to assume they
                # have no perms
                if not subpage:
                    self.redirect("/panel/error?error=Unauthorized access to Server")
            if subpage not in SUBPAGE_PERMS.keys():
                self.set_status(404)
                page_data["background"] = self.controller.cached_login
                return self.render(
                    "public/404.html",
                    data=page_data,
                    translate=self.translator.translate,
                )
            page_data["active_link"] = subpage
            logger.debug(f'Subpage: "{subpage}"')

            if (
                not self.check_subpage_perms(page_data["user_permissions"], subpage)
                and not superuser
            ):
                return self.redirect(
                    f"/panel/error?error=Unauthorized access to {subpage}"
                )

            if subpage == "schedules":
                page_data["schedules"] = HelpersManagement.get_schedules_by_server(
                    server_id
                )

            if subpage == "update_center":
                page_data["server_api"] = (
                    self.controller.big_bucket._check_bucket_alive()
                )
                page_data["server_types"] = self.controller.big_bucket.get_bucket_data()
                page_data["js_server_types"] = json.dumps(
                    self.controller.big_bucket.get_bucket_data()
                )
                if page_data["server_types"] is None:
                    page_data["server_types"] = []
                    page_data["js_server_types"] = []

            if subpage == "config":
                page_data["java_versions"] = Helpers.find_java_installs()
                server_obj: Servers = self.controller.servers.get_server_obj(server_id)
                page_data["failed"] = self.failed_server
                page_java = []
                page_data["java_versions"].append("java")
                for version in page_data["java_versions"]:
                    if os.name == "nt":
                        page_java.append(version)
                    else:
                        if len(version) > 0:
                            page_java.append(version)

                page_data["java_versions"] = page_java
            if subpage == "backup":
                server_info = self.controller.servers.get_server_data_by_id(server_id)

                page_data["backups"] = self.controller.management.get_backups_by_server(
                    server_id, model=True
                )
                page_data["backing_up"] = (
                    self.controller.servers.get_server_instance_by_id(
                        server_id
                    ).is_backingup
                )
                # makes it so relative path is the only thing shown

                self.controller.servers.refresh_server_settings(server_id)

            if subpage == "metrics":
                # Check for custom date range parameters
                start_param = self.get_argument("start", None)
                end_param = self.get_argument("end", None)

                # Support both 'hours' and legacy 'days' parameter
                hours_param = self.get_argument("hours", None)
                days_param = self.get_argument("days", None)

                max_retention_hours = self.helper.get_setting("history_max_age") * 24

                time_range_presets = self.helper.get_setting(
                    "time_range_presets", default_return=False
                )
                if not time_range_presets:
                    time_range_presets = [
                        {
                            "hours": h,
                            "label": MetricsTimeRangeHelper.format_display_label(h),
                        }
                        for h in MetricsTimeRangeHelper.FALLBACK_OPTIONS
                    ]
                page_data["time_range_presets"] = time_range_presets

                sampling_tiers = self.helper.get_setting(
                    "sampling_tiers", default_return=False
                )
                if not sampling_tiers:
                    sampling_tiers = None
                sampling_fallback_divisor = self.helper.get_setting(
                    "sampling_fallback_divisor", default_return=False
                )
                if not sampling_fallback_divisor:
                    sampling_fallback_divisor = 12

                page_data["sampling_tiers"] = (
                    sampling_tiers or MASTER_CONFIG["sampling_tiers"]
                )
                page_data["sampling_fallback_divisor"] = sampling_fallback_divisor

                # Determine if using custom range or preset range
                if start_param and end_param:
                    # Custom date range mode
                    try:
                        # Parse ISO format datetime strings
                        start_time = datetime.datetime.fromisoformat(start_param)
                        end_time = datetime.datetime.fromisoformat(end_param)

                        # Validation: ensure end is not before start
                        if end_time < start_time:
                            self.redirect(
                                "/panel/error?error=End time must be after start time"
                            )
                            return

                        # Same-date selection: expand to full day
                        if end_time == start_time:
                            start_time = start_time.replace(hour=0, minute=0, second=0)
                            end_time = end_time.replace(hour=23, minute=59, second=59)

                        # Fetch stats with custom date range
                        history_stats = (
                            self.controller.servers.get_history_stats_by_date_range(
                                server_id,
                                start_time,
                                end_time,
                                sampling_tiers=sampling_tiers,
                                sampling_fallback_divisor=sampling_fallback_divisor,
                            )
                        )

                        # Calculate hours for display purposes
                        time_delta = end_time - start_time
                        hours = int(time_delta.total_seconds() / 3600)

                        page_data["range_mode"] = "custom"
                        page_data["start_time"] = start_time.isoformat()
                        page_data["end_time"] = end_time.isoformat()

                    except (ValueError, TypeError) as e:
                        self.redirect(f"/panel/error?error=Invalid date format ({e})")
                        return
                else:
                    # Preset range mode (existing logic)
                    try:
                        if hours_param is not None:
                            hours = int(hours_param)
                        elif days_param is not None:
                            days = int(days_param)
                            hours = days * 24
                        else:
                            hours = 24  # Default to 1 day
                    except ValueError as e:
                        self.redirect(
                            "/panel/error?error=Type error: "
                            f"Time argument must be an int ({e})"
                        )
                        return

                    # Validation: clamp to retention limits
                    hours = MetricsTimeRangeHelper.clamp_hours(
                        hours, max_retention_hours
                    )

                    # Fetch stats with adaptive sampling
                    history_stats = self.controller.servers.get_history_stats_adaptive(
                        server_id,
                        hours=hours,
                        sampling_tiers=sampling_tiers,
                        sampling_fallback_divisor=sampling_fallback_divisor,
                    )

                    page_data["range_mode"] = "preset"

                # Generate dropdown options with formatted labels
                hour_options_raw = MetricsTimeRangeHelper.get_time_options(
                    hours, presets=time_range_presets
                )
                page_data["hour_options"] = [
                    {
                        "value": h,
                        "label": MetricsTimeRangeHelper.format_display_label(h),
                    }
                    for h in hour_options_raw
                ]
                page_data["selected_hours"] = hours
                page_data["max_retention_hours"] = max_retention_hours
                page_data["history_stats"] = history_stats
                page_data["earliest_metrics_date"] = self.get_earliest_metrics_date(
                    server_id
                )

                # Fill gaps with zero-value points so the chart
                # spans the full requested range
                if page_data.get("range_mode") == "custom":
                    range_start = datetime.datetime.fromisoformat(
                        page_data["start_time"]
                    )
                    range_end = datetime.datetime.fromisoformat(page_data["end_time"])
                else:
                    range_end = datetime.datetime.now()
                    range_start = range_end - datetime.timedelta(hours=hours)
                history_stats = StatsConverter.fill_gaps(
                    history_stats,
                    start_time=range_start,
                    end_time=range_end,
                )

                # Prepare chart datasets using helper
                page_data["chart_data"] = StatsConverter.prepare_chart_datasets(
                    history_stats, server_type=page_data["server_stats"]["server_type"]
                )
            if subpage == "webhooks":
                page_data["webhooks"] = (
                    self.controller.management.get_webhooks_by_server(
                        server_id, model=True
                    )
                )
                page_data["triggers"] = list(
                    WebhookFactory.get_monitored_events().keys()
                )

            if subpage == "admin_controls":
                if (
                    not page_data["permissions"]["Players"]
                    in page_data["user_permissions"]
                ):
                    if not superuser:
                        self.redirect("/panel/error?error=Unauthorized access")
                page_data["banned_players"] = (
                    self.controller.servers.get_banned_players(server_id)
                )
                server_instance = self.controller.servers.get_server_instance_by_id(
                    server_id
                )
                page_data["cached_players"] = server_instance.player_cache

                for player in page_data["banned_players"]:
                    player["banned"] = True
                    temp_date = datetime.datetime.strptime(
                        player["created"], "%Y-%m-%d %H:%M:%S %z"
                    )
                    player["banned_on"] = (temp_date).strftime("%Y/%m/%d %H:%M:%S")

            template = f"panel/server_{subpage}.html"

        elif page == "panel_config":
            auth_servers = {}
            auth_role_servers = {}
            roles = self.controller.roles.get_all_roles()
            user_roles = {}
            for user in self.controller.users.get_all_users():
                user_roles_list = self.controller.users.get_user_roles_names(
                    user.user_id
                )
                try:
                    user_servers = self.controller.servers.get_authorized_servers(
                        user.user_id
                    )
                except:
                    return self.redirect(
                        "/panel/error?error=Cannot load panel config"
                        " while servers are unloaded"
                    )
                servers = []
                for server in user_servers:
                    if server.name not in servers:
                        servers.append(server.name)
                new_item = {user.user_id: servers}
                auth_servers.update(new_item)
                data = {user.user_id: user_roles_list}
                user_roles.update(data)
            for role in roles:
                role_servers = []
                role = self.controller.roles.get_role_with_servers(role.role_id)
                for serv_id in role["servers"]:
                    role_servers.append(
                        self.controller.servers.get_server_data_by_id(serv_id)[
                            "server_name"
                        ]
                    )
                data = {role["role_id"]: role_servers}
                auth_role_servers.update(data)

            page_data["auth-servers"] = auth_servers
            page_data["role-servers"] = auth_role_servers
            page_data["user-roles"] = user_roles
            page_data["servers_dir"], _tail = os.path.split(
                self.controller.management.get_master_server_dir()
            )

            page_data["users"] = self.controller.users.user_query(exec_user["user_id"])
            page_data["roles"] = self.controller.users.user_role_query(
                exec_user["user_id"]
            )

            for user in page_data["users"]:
                if user.user_id != exec_user["user_id"]:
                    user.api_token = "********"
            if superuser:
                for user in self.controller.users.get_all_users():
                    if user.superuser:
                        super_auth_servers = ["Super User Access To All Servers"]
                        page_data["users"] = self.controller.users.get_all_users()
                        page_data["roles"] = self.controller.roles.get_all_roles()
                        page_data["auth-servers"][user.user_id] = super_auth_servers
                        page_data["managed_users"] = []
            else:
                page_data["managed_users"] = self.controller.users.get_managed_users(
                    exec_user["user_id"]
                )
                page_data["assigned_roles"] = []
                for item in page_data["roles"]:
                    page_data["assigned_roles"].append(item.role_id)

                page_data["managed_roles"] = self.controller.users.get_managed_roles(
                    exec_user["user_id"]
                )

            page_data["active_link"] = "panel_config"
            template = "panel/panel_config.html"

        elif page == "config_json":
            if exec_user["superuser"]:
                page_data["config-json"] = self.helper.get_categorized_settings(
                    self.helper.get_all_settings()
                )

                page_data["availables_languages"] = []
                page_data["all_languages"] = []
                page_data["all_partitions"] = self.helper.get_all_mounts()

                for file in sorted(
                    os.listdir(
                        os.path.join(self.helper.root_dir, "app", "translations")
                    )
                ):
                    if file == HUMANIZED_INDEX_FILE:
                        continue
                    if file.endswith(".json"):
                        if file.split(".")[0] not in self.helper.get_setting(
                            "disabled_language_files"
                        ):
                            page_data["availables_languages"].append(file.split(".")[0])
                        page_data["all_languages"].append(file.split(".")[0])

                page_data["active_link"] = "config_json"
                template = "panel/config_json.html"

        elif page == "updates":
            if exec_user["superuser"]:
                page_data["active_link"] = "updates"
                template = "panel/updates.html"

        elif page == "network":
            if exec_user["superuser"]:
                page_data["active_link"] = "network"
                template = "panel/network.html"

        elif page == "custom_login":
            if exec_user["superuser"]:
                page_data["backgrounds"] = []
                cached_split = self.controller.cached_login.split("/")

                if len(cached_split) == 1:
                    page_data["backgrounds"].append(self.controller.cached_login)
                else:
                    page_data["backgrounds"].append(cached_split[1])
                if "login_1.jpg" not in page_data["backgrounds"]:
                    page_data["backgrounds"].append("login_1.jpg")
                self.helper.ensure_dir_exists(
                    os.path.join(
                        self.controller.project_root,
                        "app/frontend/static/assets/images/auth/custom",
                    )
                )
                for item in os.listdir(
                    os.path.join(
                        self.controller.project_root,
                        "app/frontend/static/assets/images/auth/custom",
                    )
                ):
                    if item not in page_data["backgrounds"]:
                        page_data["backgrounds"].append(item)
                page_data["background"] = self.controller.cached_login
                page_data["login_opacity"] = (
                    self.controller.management.get_login_opacity()
                )
                page_data["embed_settings"] = (
                    self.controller.management.get_embed_settings()
                )
                page_data["embed_images"] = self._list_embed_images()
                page_data["brand_settings"] = (
                    self.controller.management.get_brand_settings()
                )
                page_data["logo_full_images"] = self._list_logo_images("full")
                page_data["logo_square_images"] = self._list_logo_images("square")

                page_data["active_link"] = "custom_login"
                template = "panel/custom_login.html"

        elif page == "add_user":
            page_data["new_user"] = True
            page_data["user"] = {}
            page_data["user"]["username"] = ""
            page_data["user"]["user_id"] = -1
            page_data["user"]["email"] = ""
            page_data["user"]["enabled"] = True
            page_data["user"]["superuser"] = False
            page_data["user"]["created"] = "N/A"
            page_data["user"]["last_login"] = "N/A"
            page_data["user"]["last_ip"] = "N/A"
            page_data["user"]["last_update"] = "N/A"
            page_data["user"]["roles"] = set()
            page_data["user"]["hints"] = True
            page_data["superuser"] = superuser
            page_data["themes"] = self.helper.get_themes()

            if EnumPermissionsCrafty.USER_CONFIG not in exec_user_crafty_permissions:
                self.redirect(
                    "/panel/error?error=Unauthorized access: not a user editor"
                )
                return

            page_data["roles"] = self.controller.roles.get_all_roles()
            page_data["servers"] = []
            page_data["servers_all"] = self.controller.servers.get_all_defined_servers()
            page_data["role-servers"] = []
            page_data["permissions_all"] = (
                self.controller.crafty_perms.list_defined_crafty_permissions()
            )
            page_data["permissions_list"] = set()
            page_data["quantity_server"] = (
                self.controller.crafty_perms.list_all_crafty_permissions_quantity_limits()  # pylint: disable=line-too-long
            )
            page_data["languages"] = []
            page_data["languages"].append(
                self.controller.users.get_user_lang_by_id(exec_user["user_id"])
            )
            if superuser:
                page_data["super-disabled"] = ""
                page_data["users"] = self.controller.users.get_all_users()
            else:
                page_data["super-disabled"] = "disabled"

            page_data["exec_user"] = exec_user["user_id"]

            page_data["manager"] = {
                "user_id": -100,
                "username": "None",
            }
            for file in sorted(
                os.listdir(os.path.join(self.helper.root_dir, "app", "translations"))
            ):
                if file == HUMANIZED_INDEX_FILE:
                    continue
                if file.endswith(".json"):
                    if file.split(".")[0] not in self.helper.get_setting(
                        "disabled_language_files"
                    ):
                        if file != str(page_data["languages"][0] + ".json"):
                            page_data["languages"].append(file.split(".")[0])

            template = "panel/panel_edit_user.html"

        elif page == "add_webhook":
            server_id = self.get_argument("id", None)
            if server_id is None:
                return self.redirect("/panel/error?error=Invalid Server ID")
            server_obj = self.controller.servers.get_server_instance_by_id(server_id)
            page_data["backup_failed"] = server_obj.last_backup_failed
            server_obj = None
            page_data["active_link"] = "webhooks"
            page_data["server_data"] = self.controller.servers.get_server_data_by_id(
                server_id
            )
            page_data["user_permissions"] = (
                self.controller.server_perms.get_user_id_permissions_list(
                    exec_user["user_id"], server_id
                )
            )
            page_data["permissions"] = {
                "Commands": EnumPermissionsServer.COMMANDS,
                "Terminal": EnumPermissionsServer.TERMINAL,
                "Logs": EnumPermissionsServer.LOGS,
                "Schedule": EnumPermissionsServer.SCHEDULE,
                "Backup": EnumPermissionsServer.BACKUP,
                "Files": EnumPermissionsServer.FILES,
                "Config": EnumPermissionsServer.CONFIG,
                "Players": EnumPermissionsServer.PLAYERS,
            }
            page_data["server_stats"] = self.controller.servers.get_server_stats_by_id(
                server_id
            )
            page_data["server_stats"]["server_type"] = (
                self.controller.servers.get_server_type_by_id(server_id)
            )
            page_data["new_webhook"] = True
            page_data["webhook"] = {}
            page_data["webhook"]["webhook_type"] = "Custom"
            page_data["webhook"]["name"] = ""
            page_data["webhook"]["url"] = ""
            page_data["webhook"]["bot_name"] = "Crafty Controller"
            page_data["webhook"]["trigger"] = []
            page_data["webhook"]["body"] = ""
            page_data["webhook"]["color"] = "#005cd1"
            page_data["webhook"]["enabled"] = True

            page_data["providers"] = WebhookFactory.get_supported_providers()
            page_data["triggers"] = list(WebhookFactory.get_monitored_events().keys())

            if not EnumPermissionsServer.CONFIG in page_data["user_permissions"]:
                if not superuser:
                    self.redirect("/panel/error?error=Unauthorized access To Webhooks")
                    return

            template = "panel/server_webhook_edit.html"

        elif page == "webhook_edit":
            server_id = self.get_argument("id", None)
            webhook_id = self.get_argument("webhook_id", None)
            if server_id is None:
                return self.redirect("/panel/error?error=Invalid Server ID")
            server_obj = self.controller.servers.get_server_instance_by_id(server_id)
            page_data["backup_failed"] = server_obj.last_backup_failed
            server_obj = None
            page_data["active_link"] = "webhooks"
            page_data["server_data"] = self.controller.servers.get_server_data_by_id(
                server_id
            )
            page_data["user_permissions"] = (
                self.controller.server_perms.get_user_id_permissions_list(
                    exec_user["user_id"], server_id
                )
            )
            page_data["permissions"] = {
                "Commands": EnumPermissionsServer.COMMANDS,
                "Terminal": EnumPermissionsServer.TERMINAL,
                "Logs": EnumPermissionsServer.LOGS,
                "Schedule": EnumPermissionsServer.SCHEDULE,
                "Backup": EnumPermissionsServer.BACKUP,
                "Files": EnumPermissionsServer.FILES,
                "Config": EnumPermissionsServer.CONFIG,
                "Players": EnumPermissionsServer.PLAYERS,
            }
            page_data["server_stats"] = self.controller.servers.get_server_stats_by_id(
                server_id
            )
            page_data["server_stats"]["server_type"] = (
                self.controller.servers.get_server_type_by_id(server_id)
            )
            page_data["new_webhook"] = False
            page_data["webhook"] = self.controller.management.get_webhook_by_id(
                webhook_id
            )
            page_data["webhook"]["trigger"] = str(
                page_data["webhook"]["trigger"]
            ).split(",")

            page_data["providers"] = WebhookFactory.get_supported_providers()
            page_data["triggers"] = list(WebhookFactory.get_monitored_events().keys())

            if not EnumPermissionsServer.CONFIG in page_data["user_permissions"]:
                if not superuser:
                    self.redirect("/panel/error?error=Unauthorized access To Webhooks")
                    return

            template = "panel/server_webhook_edit.html"

        elif page == "add_schedule":
            server_id = self.get_argument("id", None)
            if server_id is None:
                return self.redirect("/panel/error?error=Invalid Schedule ID")
            server_obj = self.controller.servers.get_server_instance_by_id(server_id)
            page_data["backup_failed"] = server_obj.last_backup_failed
            server_obj = None
            page_data["schedules"] = HelpersManagement.get_schedules_by_server(
                server_id
            )
            page_data["active_link"] = "schedules"
            page_data["permissions"] = {
                "Commands": EnumPermissionsServer.COMMANDS,
                "Terminal": EnumPermissionsServer.TERMINAL,
                "Logs": EnumPermissionsServer.LOGS,
                "Schedule": EnumPermissionsServer.SCHEDULE,
                "Backup": EnumPermissionsServer.BACKUP,
                "Files": EnumPermissionsServer.FILES,
                "Config": EnumPermissionsServer.CONFIG,
                "Players": EnumPermissionsServer.PLAYERS,
            }
            page_data["user_permissions"] = (
                self.controller.server_perms.get_user_id_permissions_list(
                    exec_user["user_id"], server_id
                )
            )
            page_data["server_data"] = self.controller.servers.get_server_data_by_id(
                server_id
            )
            page_data["backups"] = self.controller.management.get_backups_by_server(
                server_id, True
            )
            page_data["server_stats"] = self.controller.servers.get_server_stats_by_id(
                server_id
            )
            page_data["server_stats"]["server_type"] = (
                self.controller.servers.get_server_type_by_id(server_id)
            )
            page_data["new_schedule"] = True
            page_data["schedule"] = {}
            page_data["schedule"]["children"] = []
            page_data["schedule"]["name"] = ""
            page_data["schedule"]["server_id"] = server_id
            page_data["schedule"]["schedule_id"] = ""
            page_data["schedule"]["action"] = ""
            page_data["schedule"]["enabled"] = True
            page_data["schedule"]["command"] = ""
            page_data["schedule"]["one_time"] = False
            page_data["schedule"]["cron_string"] = ""
            page_data["schedule"]["delay"] = 0
            page_data["schedule"]["time"] = ""
            page_data["schedule"]["interval"] = 1
            page_data["schedule"]["action_id"] = ""
            # we don't need to check difficulty here.
            # We'll just default to basic for new schedules
            page_data["schedule"]["difficulty"] = "basic"
            page_data["schedule"]["interval_type"] = "days"
            page_data["parent"] = None

            if not EnumPermissionsServer.SCHEDULE in page_data["user_permissions"]:
                if not superuser:
                    self.redirect(SCHEDULE_AUTH_ERROR_URL)
                    return

            template = "panel/server_schedule_edit.html"

        elif page == "edit_schedule":
            server_id = self.check_server_id()
            if not server_id:
                return self.redirect("/panel/error?error=Invalid Schedule ID")
            server_obj = self.controller.servers.get_server_instance_by_id(server_id)
            page_data["backup_failed"] = server_obj.last_backup_failed
            server_obj = None

            page_data["schedules"] = HelpersManagement.get_schedules_by_server(
                server_id
            )
            sch_id = self.get_argument("sch_id", None)
            if sch_id is None:
                self.redirect("/panel/error?error=Invalid Schedule ID")
                return
            schedule = self.controller.management.get_scheduled_task_model(sch_id)
            page_data["active_link"] = "schedules"
            page_data["permissions"] = {
                "Commands": EnumPermissionsServer.COMMANDS,
                "Terminal": EnumPermissionsServer.TERMINAL,
                "Logs": EnumPermissionsServer.LOGS,
                "Schedule": EnumPermissionsServer.SCHEDULE,
                "Backup": EnumPermissionsServer.BACKUP,
                "Files": EnumPermissionsServer.FILES,
                "Config": EnumPermissionsServer.CONFIG,
                "Players": EnumPermissionsServer.PLAYERS,
            }
            page_data["user_permissions"] = (
                self.controller.server_perms.get_user_id_permissions_list(
                    exec_user["user_id"], server_id
                )
            )
            page_data["backups"] = self.controller.management.get_backups_by_server(
                server_id, True
            )
            page_data["server_data"] = self.controller.servers.get_server_data_by_id(
                server_id
            )
            page_data["server_stats"] = self.controller.servers.get_server_stats_by_id(
                server_id
            )
            page_data["server_stats"]["server_type"] = (
                self.controller.servers.get_server_type_by_id(server_id)
            )
            page_data["new_schedule"] = False
            page_data["schedule"] = {}
            page_data["schedule"]["server_id"] = server_id
            page_data["schedule"]["schedule_id"] = schedule.schedule_id
            page_data["schedule"]["action"] = schedule.action
            page_data["schedule"]["action_id"] = schedule.action_id
            if schedule.name:
                page_data["schedule"]["name"] = schedule.name
            else:
                page_data["schedule"]["name"] = ""
            page_data["schedule"]["children"] = (
                self.controller.management.get_child_schedules(sch_id)
            )
            # We check here to see if the command is any of the default ones.
            # We do not want a user changing to a custom command
            # and seeing our command there.
            if (
                schedule.action != "start"
                or schedule.action != "stop"
                or schedule.action != "restart"
                or schedule.action != "backup"
            ):
                page_data["schedule"]["command"] = schedule.command
            else:
                page_data["schedule"]["command"] = ""
            page_data["schedule"]["delay"] = schedule.delay
            page_data["schedule"]["enabled"] = schedule.enabled
            page_data["schedule"]["one_time"] = schedule.one_time
            page_data["schedule"]["cron_string"] = schedule.cron_string
            page_data["schedule"]["time"] = schedule.start_time
            page_data["schedule"]["interval"] = schedule.interval
            page_data["schedule"]["interval_type"] = schedule.interval_type
            if schedule.interval_type == "reaction":
                difficulty = "reaction"
                page_data["parent"] = None
                if schedule.parent:
                    page_data["parent"] = self.controller.management.get_scheduled_task(
                        schedule.parent
                    )
            elif schedule.cron_string == "":
                difficulty = "basic"
                page_data["parent"] = None
            else:
                difficulty = "advanced"
                page_data["parent"] = None
            page_data["schedule"]["difficulty"] = difficulty

            if not EnumPermissionsServer.SCHEDULE in page_data["user_permissions"]:
                if not superuser:
                    self.redirect(SCHEDULE_AUTH_ERROR_URL)
                    return

            template = "panel/server_schedule_edit.html"

        elif page == "edit_backup":
            server_id = self.get_argument("id", None)
            backup_id = self.get_argument("backup_id", None)
            page_data["active_link"] = "backups"
            page_data["permissions"] = {
                "Commands": EnumPermissionsServer.COMMANDS,
                "Terminal": EnumPermissionsServer.TERMINAL,
                "Logs": EnumPermissionsServer.LOGS,
                "Schedule": EnumPermissionsServer.SCHEDULE,
                "Backup": EnumPermissionsServer.BACKUP,
                "Files": EnumPermissionsServer.FILES,
                "Config": EnumPermissionsServer.CONFIG,
                "Players": EnumPermissionsServer.PLAYERS,
            }
            if not self.failed_server:
                server_obj = self.controller.servers.get_server_instance_by_id(
                    server_id
                )
                page_data["backup_failed"] = server_obj.last_backup_failed
            page_data["user_permissions"] = (
                self.controller.server_perms.get_user_id_permissions_list(
                    exec_user["user_id"], server_id
                )
            )
            server_info = self.controller.servers.get_server_data_by_id(server_id)
            page_data["backup_config"] = self.controller.management.get_backup_config(
                backup_id
            )
            page_data["backups"] = self.controller.management.get_backups_by_server(
                server_id, model=True
            )
            exclusions = []
            page_data["backing_up"] = self.controller.servers.get_server_instance_by_id(
                server_id
            ).is_backingup
            self.controller.servers.refresh_server_settings(server_id)
            try:
                page_data["backup_list"] = server.backup_mgr.list_backups(
                    page_data["backup_config"], server.server_id
                )
            except:
                page_data["backup_list"] = []
            page_data["backup_path"] = Helpers.wtol_path(
                page_data["backup_config"]["backup_location"]
            )
            page_data["server_data"] = self.controller.servers.get_server_data_by_id(
                server_id
            )
            page_data["server_stats"] = self.controller.servers.get_server_stats_by_id(
                server_id
            )
            page_data["server_stats"]["server_type"] = (
                self.controller.servers.get_server_type_by_id(server_id)
            )
            page_data["exclusions"] = (
                self.controller.management.get_excluded_backup_dirs(backup_id)
            )
            # Make exclusion paths relative for page
            for file in page_data["exclusions"]:
                if Helpers.is_os_windows():
                    exclusions.append(file.replace(server_info["path"] + "\\", ""))
                else:
                    exclusions.append(file.replace(server_info["path"] + "/", ""))
            page_data["exclusions"] = exclusions

            if EnumPermissionsServer.BACKUP not in page_data["user_permissions"]:
                if not superuser:
                    self.redirect(SCHEDULE_AUTH_ERROR_URL)
                    return
            template = "panel/server_backup_edit.html"

        elif page == "add_backup":
            server_id = self.get_argument("id", None)
            backup_id = self.get_argument("backup_id", None)
            page_data["active_link"] = "backups"
            page_data["permissions"] = {
                "Commands": EnumPermissionsServer.COMMANDS,
                "Terminal": EnumPermissionsServer.TERMINAL,
                "Logs": EnumPermissionsServer.LOGS,
                "Schedule": EnumPermissionsServer.SCHEDULE,
                "Backup": EnumPermissionsServer.BACKUP,
                "Files": EnumPermissionsServer.FILES,
                "Config": EnumPermissionsServer.CONFIG,
                "Players": EnumPermissionsServer.PLAYERS,
            }
            if not self.failed_server:
                server_obj = self.controller.servers.get_server_instance_by_id(
                    server_id
                )
                page_data["backup_failed"] = server_obj.last_backup_failed
            page_data["user_permissions"] = (
                self.controller.server_perms.get_user_id_permissions_list(
                    exec_user["user_id"], server_id
                )
            )
            server_info = self.controller.servers.get_server_data_by_id(server_id)
            page_data["backup_config"] = {
                "excluded_dirs": [],
                "max_backups": 0,
                "server_id": server_id,
                "backup_location": os.path.join(self.helper.backup_path, server_id),
                "compress": False,
                "shutdown": False,
                "before": "",
                "after": "",
            }
            page_data["backing_up"] = False
            self.controller.servers.refresh_server_settings(server_id)

            page_data["backup_list"] = []
            page_data["backup_path"] = Helpers.wtol_path(
                page_data["backup_config"]["backup_location"]
            )
            page_data["server_data"] = self.controller.servers.get_server_data_by_id(
                server_id
            )
            page_data["server_stats"] = self.controller.servers.get_server_stats_by_id(
                server_id
            )
            page_data["server_stats"]["server_type"] = (
                self.controller.servers.get_server_type_by_id(server_id)
            )
            page_data["exclusions"] = []

            if EnumPermissionsServer.BACKUP not in page_data["user_permissions"]:
                if not superuser:
                    self.redirect(SCHEDULE_AUTH_ERROR_URL)
                    return
            template = "panel/server_backup_edit.html"

        elif page == "edit_user":
            user_id = self.get_argument("id", None)
            role_servers = self.controller.servers.get_authorized_servers(user_id)
            page_role_servers = []
            for server in role_servers:
                page_role_servers.append(server.server_id)
            page_data["new_user"] = False
            page_data["user"] = self.controller.users.get_user_by_id(user_id)
            page_data["servers"] = set()
            page_data["role-servers"] = page_role_servers
            page_data["roles"] = self.controller.roles.get_all_roles()
            page_data["exec_user"] = exec_user["user_id"]
            page_data["servers_all"] = self.controller.servers.get_all_defined_servers()
            page_data["superuser"] = superuser
            page_data["themes"] = self.helper.get_themes()
            if page_data["user"]["manager"] is not None:
                page_data["manager"] = self.controller.users.get_user_by_id(
                    page_data["user"]["manager"]
                )
            else:
                page_data["manager"] = {
                    "user_id": -100,
                    "username": "None",
                }
            if exec_user["superuser"]:
                page_data["users"] = self.controller.users.get_all_users()
            page_data["permissions_all"] = (
                self.controller.crafty_perms.list_defined_crafty_permissions()
            )
            page_data["permissions_list"] = (
                self.controller.crafty_perms.get_crafty_permissions_list(user_id)
            )
            page_data["quantity_server"] = (
                self.controller.crafty_perms.list_crafty_permissions_quantity_limits(
                    user_id
                )
            )
            page_data["languages"] = []
            page_data["languages"].append(
                self.controller.users.get_user_lang_by_id(user_id)
            )
            # checks if super user. If not we disable the button.
            if superuser and str(exec_user["user_id"]) != str(user_id):
                page_data["super-disabled"] = ""
            else:
                page_data["super-disabled"] = "disabled"

            for file in sorted(
                os.listdir(os.path.join(self.helper.root_dir, "app", "translations"))
            ):
                if file == HUMANIZED_INDEX_FILE:
                    continue
                if file.endswith(".json"):
                    if file.split(".")[0] not in self.helper.get_setting(
                        "disabled_language_files"
                    ):
                        if file != str(page_data["languages"][0] + ".json"):
                            page_data["languages"].append(file.split(".")[0])

            if user_id is None:
                self.redirect("/panel/error?error=Invalid User ID")
                return
            if EnumPermissionsCrafty.USER_CONFIG not in exec_user_crafty_permissions:
                if str(user_id) != str(exec_user["user_id"]):
                    self.redirect(
                        "/panel/error?error=Unauthorized access: not a user editor"
                    )
                    return
            if (
                (
                    self.controller.users.get_user_by_id(user_id)["manager"]
                    != exec_user["user_id"]
                )
                and not exec_user["superuser"]
                and str(exec_user["user_id"]) != str(user_id)
            ):
                self.redirect(
                    "/panel/error?error=Unauthorized access: you cannot edit this user"
                )

                page_data["servers"] = []
                page_data["role-servers"] = []
                page_data["roles_all"] = []
                page_data["servers_all"] = []

            if exec_user["user_id"] != page_data["user"]["user_id"]:
                page_data["user"]["api_token"] = "********"

            if exec_user["email"] == "default@example.com":
                page_data["user"]["email"] = ""
            page_data["passkey_enabled"] = self.controller.passkey.is_enabled()
            template = "panel/panel_edit_user.html"

        elif page == "edit_user_apikeys":
            user_id = self.get_argument("id", None)
            page_data["user"] = self.controller.users.get_user_by_id(user_id)
            page_data["api_keys"] = self.controller.users.get_user_api_keys(user_id)
            # self.controller.crafty_perms.list_defined_crafty_permissions()
            page_data["server_permissions_all"] = (
                self.controller.server_perms.list_defined_permissions()
            )
            page_data["crafty_permissions_all"] = (
                self.controller.crafty_perms.list_defined_crafty_permissions()
            )
            page_data["user_crafty_permissions"] = (
                self.controller.crafty_perms.get_crafty_permissions_list(user_id)
            )

            if user_id is None:
                self.redirect("/panel/error?error=Invalid User ID")
                return
            if int(user_id) != exec_user["user_id"] and not exec_user["superuser"]:
                self.redirect(
                    "/panel/error?error=Unauthorized access: you do not have"
                    " permission to edit this user's API keys."
                )
                return

            page_data["passkey_enabled"] = self.controller.passkey.is_enabled()
            template = "panel/panel_edit_user_apikeys.html"

        elif page == "edit_user_otp":
            user_id = self.get_argument("id", None)
            user_obj = self.controller.users.get_user_object(user_id)
            page_data["user"] = self.controller.users.get_user_by_id(user_id)

            codes = []
            user_totp = list(user_obj.totp_user)

            for totp in user_totp:
                codes.append({"name": totp.name, "id": totp.id})

            page_data["totp"] = codes
            page_data["passkey_enabled"] = self.controller.passkey.is_enabled()
            # self.controller.crafty_perms.list_defined_crafty_permissions()

            if user_id is None:
                self.redirect("/panel/error?error=Invalid User ID")
                return
            if int(user_id) != exec_user["user_id"] and not exec_user["superuser"]:
                self.redirect(
                    "/panel/error?error=Unauthorized access: "
                    "you do not have permission to edit this user's OTP settings."
                )
                return

            template = "panel/panel_edit_user_otp.html"

        elif page == "edit_user_passkey":
            user_id = self.get_argument("id", None)
            user_obj = self.controller.users.get_user_object(user_id)
            page_data["user"] = self.controller.users.get_user_by_id(user_id)
            page_data["passkey_enabled"] = self.controller.passkey.is_enabled()

            if not page_data["passkey_enabled"]:
                self.redirect("/panel/error?error=Passkey authentication is disabled")
                return

            passkeys = []
            for pk in user_obj.passkey_user:
                passkeys.append(
                    {
                        "id": pk.id,
                        "name": pk.name,
                        "device_type": pk.device_type,
                        "backed_up": pk.backed_up,
                        "created_at": str(pk.created_at),
                        "last_used_at": (
                            str(pk.last_used_at) if pk.last_used_at else None
                        ),
                    }
                )

            page_data["passkeys"] = passkeys
            page_data["disable_password_auth"] = user_obj.disable_password_auth
            page_data["has_passkeys"] = len(passkeys) > 0

            if user_id is None:
                self.redirect("/panel/error?error=Invalid User ID")
                return
            if int(user_id) != exec_user["user_id"] and not exec_user["superuser"]:
                self.redirect(
                    "/panel/error?error=Unauthorized access: you do not have "
                    "permission to edit this user's passkey settings."
                )
                return

            template = "panel/panel_edit_user_passkey.html"

        elif page == "remove_user":
            # pylint: disable=no-member
            user_id = nh3.clean(self.get_argument("id", None))
            # pylint: enable=no-member

            if (
                not superuser
                and EnumPermissionsCrafty.USER_CONFIG
                not in exec_user_crafty_permissions
            ):
                self.redirect("/panel/error?error=Unauthorized access: not superuser")
                return

            if str(exec_user["user_id"]) == str(user_id):
                self.redirect(
                    "/panel/error?error=Unauthorized access: you cannot delete yourself"
                )
                return
            if user_id is None:
                self.redirect("/panel/error?error=Invalid User ID")
                return
            # does this user id exist?
            target_user = self.controller.users.get_user_by_id(user_id)
            if not target_user:
                self.redirect("/panel/error?error=Invalid User ID")
                return
            if target_user["superuser"]:
                self.redirect("/panel/error?error=Cannot remove a superuser")
                return

            self.controller.users.remove_user(user_id)

            self.controller.management.add_to_audit_log(
                exec_user["user_id"],
                f"Removed user {target_user['username']} (UID:{user_id})",
                server_id=None,
                source_ip=self.get_remote_ip(),
            )
            self.redirect("/panel/panel_config")

        elif page == "add_role":
            user_roles = self.get_user_roles()
            page_data["new_role"] = True
            page_data["role"] = {}
            page_data["role"]["role_name"] = ""
            page_data["role"]["role_id"] = -1
            page_data["role"]["created"] = "N/A"
            page_data["role"]["last_update"] = "N/A"
            page_data["role"]["mfa_required"] = False
            page_data["role"]["servers"] = set()
            page_data["user-roles"] = user_roles
            page_data["users"] = self.controller.users.get_all_users()

            if EnumPermissionsCrafty.ROLES_CONFIG not in exec_user_crafty_permissions:
                self.redirect(
                    "/panel/error?error=Unauthorized access: not a role editor"
                )
                return
            if exec_user["superuser"]:
                defined_servers = self.controller.servers.list_defined_servers()
            else:
                defined_servers = self.controller.servers.get_authorized_servers(
                    exec_user["user_id"]
                )

            page_data["role_manager"] = {
                "user_id": -100,
                "username": "None",
            }
            page_servers = []
            for server in defined_servers:
                if server not in page_servers:
                    page_servers.append(
                        DatabaseShortcuts.get_data_obj(server.server_object)
                    )
            page_data["servers_all"] = page_servers
            page_data["permissions_all"] = (
                self.controller.server_perms.list_defined_permissions()
            )
            page_data["permissions_dict"] = {}
            template = "panel/panel_edit_role.html"

        elif page == "edit_role":
            user_roles = self.get_user_roles()
            page_data["new_role"] = False
            role_id = self.get_argument("id", None)
            role = self.controller.roles.get_role(role_id)
            page_data["role"] = self.controller.roles.get_role_with_servers(role_id)
            if exec_user["superuser"]:
                defined_servers = self.controller.servers.list_defined_servers()
            else:
                defined_servers = self.controller.servers.get_authorized_servers(
                    exec_user["user_id"]
                )
            page_servers = []
            for server in defined_servers:
                if server not in page_servers:
                    page_servers.append(
                        DatabaseShortcuts.get_data_obj(server.server_object)
                    )
            page_data["servers_all"] = page_servers
            page_data["permissions_all"] = (
                self.controller.server_perms.list_defined_permissions()
            )
            page_data["permissions_dict"] = (
                self.controller.server_perms.get_role_permissions_dict(role_id)
            )
            page_data["user-roles"] = user_roles
            page_data["users"] = self.controller.users.get_all_users()

            if page_data["role"]["manager"] is not None:
                page_data["role_manager"] = self.controller.users.get_user_by_id(
                    page_data["role"]["manager"]
                )
            else:
                page_data["role_manager"] = {
                    "user_id": -100,
                    "username": "None",
                }

            if (
                EnumPermissionsCrafty.ROLES_CONFIG not in exec_user_crafty_permissions
                or exec_user["user_id"] != role["manager"]
                and not exec_user["superuser"]
            ):
                self.redirect(
                    "/panel/error?error=Unauthorized access: not a role editor"
                )
                return
            if role_id is None:
                self.redirect("/panel/error?error=Invalid Role ID")
                return

            template = "panel/panel_edit_role.html"

        elif page == "activity_logs":
            template = "panel/activity_logs.html"

        elif page == "wiki":
            template = "panel/wiki.html"
        elif page == "edit_file":
            template = "panel/server_file_edit.html"
        if self.helper.crafty_starting:
            template = "panel/loading.html"
        self.render(
            template,
            data=page_data,
            time=time,
            utc_offset=(time.timezone * -1 / 60 / 60),
            translate=self.translator.translate,
            json=json,
        )
