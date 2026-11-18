import html
import logging
import pathlib
from app.classes.helpers.helpers import Helpers
from app.classes.models.server_permissions import EnumPermissionsServer
from app.classes.shared.server import ServerOutBuf
from app.classes.web.base_api_handler import BaseApiHandler

logger = logging.getLogger(__name__)


class ApiServersServerLogsHandler(BaseApiHandler):
    def validate_read_perms(self, auth_data, server_id, read_log_file):
        mask = self.controller.server_perms.get_lowest_api_perm_mask(
            self.controller.server_perms.get_user_permissions_mask(
                auth_data[4]["user_id"], server_id
            ),
            auth_data[5],
        )
        server_permissions = self.controller.server_perms.get_permissions(mask)

        # does user have access to the server?
        # does user have terminal perms?
        # does user have log permissions?
        # is user reading the log file?
        match (
            server_id in [str(x["server_id"]) for x in auth_data[0]],
            EnumPermissionsServer.TERMINAL in server_permissions,
            EnumPermissionsServer.LOGS in server_permissions,
            read_log_file,
        ):
            # allow terminal buffer access
            case (True, True, _, False):
                return True
            # allow log file access
            case (True, _, True, True):
                return True
            # fail-shut
            case _:
                return False

    def render_line(self, line, colored_output, disable_ansi_strip):
        """Turn one raw log line into what the caller asked for.

        :param line: The line as it was read, escape sequences included.
        :param colored_output: Whether the caller wants Crafty's highlighting.
        :param disable_ansi_strip: Whether to leave escape sequences in place.
        :return: The line, ready to send.
        """
        if disable_ansi_strip:
            # Hand back what the server wrote, for clients rendering it
            # themselves.
            return self.helper.log_colors(line) if colored_output else line

        if colored_output:
            # The renderer escapes, so it can tell text from its own markup.
            return self.helper.console_line_to_html(line)

        return html.escape(Helpers.strip_console_control(line))

    def get(self, server_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        # GET /api/v2/servers/server/logs?file=true
        read_log_file = self.get_query_argument("file", None) == "true"
        # GET /api/v2/servers/server/logs?colors=true
        colored_output = self.get_query_argument("colors", None) == "true"
        # GET /api/v2/servers/server/logs?raw=true
        disable_ansi_strip = self.get_query_argument("raw", None) == "true"
        # GET /api/v2/servers/server/logs?html=true
        use_html = self.get_query_argument("html", None) == "true"
        # GET /api/v2/servers/server/logs?file=true&log_file=2026-03-05-1.log.gz
        # GET /api/v2/servers/server/logs?file=true&log_file=a.log.gz,b.log.gz
        # Selects one or more specific (typically rotated/archived) log files
        # instead of the server's currently active log - e.g. every rotation
        # from one day, so they can be viewed combined. Ignored unless
        # file=true.
        requested_log_file = self.get_query_argument("log_file", None)

        if not self.validate_read_perms(auth_data, server_id, read_log_file):
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
        server_data = self.controller.servers.get_server_data_by_id(server_id)

        if read_log_file:
            log_lines = self.helper.get_setting("max_log_lines")
            # If the log path is absolute it returns it as is
            # If it is relative it joins the paths below like normal.
            # resolve_log_path expands a glob (e.g. Hytale's per-boot files)
            # to the newest matching file; a literal path is returned as-is.
            active_log_path = self.helper.resolve_log_path(
                pathlib.Path(server_data["path"], server_data["log_path"])
            )

            if requested_log_file:
                # One or more specific (usually older/rotated) log files were
                # requested - confine each to the active log's directory.
                requested_names = [
                    name.strip()
                    for name in requested_log_file.split(",")
                    if name.strip()
                ]
                try:
                    log_paths = [
                        Helpers.validate_traversal(active_log_path.parent, name)
                        for name in requested_names
                    ]
                except ValueError:
                    return self.finish_json(
                        403,
                        {
                            "status": "error",
                            "error": "TRAVERSAL DETECTED",
                            "error_data": "TRAVERSAL DETECTED",
                        },
                    )
                raw_lines = self.file_helper.tail_files(log_paths, log_lines)
            else:
                raw_lines = self.helper.tail_file(active_log_path, log_lines)

            # Remove newline characters from the end of the lines
            raw_lines = [line.rstrip("\r\n") for line in raw_lines]
        else:
            raw_lines = ServerOutBuf.lines.get(server_id, [])

        lines = []

        for line in raw_lines:
            try:
                lines.append(self.render_line(line, colored_output, disable_ansi_strip))
            except Exception as e:
                logger.warning(f"Skipping Log Line due to error: {e}")

        if use_html:
            for line in lines:
                line = f"{line}<br />"

        self.finish_json(200, {"status": "ok", "data": lines})


class ApiServersServerLogFilesHandler(ApiServersServerLogsHandler):
    """Lists the log files (current + rotated/archived) available for a
    server, grouped by calendar date so a day with several rotations can be
    offered - and later read - as a single combined log."""

    def get(self, server_id: str):
        auth_data = self.authenticate_user()
        if not auth_data:
            return

        # Listing log files requires the same LOGS permission as reading one.
        if not self.validate_read_perms(auth_data, server_id, True):
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

        server_data = self.controller.servers.get_server_data_by_id(server_id)
        active_log_path = self.helper.resolve_log_path(
            pathlib.Path(server_data["path"], server_data["log_path"])
        )

        files = self.file_helper.list_log_files(active_log_path.parent)
        for entry in files:
            entry["active"] = entry["name"] == active_log_path.name

        groups = self.file_helper.group_log_files_by_date(files)

        self.finish_json(200, {"status": "ok", "data": groups})
