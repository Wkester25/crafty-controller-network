import logging

from jsonschema import ValidationError, validate
import orjson
from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)


IMPORT_DESCRIPTION = "Zip file in import mount"
IMPORT_TITLE = "Zip name"
ZIP_NAME_EXAMPLE = "Server.zip"
ARCHIVE_PATH_TITLE = "Internal zip path"
ARCHIVE_PATH_DESCRIPTION = "Path to internal zip folder"
ARCHIVE_PATH_EXAMPLE = "server_files/my_server/"
MIN_MEM = "Minimum JVM memory (in GiBs)"
MAX_MEM = "Maximum JVM memory (in GiBs)"

new_server_schema = {
    "definitions": {},
    "$schema": "https://json-schema.org/draft-07/schema#",
    "title": "Root",
    "type": "object",
    "required": [
        "name",
        "monitoring_type",
        "create_type",
    ],
    "properties": {
        "name": {
            "title": "Name",
            "type": "string",
            "examples": ["My Server"],
            "minLength": 2,
            "pattern": r"^[^/\\\\#]*$",
            "error": "serverCreateName",
        },
        "roles": {
            "title": "Roles to add",
            "type": "array",
            "examples": [1, 2, 3],
            "error": "typeList",
        },
        "stop_command": {
            "title": "Stop command",
            "description": '"" means the default for the server creation type.',
            "type": "string",
            "default": "",
            "examples": ["stop", "end"],
            "error": "typeString",
            "fill": True,
        },
        "log_location": {
            "title": "Log file",
            "description": '"" means the default for the server creation type.',
            "type": "string",
            "default": "",
            "examples": ["./logs/latest.log", "./proxy.log.0"],
            "error": "typeString",
            "fill": True,
        },
        "crashdetection": {
            "title": "Crash detection",
            "type": "boolean",
            "default": False,
            "error": "typeBool",
            "fill": True,
        },
        "autostart": {
            "title": "Autostart",
            "description": "If true, the server will be started"
            + " automatically when Crafty is launched.",
            "type": "boolean",
            "default": False,
            "error": "typeBool",
            "fill": True,
        },
        "autostart_delay": {
            "title": "Autostart delay",
            "description": "Delay in seconds before autostarting. (If enabled)",
            "type": "number",
            "default": 10,
            "minimum": 0,
            "error": "typeIntMinVal0",
            "fill": True,
        },
        "monitoring_type": {
            "title": "Server monitoring type",
            "type": "string",
            "default": "minecraft_java",
            "enum": [
                "minecraft_java",
                "minecraft_bedrock",
                "hytale",
                "steam_cmd",
                "none",
            ],
            "error": "enumErr",
            "fill": True,
            # TODO: SteamCMD, RakNet, etc.
        },
        "minecraft_java_monitoring_data": {
            "title": "Minecraft Java monitoring data",
            "type": "object",
            "required": ["host", "port"],
            "properties": {
                "host": {
                    "title": "Host",
                    "type": "string",
                    "default": "127.0.0.1",
                    "examples": ["127.0.0.1"],
                    "minLength": 1,
                    "error": "typeString",
                    "fill": True,
                },
                "port": {
                    "title": "Port",
                    "type": "integer",
                    "examples": [25565],
                    "default": 25565,
                    "minimum": 0,
                    "error": "typeIntMinVal0",
                    "fill": True,
                },
            },
        },
        "minecraft_bedrock_monitoring_data": {
            "title": "Minecraft Bedrock monitoring data",
            "type": "object",
            "required": ["host", "port"],
            "properties": {
                "host": {
                    "title": "Host",
                    "type": "string",
                    "default": "127.0.0.1",
                    "examples": ["127.0.0.1"],
                    "minLength": 1,
                    "error": "typeString",
                    "fill": True,
                },
                "port": {
                    "title": "Port",
                    "type": "integer",
                    "examples": [19132],
                    "default": 19132,
                    "minimum": 0,
                    "error": "typeIntMinVal0",
                    "fill": True,
                },
            },
        },
        "hytale_monitoring_data": {
            "title": "Hytale monitoring data",
            "type": "object",
            "required": ["host", "port"],
            "properties": {
                "host": {
                    "title": "Host",
                    "type": "string",
                    "default": "127.0.0.1",
                    "examples": ["127.0.0.1"],
                    "minLength": 1,
                    "error": "typeString",
                    "fill": True,
                },
                "port": {
                    "title": "Port",
                    "type": "integer",
                    "examples": [5520],
                    "default": 5520,
                    "minimum": 0,
                    "error": "typeIntMinVal0",
                    "fill": True,
                },
            },
        },
        "create_type": {
            # This is only used for creation, this is not saved in the db
            "title": "Server creation type",
            "type": "string",
            "default": "minecraft_java",
            "enum": [
                "minecraft_java",
                "minecraft_bedrock",
                "hytale",
                "steam_cmd",
                "custom",
            ],
            "error": "enumErr",
            "fill": True,
        },
        "minecraft_java_create_data": {
            "title": "Java creation data",
            "type": "object",
            "required": ["create_type"],
            "properties": {
                "create_type": {
                    "title": "Creation type",
                    "type": "string",
                    "default": "download_jar",
                    "enum": ["download_jar", "import_server"],
                    "error": "enumErr",
                    "fill": True,
                },
                "download_jar_create_data": {
                    "title": "JAR download data",
                    "type": "object",
                    "error": "enumErr",
                    "fill": True,
                    "required": [
                        "type",
                        "version",
                        "mem_min",
                        "mem_max",
                        "server_properties_port",
                        "category",
                    ],
                    "category": {
                        "title": "Jar Category",
                        "type": "string",
                        "examples": ["Mc_java_servers", "Mc_java_proxies"],
                        "error": "enumErr",
                        "fill": True,
                    },
                    "properties": {
                        "type": {
                            "title": "Server JAR Type",
                            "type": "string",
                            "examples": ["Paper"],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "version": {
                            "title": "Server JAR Version",
                            "type": "string",
                            "examples": ["1.18.2"],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "mem_min": {
                            "title": MIN_MEM,
                            "type": "number",
                            "examples": [1],
                            "default": 1,
                            "exclusiveMinimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                        "mem_max": {
                            "title": MAX_MEM,
                            "type": "number",
                            "examples": [2],
                            "default": 2,
                            "exclusiveMinimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                        "server_properties_port": {
                            "title": "Port",
                            "type": "integer",
                            "examples": [25565],
                            "default": 25565,
                            "minimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                        "agree_to_eula": {
                            "title": "Agree to the EULA",
                            "type": "boolean",
                            "default": False,
                            "error": "typeBool",
                            "fill": True,
                        },
                    },
                },
                "import_server_create_data": {
                    "title": "Import server data",
                    "type": "object",
                    "error": "enumErr",
                    "fill": True,
                    "required": [
                        "archive_name",
                        "archive_internal_path",
                        "jarfile",
                        "mem_min",
                        "mem_max",
                        "server_properties_port",
                    ],
                    "properties": {
                        "archive_name": {
                            "title": IMPORT_TITLE,
                            "description": IMPORT_DESCRIPTION,
                            "type": "string",
                            "examples": [ZIP_NAME_EXAMPLE],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "archive_internal_path": {
                            "title": ARCHIVE_PATH_TITLE,
                            "description": ARCHIVE_PATH_DESCRIPTION,
                            "type": "string",
                            "examples": ["", ARCHIVE_PATH_EXAMPLE],
                            "minLength": 0,
                            "error": "typeString",
                            "fill": True,
                        },
                        "jarfile": {
                            "title": "JAR file",
                            "description": "The JAR file relative to the previous path",
                            "type": "string",
                            "examples": ["paper.jar", "jars/vanilla-1.12.jar"],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "mem_min": {
                            "title": MIN_MEM,
                            "type": "number",
                            "examples": [1],
                            "default": 1,
                            "exclusiveMinimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                        "mem_max": {
                            "title": MAX_MEM,
                            "type": "number",
                            "examples": [2],
                            "default": 2,
                            "exclusiveMinimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                        "server_properties_port": {
                            "title": "Port",
                            "type": "integer",
                            "examples": [25565],
                            "default": 25565,
                            "minimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                        "agree_to_eula": {
                            "title": "Agree to the EULA",
                            "type": "boolean",
                            "default": False,
                            "error": "typeBool",
                            "fill": True,
                        },
                    },
                },
            },
            "allOf": [
                {
                    "$comment": "If..then section",
                    "allOf": [
                        {
                            "if": {
                                "properties": {"create_type": {"const": "download_jar"}}
                            },
                            "then": {"required": ["download_jar_create_data"]},
                        },
                        {
                            "if": {
                                "properties": {"create_type": {"const": "import_exec"}}
                            },
                            "then": {"required": ["import_server_create_data"]},
                        },
                    ],
                },
                {
                    "title": "Only one creation data",
                    "oneOf": [
                        {"required": ["download_jar_create_data"]},
                        {"required": ["import_server_create_data"]},
                    ],
                },
            ],
        },
        "minecraft_bedrock_create_data": {
            "title": "Minecraft Bedrock creation data",
            "type": "object",
            "required": ["create_type"],
            "properties": {
                "create_type": {
                    "title": "Creation type",
                    "type": "string",
                    "default": "import_server",
                    "enum": ["download_exe", "import_server"],
                    "error": "enumErr",
                    "fill": True,
                },
                "download_exe_create_data": {
                    "title": "Import server data",
                    "type": "object",
                    "error": "enumErr",
                    "fill": True,
                    "required": [],
                    "properties": {
                        "agree_to_eula": {
                            "title": "Agree to the EULA",
                            "type": "boolean",
                            "enum": [True],
                        },
                    },
                },
                "import_server_create_data": {
                    "title": "Import server data",
                    "type": "object",
                    "error": "enumErr",
                    "fill": True,
                    "required": ["archive_name", "archive_internal_path", "executable"],
                    "properties": {
                        "archive_name": {
                            "title": IMPORT_TITLE,
                            "description": IMPORT_DESCRIPTION,
                            "type": "string",
                            "examples": [ZIP_NAME_EXAMPLE],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "archive_internal_path": {
                            "title": ARCHIVE_PATH_TITLE,
                            "description": ARCHIVE_PATH_DESCRIPTION,
                            "type": "string",
                            "examples": ["", ARCHIVE_PATH_EXAMPLE],
                            "minLength": 0,
                            "error": "typeString",
                            "fill": True,
                        },
                        "executable": {
                            "title": "Executable File",
                            "description": "File Crafty should execute"
                            "on server launch",
                            "type": "string",
                            "examples": ["bedrock_server.exe"],
                            "minlength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "command": {
                            "title": "Command",
                            "type": "string",
                            "default": "echo foo bar baz",
                            "examples": ["LD_LIBRARY_PATH=. ./bedrock_server"],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                    },
                },
            },
            "allOf": [
                {
                    "$comment": "If..then section",
                    "allOf": [
                        {
                            "if": {
                                "properties": {
                                    "create_type": {"const": "import_server"}
                                }
                            },
                            "then": {"required": ["import_server_create_data"]},
                        },
                        {
                            "if": {
                                "properties": {"create_type": {"const": "download_exe"}}
                            },
                            "then": {
                                "required": [
                                    "download_exe_create_data",
                                ]
                            },
                        },
                    ],
                },
                {
                    "title": "Only one creation data",
                    "oneOf": [
                        {"required": ["import_server_create_data"]},
                        {"required": ["download_exe_create_data"]},
                    ],
                },
            ],
        },
        "hytale_create_data": {
            "title": "Hytale creation data",
            "type": "object",
            "required": ["create_type"],
            "properties": {
                "create_type": {
                    "title": "Creation type",
                    "type": "string",
                    "default": "import_server",
                    "enum": ["download_exe", "import_server"],
                    "error": "enumErr",
                    "fill": True,
                },
                "download_exe_create_data": {
                    "title": "Import server data",
                    "type": "object",
                    "error": "enumErr",
                    "fill": True,
                    "required": ["mem_min", "mem_max"],
                    "properties": {
                        "agree_to_eula": {
                            "title": "Agree to the EULA",
                            "type": "boolean",
                            "enum": [True],
                        },
                        "mem_min": {
                            "title": MIN_MEM,
                            "type": "number",
                            "examples": [1],
                            "default": 1,
                            "exclusiveMinimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                        "mem_max": {
                            "title": MAX_MEM,
                            "type": "number",
                            "examples": [2],
                            "default": 2,
                            "exclusiveMinimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                    },
                },
                "import_server_create_data": {
                    "title": "Import server data",
                    "type": "object",
                    "error": "enumErr",
                    "fill": True,
                    "required": [
                        "archive_name",
                        "archive_internal_path",
                        "executable",
                        "mem_min",
                        "mem_max",
                    ],
                    "properties": {
                        "archive_name": {
                            "title": IMPORT_TITLE,
                            "description": IMPORT_DESCRIPTION,
                            "type": "string",
                            "examples": [ZIP_NAME_EXAMPLE],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "archive_internal_path": {
                            "title": ARCHIVE_PATH_TITLE,
                            "description": ARCHIVE_PATH_DESCRIPTION,
                            "type": "string",
                            "examples": ["", ARCHIVE_PATH_EXAMPLE],
                            "minLength": 0,
                            "error": "typeString",
                            "fill": True,
                        },
                        "executable": {
                            "title": "Executable File",
                            "description": "File Crafty should execute"
                            "on server launch",
                            "type": "string",
                            "examples": ["bedrock_server.exe"],
                            "minlength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "command": {
                            "title": "Command",
                            "type": "string",
                            "default": "echo foo bar baz",
                            "examples": ["LD_LIBRARY_PATH=. ./bedrock_server"],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "mem_min": {
                            "title": MIN_MEM,
                            "type": "number",
                            "examples": [1],
                            "default": 1,
                            "exclusiveMinimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                        "mem_max": {
                            "title": MAX_MEM,
                            "type": "number",
                            "examples": [2],
                            "default": 2,
                            "exclusiveMinimum": 0,
                            "error": "typeInteger",
                            "fill": True,
                        },
                    },
                },
            },
            "allOf": [
                {
                    "$comment": "If..then section",
                    "allOf": [
                        {
                            "if": {
                                "properties": {
                                    "create_type": {"const": "import_server"}
                                }
                            },
                            "then": {"required": ["import_server_create_data"]},
                        },
                        {
                            "if": {
                                "properties": {"create_type": {"const": "download_exe"}}
                            },
                            "then": {
                                "required": [
                                    "download_exe_create_data",
                                ]
                            },
                        },
                    ],
                },
                {
                    "title": "Only one creation data",
                    "oneOf": [
                        {"required": ["import_server_create_data"]},
                        {"required": ["download_exe_create_data"]},
                    ],
                },
            ],
        },
        "steam_cmd_create_data": {
            "title": "SteamCMD creation data",
            "type": "object",
            "required": ["create_type"],
            "properties": {
                "create_type": {
                    "title": "Creation type",
                    "type": "string",
                    "default": "download_exe",
                    "enum": ["download_exe"],
                },
                "download_exe_create_data": {
                    "title": "Import server data",
                    "type": "object",
                    "required": [],
                    "properties": {
                        "app_id": {
                            "title": "Steam Server App ID",
                            "type": "integer",
                        },
                        "agree_to_eula": {
                            "title": "Agree to the EULA",
                            "type": "boolean",
                            "enum": [True],
                        },
                    },
                },
            },
            "allOf": [
                {
                    "$comment": "If..then section",
                    "allOf": [
                        {
                            "if": {
                                "properties": {"create_type": {"const": "download_exe"}}
                            },
                            "then": {
                                "required": [
                                    "download_exe_create_data",
                                ]
                            },
                        },
                    ],
                },
                {
                    "title": "Only one creation data",
                    "oneOf": [
                        {"required": ["download_exe_create_data"]},
                    ],
                },
            ],
        },
        "custom_create_data": {
            "title": "Custom creation data",
            "type": "object",
            "error": "enumErr",
            "fill": True,
            "required": [
                "working_directory",
                "executable_update",
                "create_type",
            ],
            "properties": {
                "working_directory": {
                    "title": "Working directory",
                    "description": '"" means the default',
                    "type": "string",
                    "default": "",
                    "examples": ["/mnt/mydrive/server-configs/", "./subdirectory", ""],
                    "error": "typeString",
                    "fill": True,
                },
                "executable_update": {
                    "title": "Executable Updation",
                    "description": "Also configurable later on and for other servers",
                    "type": "object",
                    "error": "enumErr",
                    "fill": True,
                    "required": ["enabled", "file", "url"],
                    "properties": {
                        "enabled": {
                            "title": "Enabled",
                            "type": "boolean",
                            "default": False,
                            "error": "typeBool",
                            "fill": True,
                        },
                        "file": {
                            "title": "Executable to update",
                            "type": "string",
                            "default": "",
                            "examples": ["./paper.jar"],
                            "error": "typeString",
                            "fill": True,
                        },
                        "url": {
                            "title": "URL to download the executable from",
                            "type": "string",
                            "default": "",
                            "error": "typeString",
                            "fill": True,
                        },
                    },
                },
                "create_type": {
                    "title": "Creation type",
                    "type": "string",
                    "default": "raw_exec",
                    "enum": ["raw_exec", "import_server"],
                    "error": "enumErr",
                    "fill": True,
                },
                "raw_exec_create_data": {
                    "title": "Raw execution command create data",
                    "type": "object",
                    "required": ["command"],
                    "properties": {
                        "command": {
                            "title": "Command",
                            "type": "string",
                            "default": "echo foo bar baz",
                            "examples": ["caddy start"],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        }
                    },
                },
                "import_server_create_data": {
                    "title": "Import server data",
                    "type": "object",
                    "error": "enumErr",
                    "fill": True,
                    "required": ["archive_name", "archive_internal_path", "command"],
                    "properties": {
                        "archive_name": {
                            "title": IMPORT_TITLE,
                            "description": IMPORT_DESCRIPTION,
                            "type": "string",
                            "examples": [ZIP_NAME_EXAMPLE],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                        "archive_internal_path": {
                            "title": ARCHIVE_PATH_TITLE,
                            "description": ARCHIVE_PATH_DESCRIPTION,
                            "type": "string",
                            "examples": ["", ARCHIVE_PATH_EXAMPLE],
                            "minLength": 0,
                            "error": "typeString",
                            "fill": True,
                        },
                        "command": {
                            "title": "Command",
                            "type": "string",
                            "default": "echo foo bar baz",
                            "examples": ["caddy start"],
                            "minLength": 1,
                            "error": "typeString",
                            "fill": True,
                        },
                    },
                },
            },
            "allOf": [
                {
                    "$comment": "If..then section",
                    "allOf": [
                        {
                            "if": {
                                "properties": {"create_type": {"const": "raw_exec"}}
                            },
                            "then": {"required": ["raw_exec_create_data"]},
                        },
                        {
                            "if": {
                                "properties": {
                                    "create_type": {"const": "import_server"}
                                }
                            },
                            "then": {"required": ["import_server_create_data"]},
                        },
                    ],
                },
                {
                    "title": "Only one creation data",
                    "oneOf": [
                        {"required": ["raw_exec_create_data"]},
                        {"required": ["import_server_create_data"]},
                    ],
                },
            ],
        },
    },
    "allOf": [
        {
            "$comment": "If..then section",
            "allOf": [
                # start require creation data
                {
                    "if": {"properties": {"create_type": {"const": "minecraft_java"}}},
                    "then": {"required": ["minecraft_java_create_data"]},
                },
                {
                    "if": {
                        "properties": {"create_type": {"const": "minecraft_bedrock"}}
                    },
                    "then": {"required": ["minecraft_bedrock_create_data"]},
                },
                {
                    "if": {"properties": {"create_type": {"const": "steam_cmd"}}},
                    "then": {"required": ["steam_cmd_create_data"]},
                },
                {
                    "if": {"properties": {"create_type": {"const": "hytale"}}},
                    "then": {"required": ["hytale_create_data"]},
                },
                {
                    "if": {"properties": {"create_type": {"const": "custom"}}},
                    "then": {"required": ["custom_create_data"]},
                },
                # end require creation data
                # start require monitoring data
                {
                    "if": {
                        "properties": {"monitoring_type": {"const": "minecraft_java"}}
                    },
                    "then": {"required": ["minecraft_java_monitoring_data"]},
                },
                {
                    "if": {
                        "properties": {
                            "monitoring_type": {"const": "minecraft_bedrock"}
                        }
                    },
                    "then": {"required": ["minecraft_bedrock_monitoring_data"]},
                },
                {
                    "if": {"properties": {"monitoring_type": {"const": "hytale"}}},
                    "then": {"required": ["hytale_monitoring_data"]},
                },
                # end require monitoring data
            ],
        },
        {
            "title": "Only one creation data",
            "oneOf": [
                {"required": ["minecraft_java_create_data"]},
                {"required": ["minecraft_bedrock_create_data"]},
                {"required": ["steam_cmd_create_data"]},
                {"required": ["hytale_create_data"]},
                {"required": ["custom_create_data"]},
            ],
        },
        {
            "title": "Only one monitoring data",
            "oneOf": [
                {"required": ["minecraft_java_monitoring_data"]},
                {"required": ["minecraft_bedrock_monitoring_data"]},
                {"required": ["steam_cmd_monitoring_data"]},
                {"required": ["hytale_monitoring_data"]},
                {"properties": {"monitoring_type": {"const": "none"}}},
            ],
        },
    ],
}


class ApiServersIndexHandler(BaseApiHandler):
    def get(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        # TODO: limit some columns for specific permissions

        self.finish_json(200, {"status": "ok", "data": auth_data[0]})

    def post(self):
        auth_data = self.authenticate_user()
        if not auth_data:
            return
        (
            _,
            exec_user_crafty_permissions,
            _,
            _superuser,
            user,
            _,
        ) = auth_data

        if EnumPermissionsCrafty.SERVER_CREATION not in exec_user_crafty_permissions:
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
            validate(data, new_server_schema)
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
        # Check to make sure port is allowable
        if data["monitoring_type"] == "minecraft_java":
            try:
                port = data["minecraft_java_monitoring_data"]["port"]
            except:
                port = 25565
        else:
            try:
                port = data["minecraft_bedrock_monitoring_data"]["port"]
            except:
                port = 19132
        if port > 65535 or port < 1:
            self.finish_json(
                405,
                {
                    "status": "error",
                    "error": "DATA CONSTRAINT FAILED",
                    "error_data": "1 - 65535",
                },
            )
            return
        try:
            new_server_id = self.controller.create_api_server(data, user["user_id"])
        except Exception as e:
            self.controller.servers.stats.record_stats()

            return self.finish_json(
                503,
                {
                    "status": "error",
                    "error": "Could not create server",
                    "error_data": str(e),
                },
            )

        self.controller.servers.stats.record_stats()

        self.controller.management.add_to_audit_log(
            user["user_id"],
            (
                f"created server {data['name']}"
                f" (ID: {new_server_id})"
                f" (UUID: {new_server_id})"
            ),
            server_id=new_server_id,
            source_ip=self.get_remote_ip(),
        )

        self.finish_json(
            201,
            {
                "status": "ok",
                "data": {
                    "new_server_id": str(new_server_id),
                    "new_server_uuid": new_server_id,
                },
            },
        )
