import os
from pathlib import Path
from app.classes.web.base_api_handler import BaseApiHandler


class ApiCraftySupportIndexHandler(BaseApiHandler):
    async def get(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        (
            _,
            _,
            _,
            _,
            _,
            _,
        ) = auth_data

        if not self.is_superuser(auth_data) and not self.helper.get_setting(
            "general_user_log_access"
        ):
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
        # All server permission checking occurs in the package function
        self.controller.package_support_logs(auth_data[4])

        self.controller.management.add_to_audit_log(
            auth_data[4]["user_id"],
            "started support log download.",
            None,
            self.request.remote_ip,
        )
        await self.download_file(
            Path(
                self.controller.project_root,
                "temp",
                str(auth_data[4]["user_id"]),
                "support_logs",
            ).with_suffix(".zip")
        )
        os.remove(
            Path(
                self.controller.project_root,
                "temp",
                str(auth_data[4]["user_id"]),
                "support_logs",
            ).with_suffix(".zip")
        )
