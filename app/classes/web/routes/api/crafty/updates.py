import logging

from tornado.ioloop import IOLoop

from app.classes.shared.fork_updater import UpdateError
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)


class _UpdatesBase(BaseApiHandler):
    def _superuser_auth(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return None
        if not auth_data[4]["superuser"]:
            self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )
            return None
        return auth_data

    async def _ok(self):
        # status() shells out to git/systemctl; keep the IOLoop free
        data = await IOLoop.current().run_in_executor(
            None, self.controller.fork_updater.status
        )
        # The page waits for this before reloading after an update; while it
        # is true Crafty serves its "starting" page instead of this one
        data["crafty_starting"] = bool(self.helper.crafty_starting)
        self.finish_json(200, {"status": "ok", "data": data})


class ApiCraftyUpdatesHandler(_UpdatesBase):
    async def get(self):
        if not self._superuser_auth():
            return
        await self._ok()


class ApiCraftyUpdatesActionHandler(_UpdatesBase):
    async def post(self, action: str):
        auth_data = self._superuser_auth()
        if not auth_data:
            return
        updater = self.controller.fork_updater
        func = {"check": updater.check, "apply": updater.request_update}.get(action)
        if not func:
            return self.finish_json(
                404, {"status": "error", "error": "UNKNOWN_ACTION", "error_data": action}
            )
        try:
            await IOLoop.current().run_in_executor(None, func)
        except UpdateError as why:
            return self.finish_json(
                400, {"status": "error", "error": "UPDATE_ERROR", "error_data": str(why)}
            )
        if action == "apply":
            self.controller.management.add_to_audit_log(
                auth_data[4]["user_id"],
                "started a Crafty update from the web UI",
                server_id=None,
                source_ip=self.get_remote_ip(),
            )
        await self._ok()
