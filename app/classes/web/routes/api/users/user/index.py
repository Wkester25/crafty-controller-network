import json
import logging
import typing as t

from jsonschema import ValidationError, validate
from app.classes.controllers.users_controller import UsersController
from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.models.roles import HelperRoles
from app.classes.models.users import HelperUsers
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)


class ApiUsersUserIndexHandler(BaseApiHandler):
    def get(self, user_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            _,
            user,
            _,
            _,
        ) = auth_data

        if user_id in ["@me", user["user_id"]]:
            user_id = user["user_id"]
            res_user = user
        elif not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                },
            )
        else:
            # has User_Config permission and isn't viewing self
            res_user = self.controller.users.get_user_by_id(user_id)
            if not res_user:
                return self.finish_json(
                    404,
                    {
                        "status": "error",
                        "error": "USER_NOT_FOUND",
                    },
                )

        # Remove password and valid_tokens_from from the response
        # as those should never be sent out to the client.
        res_user.pop("password", None)
        res_user.pop("valid_tokens_from", None)
        res_user["roles"] = list(
            map(HelperRoles.get_role, res_user.get("roles", set()))
        )

        self.finish_json(
            200,
            {"status": "ok", "data": res_user},
        )

    def delete(self, user_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            _,
            user,
            _,
            _,
        ) = auth_data

        if user_id == "@me" or int(user_id) == int(user["user_id"]):
            if not self.helper.get_setting("enable_user_self_delete", False):
                # Confirm users can delete themselves on this server
                return self.finish_json(
                    403,
                    {
                        "status": "error",
                        "error": "NOT_AUTHORIZED",
                        "error_data": "Self delete is not enabled on this server.",
                    },
                )
            user_id = user["user_id"]  # Set user ID of request to requester ID with @me
        elif not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": "Not authorized to delete user",
                },
            )

        user_model = self.controller.users.get_user_object(user_id)
        for totp in user_model.totp_user:
            self.controller.totp.delete_user_totp(totp, user_model.user_id)
        self.controller.totp.remove_all_recovery_codes(user_model.user_id)
        self.controller.users.remove_user(user_id)

        self.controller.management.add_to_audit_log(
            user["user_id"],
            f"deleted the user {user_id}",
            server_id=None,
            source_ip=self.get_remote_ip(),
        )

        self.finish_json(
            200,
            {"status": "ok"},
        )

    def patch(self, user_id: str):
        user_patch_schema = {
            "type": "object",
            "properties": {
                **self.controller.users.user_jsonschema_props,
            },
            "additionalProperties": False,
            "minProperties": 1,
        }
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            superuser,
            user,
            _,
            _,
        ) = auth_data

        try:
            data = json.loads(self.request.body)
        except json.decoder.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )
        try:
            validate(data, user_patch_schema)
        except ValidationError as why:
            offending_key = ""
            if why.schema.get("fill", None):
                offending_key = why.path[0] if why.path else None
            err = f"""{offending_key} {self.translator.translate(
                "validators",
                why.schema.get("error", "additionalProperties"),
                self.controller.users.get_user_lang_by_id(auth_data[4]["user_id"]),
            )} {why.schema.get("enum", "")}"""
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": f"{str(err)}",
                },
            )
        if (
            user_id == "@me" or str(auth_data[4]["user_id"]) == str(user_id)
        ) and data.get(
            "enabled"
        ) is False:  # User cannot enable or disable themselves
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                    "error_data": self.helper.translation.translate(
                        "userConfig", "selfDisable", auth_data[4]["lang"]
                    ),
                },
            )
        if user_id == "@me":
            user_id = user["user_id"]
        if not self.can_modify_user(exec_user_crafty_permissions, auth_data, user_id):
            # If doesn't have perm can't edit other users
            return self.finish_json(
                403,
                {
                    "status": "error",
                    "error": "NOT_AUTHORIZED",
                },
            )

        if "username" in data:
            if data["username"].lower() in ["system", ""]:
                return self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "INVALID_USERNAME",
                        "error_data": "INVALID USERNAME",
                    },
                )
            if self.controller.users.get_id_by_name(
                data["username"]
            ) is not None and self.controller.users.get_id_by_name(
                data["username"]
            ) != int(
                user_id
            ):
                return self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "USER_EXISTS",
                        "error_data": "UNIQUE CONSTAINT FAILED",
                    },
                )

        if "superuser" in data:
            if str(user["user_id"]) == str(user_id) and not superuser:
                # Checks if user is trying to change super user status
                # of self without superuser. We don't want that.
                return self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "INVALID_SUPERUSER_MODIFY",
                        "error_data": self.helper.translation.translate(
                            "validators", "insufficientPerms", auth_data[4]["lang"]
                        ),
                    },
                )
            if not superuser:
                # The user is not superuser so they can't change the superuser status
                data.pop("superuser")

        if "permissions" in data:
            if str(user["user_id"]) == str(user_id) and not superuser:
                # Checks if user is trying to change permissions
                # of self without superuser. We don't want that.
                return self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "INVALID_PERMISSIONS_MODIFY",
                        "error_data": self.helper.translation.translate(
                            "validators", "insufficientPerms", auth_data[4]["lang"]
                        ),
                    },
                )
            if not self.can_modify_user(
                exec_user_crafty_permissions, auth_data, user_id
            ):
                # Checks if user is trying to change permissions of someone
                # else without User Config permission. We don't want that.
                return self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "INVALID_PERMISSIONS_MODIFY",
                        "error_data": self.helper.translation.translate(
                            "validators", "insufficientPerms", auth_data[4]["lang"]
                        ),
                    },
                )

        if "roles" in data:
            if str(user["user_id"]) == str(user_id) and not superuser:
                # Checks if user is trying to change roles of
                # self without superuser. We don't want that.
                return self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "INVALID_ROLES_MODIFY",
                        "error_data": self.helper.translation.translate(
                            "validators", "insufficientPerms", auth_data[4]["lang"]
                        ),
                    },
                )
            if not self.can_modify_user(
                exec_user_crafty_permissions, auth_data, user_id
            ):
                # Checks if user is trying to change roles of someone
                # else without User Config permission. We don't want that.
                return self.finish_json(
                    400,
                    {
                        "status": "error",
                        "error": "INVALID_ROLES_MODIFY",
                        "error_data": self.helper.translation.translate(
                            "validators", "insufficientPerms", auth_data[4]["lang"]
                        ),
                    },
                )
            user_modify = self.controller.users.get_user_roles_id(user_id)

            for role in data["roles"]:
                # Check if user is not a super user and that the exec user is the role
                # manager or that the role already exists in the user's list
                if not superuser and (
                    str(
                        self.controller.roles.get_role(role).get(
                            "manager", "no manager found"
                        )
                    )
                    != str(auth_data[4]["user_id"])
                    and role not in user_modify
                ):
                    for item in user_modify:
                        print(type(role), type(item))
                    return self.finish_json(
                        400,
                        {
                            "status": "error",
                            "error": "INVALID_ROLES_MODIFY",
                            "error_data": self.helper.translation.translate(
                                "error", "no-file", auth_data[4]["lang"]
                            ),
                        },
                    )

        user_obj = HelperUsers.get_user_model(user_id)
        if (
            "password" in data
            and str(user["user_id"]) != str(user_id)
            and str(user["user_id"]) != str(user_obj.manager)
            and not self.is_superuser(auth_data)
        ):
            # TODO: edit your own password
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_PASSWORD_MODIFY",
                    "error_data": self.helper.translation.translate(
                        "validators", "insufficientPerms", auth_data[4]["lang"]
                    ),
                },
            )

        if "roles" in data:
            roles: t.Set[str] = set(data.pop("roles"))
            base_roles: t.Set[str] = set(user_obj.roles)
            added_roles = roles.difference(base_roles)
            removed_roles = base_roles.difference(roles)
            logger.debug(
                f"updating user {user_id}'s roles: "
                f"+role:{added_roles} -role:{removed_roles}"
            )

            for role_id in added_roles:
                HelperUsers.get_or_create(user_id, role_id)

            if len(removed_roles) != 0:
                self.controller.users.users_helper.delete_user_roles(
                    user_id, removed_roles
                )

        if "manager" in data and (
            data["manager"] == self.controller.users.get_id_by_name("SYSTEM")
            or data["manager"] == 0
        ):
            data["manager"] = None
        crafty_perms = None
        if "permissions" in data:
            permissions: t.List[UsersController.ApiPermissionDict] = data.pop(
                "permissions"
            )
            permissions_mask = "0" * len(EnumPermissionsCrafty)
            if permissions is not None:
                server_quantity = {}
                permissions_mask = list(permissions_mask)
                for permission in permissions:
                    server_quantity[permission["name"]] = permission["quantity"]
                    permissions_mask[
                        EnumPermissionsCrafty[permission["name"]].value
                    ] = ("1" if permission["enabled"] else "0")
                permissions_mask = "".join(permissions_mask)
                crafty_perms = {
                    "permissions_mask": permissions_mask,
                    "server_quantity": server_quantity,
                }
        # TODO: make this more efficient
        if len(data) != 0:
            for key in data:
                # If we don't validate the input there could be security issues
                value = data[key]
                if key == "password":
                    value = self.helper.encode_pass(value)
                setattr(user_obj, key, value)
        self.controller.users.update_user(
            user_id,
            data,
            crafty_perms,
        )

        # Suppress notifications for preference-only updates (e.g. column
        # visibility, server ordering) to avoid spamming, but still audit log.
        silent_fields = set(self.helper.get_setting("silent_notif_fields", []))
        notify = not set(data.keys()).issubset(silent_fields)
        self.controller.management.add_to_audit_log(
            user["user_id"],
            (
                f"edited user {user_obj.username} (UID: {user_id})"
                f"with roles {user_obj.roles}"
            ),
            server_id=None,
            source_ip=self.get_remote_ip(),
            notify=notify,
        )

        return self.finish_json(200, {"status": "ok"})
