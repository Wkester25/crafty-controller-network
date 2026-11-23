import os
import json
from app.classes.web.base_api_handler import BaseApiHandler


class ApiCraftyLogIndexHandler(BaseApiHandler):
    def get(self, log_type: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_superuser(auth_data):
            return

        log_types = ["audit", "session", "schedule"]
        if log_type not in log_types:
            raise NotImplementedError

        if log_type == "audit":
            with open(
                os.path.join(self.controller.project_root, "logs", "audit.log"),
                "r",
                encoding="utf-8",
            ) as f:
                log_lines = [json.loads(line) for line in f]
                rev_log_lines = log_lines[::-1]

            return self.finish_json(
                200,
                {"status": "ok", "data": rev_log_lines},
            )

        if log_type == "session":
            raise NotImplementedError

        if log_type == "schedule":
            raise NotImplementedError
