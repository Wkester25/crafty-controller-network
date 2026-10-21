import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable, Optional
import aiofiles
import orjson
from jsonschema import ValidationError, validate
from tornado.iostream import StreamClosedError
from app.classes.models.crafty_permissions import EnumPermissionsCrafty
from app.classes.web.base_handler import BaseHandler

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=4)


class BaseApiHandler(BaseHandler):
    # {{{ Disable XSRF protection on API routes
    def check_xsrf_cookie(self) -> None:
        pass

    # }}}

    # {{{ 405 Method Not Allowed as JSON
    def _unimplemented_method(self, *_args: str, **_kwargs: str) -> None:
        self.finish_json(
            405,
            {
                "status": "error",
                "error": "METHOD_NOT_ALLOWED",
                "error_data": "METHOD NOT ALLOWED",
            },
        )

    head = _unimplemented_method  # type: Callable[..., Optional[Awaitable[None]]]
    get = _unimplemented_method  # type: Callable[..., Optional[Awaitable[None]]]
    post = _unimplemented_method  # type: Callable[..., Optional[Awaitable[None]]]
    delete = _unimplemented_method  # type: Callable[..., Optional[Awaitable[None]]]
    patch = _unimplemented_method  # type: Callable[..., Optional[Awaitable[None]]]
    put = _unimplemented_method  # type: Callable[..., Optional[Awaitable[None]]]
    # }}}

    def options(self, *_, **__):
        """
        Fix CORS
        """
        # no body
        self.set_status(204)
        self.finish()

    async def download_file(self, file_path: Path):
        """THIS METHOD HAS NO TRAVERSAL DETECTION OR PERMISSION CHECKS! MAKE SURE TO
        CHECK FOR TRAVERSAL AND PERMISSION BEFORE CALLING THIS FUNCTION.

        Downloads file async in chunks

        Args:
            file_path (Path): pathlib Path object pointing to the intended file to
            download

        Returns:
            _type_: _description_
        """
        chunk_size = 4 * 1024 * 1024  # 4 MiB
        try:
            self.set_header("Content-Type", "application/octet-stream")
            self.set_header(
                "Content-Disposition", f'attachment; filename="{file_path.name}"'
            )

            async with aiofiles.open(file_path, "rb") as f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk:
                        break
                    try:
                        self.write(chunk)
                        await self.flush()
                    except StreamClosedError:
                        break
                    finally:
                        del chunk

        except Exception as e:
            print("Download error:", e)
            return self.finish_json(
                500,
                {
                    "status": "error",
                    "error": "Download error",
                    "error_data": f"ERROR: {e}",
                },
            )

    def can_modify_user(
        self, exec_user_crafty_permissions: list, auth_data: tuple, user_id: int | str
    ) -> bool:
        """Checks if exec user has permissions to modify target user

        Args:
            exec_user_crafty_permissions (list): Permissions of exec user returned
            from auth check
            auth_data (dict): Authenticated user information from auth check
            user_id (int): Target user ID

        Returns:
            bool: Returns true if user can edit target user
        """
        if int(auth_data[4]["user_id"]) == int(user_id) or str(user_id) == "@me":
            return True
        if self.is_superuser(auth_data):
            return True
        if EnumPermissionsCrafty.USER_CONFIG in exec_user_crafty_permissions and int(
            user_id
        ) in self.controller.users.get_managed_users_ids(auth_data[4]["user_id"]):
            return True
        return False

    def require_superuser(self, auth_data: tuple) -> bool:
        """Guard an endpoint behind superuser access.

        Writes a 403 ``NOT_AUTHORIZED`` response and returns ``False`` when the
        caller does not have superuser authority; returns ``True`` otherwise.

        Args:
            auth_data (tuple): Authenticated user information from the auth check.

        Returns:
            bool: ``True`` if the caller is a superuser, else ``False``.
        """
        if self.is_superuser(auth_data):
            return True
        self.finish_json(
            403,
            {
                "status": "error",
                "error": "NOT_AUTHORIZED",
                "error_data": self.helper.translation.translate(
                    "validators", "insufficientPerms", auth_data[4]["lang"]
                ),
            },
        )
        return False

    def require_session_auth(self, auth_data: tuple) -> bool:
        """Guard an endpoint against being reached with an API key.

        An API key carries a fixed permission set, so a key that can manage keys
        is not limited by that set: it mints a second key on its own account with
        any mask it likes and reads back the bearer token. Credential management
        is therefore restricted to an interactive session. Writes a 403
        ``NOT_AUTHORIZED`` response and returns ``False`` when the request
        authenticated with a key; returns ``True`` otherwise.

        Args:
            auth_data (tuple): Authenticated user information from the auth check.

        Returns:
            bool: ``True`` if the caller is a logged in user, else ``False``.
        """
        if self.api_key is None:
            return True
        self.finish_json(
            403,
            {
                "status": "error",
                "error": "NOT_AUTHORIZED",
                "error_data": self.helper.translation.translate(
                    "validators", "sessionOnly", auth_data[4]["lang"]
                ),
            },
        )
        return False

    def load_and_validate(self, schema: dict):
        """Parse the JSON request body and validate it against ``schema``.

        On failure, writes the appropriate 400 response (``INVALID_JSON`` for a
        malformed body, ``INVALID_JSON_SCHEMA`` for a schema mismatch) and returns
        ``None``. A successful parse always yields the decoded object, so callers
        can treat ``None`` as "response already sent, return now".

        Args:
            schema (dict): JSON schema to validate the request body against.

        Returns:
            Optional[object]: The validated request body, or ``None`` on failure.
        """
        try:
            data = orjson.loads(self.request.body)
        except orjson.JSONDecodeError as e:
            self.finish_json(
                400,
                {"status": "error", "error": "INVALID_JSON", "error_data": str(e)},
            )
            return None
        try:
            validate(data, schema)
        except ValidationError as e:
            self.finish_json(
                400,
                {
                    "status": "error",
                    "error": "INVALID_JSON_SCHEMA",
                    "error_data": str(e),
                },
            )
            return None
        return data

    def reject_traversal(self, base_dir: str, target_path: str) -> bool:
        """Guard a filesystem path against directory traversal.

        Writes a 403 ``TRAVERSAL DETECTED`` response and returns ``False`` when
        ``target_path`` escapes ``base_dir``; returns ``True`` when the path is safe.

        Args:
            base_dir (str): Absolute path of the directory the target must stay within.
            target_path (str): Absolute path being accessed.

        Returns:
            bool: ``True`` if the path is safe, else ``False``.
        """
        if self.helper.is_path_within(base_dir, target_path):
            return True
        self.finish_json(
            403,
            {
                "status": "error",
                "error": "TRAVERSAL DETECTED",
                "error_data": "TRIED TO REACH FILES THAT ARE"
                " NOT SUPPOSED TO BE ACCESSIBLE",
            },
        )
        return False
