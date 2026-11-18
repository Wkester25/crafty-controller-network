import typing as t
from jsonschema import ValidationError, validate
import orjson
from playhouse.shortcuts import model_to_dict
from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.web.base_api_handler import BaseApiHandler

create_role_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "pattern": r"^[^,\[\]]*$",
            "error": "roleName",
        },
        "servers": {
            "type": "array",
            "error": "typeList",
            "fill": True,
            "items": {
                "type": "object",
                "properties": {
                    "server_id": {
                        "type": "string",
                        "minimum": 1,
                        "error": "roleServerId",
                    },
                    "permissions": {
                        "type": "string",
                        "pattern": r"^[01]{8}$",  # 8 bits, see EnumPermissionsServer
                        "error": "roleServerPerms",
                    },
                },
                "required": ["server_id", "permissions"],
            },
        },
        "manager": {"type": ["integer", "null"], "error": "roleManager"},
        "mfa_required": {"type": ["boolean"], "error": "typeBool", "fill": True},
    },
    "additionalProperties": False,
    "minProperties": 1,
}

basic_create_role_schema = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "error": "roleName",
        },
        "servers": {
            "type": "array",
            "error": "typeList",
            "fill": True,
            "items": {
                "type": "object",
                "properties": {
                    "server_id": {
                        "type": "string",
                        "minimum": 1,
                        "error": "roleServerId",
                    },
                    "permissions": {
                        "type": "string",
                        "pattern": r"^[01]{8}$",  # 8 bits, see EnumPermissionsServer
                        "error": "roleServerPerms",
                    },
                },
                "required": ["server_id", "permissions"],
            },
        },
        "mfa_required": {"type": "boolean", "error": "typeBool", "fill": True},
    },
    "additionalProperties": False,
    "minProperties": 1,
}


class ApiRolesIndexHandler(BaseApiHandler):
    def get(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        (
            _,
            exec_user_permissions_crafty,
            _,
            superuser,
            _,
            _,
            _,
        ) = auth_data

        # GET /api/v2/roles?ids=true
        get_only_ids = self.get_query_argument("ids", None) == "true"

        if (
            not superuser
            and EnumPermissionsCrafty.ROLES_CONFIG not in exec_user_permissions_crafty
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

        self.finish_json(
            200,
            {
                "status": "ok",
                "data": (
                    self.controller.roles.get_all_role_ids()
                    if get_only_ids
                    else [
                        model_to_dict(r) for r in self.controller.roles.get_all_roles()
                    ]
                ),
            },
        )

    def post(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        (
            _,
            exec_user_permissions_crafty,
            _,
            superuser,
            user,
            _,
            _,
        ) = auth_data

        if (
            not superuser
            and EnumPermissionsCrafty.ROLES_CONFIG not in exec_user_permissions_crafty
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

        try:
            data = orjson.loads(self.request.body)
        except orjson.JSONDecodeError as e:
            return self.finish_json(
                400, {"status": "error", "error": "INVALID_JSON", "error_data": str(e)}
            )

        try:
            if self.is_superuser(auth_data):
                validate(data, create_role_schema)
            else:
                validate(data, basic_create_role_schema)
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

        role_name = data["name"]
        manager = data.get("manager", None)
        if not superuser and not manager:
            manager = auth_data[4]["user_id"]
        if manager == self.controller.users.get_id_by_name("SYSTEM") or manager == 0:
            manager = None

        # Get the servers
        servers_dict = {server["server_id"]: server for server in data["servers"]}
        server_ids = (
            (
                {server["server_id"] for server in data["servers"]}
                & set(self.controller.servers.get_all_server_ids())
            )  # Only allow existing servers
            if "servers" in data
            else set()
        )
        servers: t.List[dict] = [servers_dict[server_id] for server_id in server_ids]

        if self.controller.roles.get_roleid_by_name(role_name) is not None:
            return self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "ROLE_NAME_ALREADY_EXISTS",
                    "error_data": "UNIQUE VALUE ERROR",
                },
            )

        role_id = self.controller.roles.add_role_advanced(
            role_name, servers, manager, data["mfa_required"]
        )

        self.controller.management.add_to_audit_log(
            user["user_id"],
            f"created role {role_name} (RID:{role_id})",
            server_id=None,
            source_ip=self.get_remote_ip(),
        )

        self.finish_json(
            200,
            {"status": "ok", "data": {"role_id": role_id}},
        )
