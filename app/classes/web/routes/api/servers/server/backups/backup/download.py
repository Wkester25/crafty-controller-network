import logging
import html
from pathlib import Path
from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)
ID_MISMATCH = "Server ID backup server ID different"
GENERAL_AUTH_ERROR = "Authorization Error"


class ApiServersServerBackupsBackupDownloadHandler(BaseApiHandler):
    async def get(self, server_id: str, backup_id: str, encoded_file_name: str):
        logger.debug(
            "Download file request received. server_id: %s, encoded file path: %s",
            server_id,
            encoded_file_name,
        )
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        backup_conf = self.controller.management.get_backup_config(backup_id)
        raw_file_name = html.unescape(encoded_file_name)
        file_name = Path(
            backup_conf["backup_location"], str(backup_conf["backup_id"]), raw_file_name
        )
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        if backup_conf["server_id"]["server_id"] != server_id:
            # Check to make sure backup ID is related to server ID
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "ID_MISMATCH",
                    "error_data": ID_MISMATCH,
                },
            )
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
        server_permissions = self.controller.server_perms.get_permissions(mask)
        if EnumPermissionsServer.BACKUP not in server_permissions:
            # if the user doesn't have Schedule permission, return an error
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": GENERAL_AUTH_ERROR,
                },
            )
        try:
            if not self.helper.validate_traversal(
                backup_conf["backup_location"], file_name
            ):
                return self.finish_json(
                    403,
                    {
                        "status": "error",
                        "error": "NOT_AUTHORIZED",
                        "error_data": GENERAL_AUTH_ERROR,
                    },
                )
        except ValueError:
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": GENERAL_AUTH_ERROR,
                },
            )
        if not self.helper.check_file_exists(str(file_name)):
            return self.finish_json(
                404,
                {
                    "status": "error",
                    "error": "NOT_FOUND",
                    "error_data": "File does not exist",
                },
            )

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"started backup file {file_name} download from server {server_id}.",
            server_id,
            self.request.remote_ip,
        )
        await self.download_file(file_name)

        # Do not remove file after download. user may still want it
