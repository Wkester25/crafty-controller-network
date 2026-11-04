from app.classes.web.base_api_handler import BaseApiHandler


class ApiCraftyJarCacheIndexHandler(BaseApiHandler):
    def get(self, refresh):
        if refresh is None:
            refresh = True
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
            _,
        ) = auth_data

        if not self.is_superuser(auth_data):
            return self.finish_json(
                200,
                {
                    "status": "ok",
                    "data": self.controller.big_bucket.get_bucket_data(),
                },
            )
        if refresh:
            self.controller.big_bucket.manual_refresh_cache()
        return self.finish_json(
            200,
            {
                "status": "ok",
                "data": self.controller.big_bucket.get_bucket_data(),
            },
        )


class ApiCraftySteamCacheIndexHandler(BaseApiHandler):
    def get(self):
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
            _,
        ) = auth_data

        if not self.is_superuser(auth_data):
            return self.finish_json(403, {"status": "error", "error": "NOT_AUTHORIZED"})

        self.controller.server_jars.refresh_cache()
        self.finish_json(
            200,
            {
                "status": "ok",
                "data": self.controller.steam_apps.fetch_cache(),
            },
        )
