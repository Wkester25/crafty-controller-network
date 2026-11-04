import logging
import nh3

from app.classes.web.base_handler import BaseHandler

logger = logging.getLogger(__name__)
auth_log = logging.getLogger("auth")

LOGIN_URL = "/login"


class PublicHandler(BaseHandler):
    def get(self, page=None):
        # pylint: disable=no-member
        error = nh3.clean(self.get_argument("error", "Invalid Login!"))
        error_msg = nh3.clean(self.get_argument("error_msg", ""))
        # pylint: enable=no-member

        page_data = {
            "version": self.helper.get_version_string(),
            "error": error,
            "lang": self.helper.get_setting("language"),
            "lang_page": self.helper.get_lang_page(self.helper.get_setting("language")),
            "query": "",
            "background": self.controller.cached_login,
            "brand": self.controller.cached_brand,
            "login_opacity": self.controller.management.get_login_opacity(),
            "themes": self.helper.get_themes(),
        }
        page_data["embed"] = self.get_embed_meta(LOGIN_URL)

        if self.request.query:
            request_query = self.request.query_arguments.get("next")
            if not request_query:
                # No next param, so don't fall through to request_query[0]
                return self.redirect(LOGIN_URL)
            page_data["query"] = request_query[0].decode()

        match page:
            case "login":
                template = "public/login.html"

            case "404":
                template = "public/404.html"

            case "error":
                template = "public/error.html"

            case "offline":
                template = "public/offline.html"

            case "logout":
                exec_user = self.get_current_user()
                self.clear_cookie("token")
                # Delete anti-lockout-user on lockout...it's one time use
                if exec_user[2]["username"] == "anti-lockout-user":
                    self.controller.users.stop_anti_lockout()
                # self.clear_cookie("user")
                # self.clear_cookie("user_data")
                self.redirect(LOGIN_URL)
                return

            # if we have no page, let's go to login
            case _:
                return self.redirect(LOGIN_URL)

        self.render(
            template,
            data=page_data,
            translate=self.translator.translate,
            error_msg=error_msg,
        )

    def post(self, _page=None):
        self.redirect("/login?")
