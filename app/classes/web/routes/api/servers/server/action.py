import logging
import os
import json
from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.models.servers import Servers
from app.classes.helpers.file_helpers import FileHelpers
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)


class ApiServersServerActionHandler(BaseApiHandler):
    def post(self, server_id: str, action: str, action_id=None):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        if server_id not in [str(x["server_id"]) for x in auth_data[0]]:
            # if the user doesn't have access to the server, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        server_permissions = self.controller.server_perms.get_permissions(mask)
        if EnumPermissionsServer.COMMANDS not in server_permissions:
            # if the user doesn't have Commands permission, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )

        if action == "clone_server":
            if self.controller.crafty_perms.can_create_server(
                auth_data[4]["user_id"]
            ) or self.is_superuser(auth_data):
                srv_object = self.controller.servers.get_server_instance_by_id(
                    server_id
                )
                if srv_object.check_running():
                    return self.finish_json(
                        409,
                        {
                            "status": "error",
                            "error": "Server Running!",
                        },
                    )
                self._clone_server(server_id, auth_data[4]["user_id"])
                return self.finish_json(200, {"status": "ok"})
            return self.finish_json(
                200,
                {
                    "status": "error",
                    "error": "SERVER_LIMIT_REACHED",
                    "error_data": "LIMIT REACHED",
                },
            )
        if action == "eula":
            return self._agree_eula(server_id, auth_data[4]["user_id"])

        self.controller.management.send_command(
            auth_data[4]["user_id"], server_id, self.get_remote_ip(), action, action_id
        )

        self.finish_json(
            200,
            {"status": "ok"},
        )

    def _agree_eula(self, server_id, user):
        svr = self.controller.servers.get_server_instance_by_id(server_id)
        svr.agree_eula(user)
        return self.finish_json(200, {"status": "ok"})

    def _clone_server(self, server_id, user_id):
        def is_name_used(name):
            return Servers.select().where(Servers.server_name == name).exists()

        server_data = self.controller.servers.get_server_data_by_id(server_id)
        new_server_name = server_data.get("server_name") + " (Copy)"

        name_counter = 1
        while is_name_used(new_server_name):
            name_counter += 1
            new_server_name = server_data.get("server_name") + f" (Copy {name_counter})"

        new_server_id = self.helper.create_uuid()
        new_server_path = os.path.join(self.helper.servers_dir, new_server_id)
        new_backup_path = os.path.join(self.helper.backup_path, new_server_id)
        backup_data = {
            "backup_name": f"{new_server_name} Backup",
            "backup_location": new_backup_path,
            "excluded_dirs": "",
            "max_backups": 0,
            "server_id": new_server_id,
            "compress": False,
            "shutdown": False,
            "before": "",
            "after": "",
            "default": True,
            "status": json.dumps({"status": "Standby", "message": ""}),
            "enabled": True,
        }
        new_server_command = str(server_data.get("execution_command")).replace(
            server_id, new_server_id
        )
        new_server_log_path = server_data.get("log_path").replace(
            server_id, new_server_id
        )

        self.controller.register_server(
            new_server_name,
            new_server_id,
            new_server_path,
            new_server_command,
            server_data.get("executable"),
            new_server_log_path,
            server_data.get("stop_command"),
            server_data.get("server_port"),
            user_id,
            server_data.get("type"),
        )

        self.controller.management.add_backup_config(backup_data)

        self.controller.management.add_to_audit_log(
            user_id,
            f"is cloning server {server_id} named {server_data.get('server_name')}",
            server_id,
            self.get_remote_ip(),
        )

        # copy the old server
        FileHelpers.copy_dir(server_data.get("path"), new_server_path)

        for role in self.controller.server_perms.get_server_roles(server_id):
            mask = self.controller.server_perms.get_permissions_mask(
                role.role_id, server_id
            )
            self.controller.server_perms.add_role_server(
                new_server_id, role.role_id, mask
            )

        self.controller.servers.init_all_servers()

        self.finish_json(
            200,
            {"status": "ok", "data": {"new_server_id": str(new_server_id)}},
        )
