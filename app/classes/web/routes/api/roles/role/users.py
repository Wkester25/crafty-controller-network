from app.classes.web.base_api_handler import BaseApiHandler


class ApiRolesRoleUsersHandler(BaseApiHandler):
    def get(self, role_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        if not self.require_superuser(auth_data):
            return

        all_user_ids = self.controller.users.get_all_user_ids()

        user_roles = {}
        for user_id in all_user_ids:
            user_roles_list = self.controller.users.get_user_roles_names(user_id)
            user_roles[user_id] = user_roles_list

        role = self.controller.roles.get_role(role_id)

        user_ids = []

        for user_id in all_user_ids:
            for role_user in user_roles[user_id]:
                if role_user == role["role_name"]:
                    user_ids.append(user_id)

        self.finish_json(200, {"status": "ok", "data": user_ids})
