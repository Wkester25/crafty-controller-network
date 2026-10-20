from app.classes.web.base_api_handler import BaseApiHandler


class ApiServersServerStopAllHandler(BaseApiHandler):
    def post(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        _, _, _, _, user, _ = auth_data

        if not self.require_superuser(auth_data):
            return

        running_servers = self.controller.servers.list_running_servers()

        if not running_servers:
            return self.finish_json(
                200,
                {
                    "status": "ok",
                    "data": {
                        "message": "No running servers to stop",
                        "count": 0,
                    },
                },
            )

        # Audit log the bulk action itself
        self.controller.management.add_to_audit_log(
            user["user_id"],
            f"issued stop_all_servers for {len(running_servers)} running server(s)",
            None,
            self.get_remote_ip(),
        )

        # Queue individual stop commands
        for server in running_servers:
            self.controller.management.send_command(
                user["user_id"],
                server["id"],
                self.get_remote_ip(),
                "stop_server",
                None,
            )

        return self.finish_json(
            200,
            {
                "status": "ok",
                "data": {
                    "message": f"Stopping {len(running_servers)} server(s)",
                    "count": len(running_servers),
                },
            },
        )
