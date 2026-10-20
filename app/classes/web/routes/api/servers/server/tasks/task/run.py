import json
import logging

from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)


class ApiServersServerTasksTaskRunHandler(BaseApiHandler):
    """API handler for manually triggering a scheduled task to run immediately."""

    def _not_authorized(self, auth_data):
        """Return a standard 400 NOT_AUTHORIZED response.

        Args:
            auth_data: The tuple returned by ``authenticate_user``, used to
                resolve the requesting user's language for the error message.

        Returns:
            A finished JSON response describing the authorization failure.
        """
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

    def post(self, server_id: str, task_id: str):
        """Queue a scheduled task's action for immediate execution.

        Authenticates the requesting user, verifies they have access to the
        server and hold the SCHEDULE permission, then hands the task off to the
        tasks manager to be queued. The manual run is recorded in the audit log.

        An optional JSON body of ``{"cascade": true}`` additionally triggers any
        enabled reaction tasks chained to this task. When the body is absent or
        ``cascade`` is omitted, only the selected task runs.

        Args:
            server_id: ID of the server the task belongs to.
            task_id: ID of the scheduled task to run.

        Returns:
            A JSON response with status "ok" on success, or a 400 error
            response if the user is unauthorized, the body is invalid JSON, or
            the task fails to queue.
        """
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        if server_id not in [str(x["server_id"]) for x in auth_data[0]]:
            return self._not_authorized(auth_data)

        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        server_permissions = self.controller.server_perms.get_permissions(mask)
        if EnumPermissionsServer.SCHEDULE not in server_permissions:
            return self._not_authorized(auth_data)

        cascade = False
        if self.request.body:
            try:
                data = json.loads(self.request.body)
            except json.JSONDecodeError as why:
                return self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "INVALID_JSON",
                        "error_data": str(why),
                    },
                )
            cascade = bool(data.get("cascade", False))

        try:
            self.tasks_manager.run_task_now(
                task_id, auth_data[4]["user_id"], server_id, cascade
            )
        except Exception as why:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "RUN_TASK_FAILED",
                    "error_data": str(why),
                },
            )

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            f"Ran scheduled task {task_id} manually for server {server_id}",
            server_id,
            self.get_remote_ip(),
        )

        return self.finish_json(200, {"status": "ok"})
