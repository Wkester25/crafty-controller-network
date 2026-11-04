"""Cover the API key limits on the password, TOTP and passkey routes.

A key that sets its own account's password, TOTP or passkey then signs in as
that account, so those changes need a session. Changing them on another account
is user management, which a User Config key may do for the users it manages.
The handlers authenticate a real token against real ``Users`` and ``ApiKeys``
rows in an in-memory database.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock as Mock

import pytest
from peewee import SqliteDatabase

from app.classes.models.crafty_permissions import PermissionsCrafty
from app.classes.models.roles import Roles
from app.classes.models.users import ApiKeys, HelperUsers, UserRoles, Users
from app.classes.shared.authentication import Authentication
from app.classes.shared.translation import Translation
from app.classes.web.routes.api.users.user.index import ApiUsersUserIndexHandler
from app.classes.web.routes.api.users.user.otp import (
    APIUsersTOTPHandler,
    APIUsersTOTPIndexHandler,
    APIUsersTOTPRecovery,
    APIUsersTOTPVerifyIndexHandler,
)
from app.classes.web.routes.api.users.user.passkey import (
    ApiUsersPasskeyHandler,
    ApiUsersPasskeyIndexHandler,
    ApiUsersPasskeyVerifyHandler,
)

ROOT_DIR = Path(__file__).resolve().parents[4]
MODELS = [Users, ApiKeys, UserRoles, Roles]
SECRET = "test-secret"
OLD_PASSWORD = "old-password-hash"

# Crafty masks are SERVER_CREATION, USER_CONFIG, ROLES_CONFIG.
NO_PERMS = "000"
USER_CONFIG = "010"

CREDENTIAL_ROUTES = [
    (APIUsersTOTPIndexHandler, "post", ("@me",)),
    (APIUsersTOTPIndexHandler, "get", ("@me",)),
    (APIUsersTOTPVerifyIndexHandler, "post", ("@me", "1")),
    (APIUsersTOTPHandler, "get", ("@me",)),
    (APIUsersTOTPHandler, "delete", ("@me", "1")),
    (APIUsersTOTPRecovery, "get", ("@me",)),
    (ApiUsersPasskeyIndexHandler, "get", ("@me",)),
    (ApiUsersPasskeyIndexHandler, "post", ("@me",)),
    (ApiUsersPasskeyVerifyHandler, "post", ("@me", "challenge")),
    (ApiUsersPasskeyHandler, "delete", ("@me", "1")),
]


class Response:
    """Record the status and body the handler finishes with."""

    def __init__(self):
        self.status = None
        self.body = None

    def __call__(self, status, body):
        self.status = status
        self.body = body

    @property
    def refused(self) -> bool:
        """Report whether the handler answered with a 403 NOT_AUTHORIZED."""
        return self.status == 403 and self.body["error"] == "NOT_AUTHORIZED"


def add_user(username, superuser=False, manager=None) -> int:
    """Insert a user whose tokens are valid now and return its ID."""
    return Users.insert(
        {
            Users.username: username,
            Users.superuser: superuser,
            Users.manager: manager,
            Users.password: OLD_PASSWORD,
            # Tokens are only valid if issued after this, so date it in the past.
            Users.valid_tokens_from: datetime.now(timezone.utc) - timedelta(days=1),
        }
    ).execute()


@pytest.fixture(name="admin")
def fixture_admin():
    """Bind the user tables to a fresh database holding one superuser."""
    database = SqliteDatabase(":memory:")
    with database.bind_ctx(MODELS, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(MODELS)
        yield add_user("admin", superuser=True)
        database.close()


def add_key(user_id, crafty_mask=USER_CONFIG, full_access=False) -> ApiKeys:
    """Insert an API key for ``user_id`` and return the stored row."""
    key_id = HelperUsers.add_user_api_key(
        "key", user_id, full_access, "11111111", crafty_mask
    )
    return HelperUsers.get_user_api_key(key_id)


def build_handler(handler_class, user_id, api_key=None, body=None):
    """Build a handler that carries a token for ``api_key``, or a session token.

    Args:
        handler_class (type): The route handler to build.
        user_id (int): The account the token authenticates as.
        api_key (Optional[ApiKeys]): The key the token names, None for a session.
        body (Optional[dict]): The JSON request body.

    Returns:
        tuple: The handler and the Response that captures its answer.
    """
    authentication = Authentication.__new__(Authentication)
    authentication.secret = SECRET
    token = authentication.generate(
        user_id, {"token_id": api_key.token_id} if api_key else {}
    )

    handler = handler_class.__new__(handler_class)
    handler.api_key = None
    handler.request = Mock()
    handler.request.path = f"/api/v2/users/{user_id}/"
    handler.request.body = json.dumps(body or {}).encode()
    handler.request.headers = {"Authorization": f"Bearer {token}"}
    handler.get_query_argument = Mock(return_value=None)
    handler.get_remote_ip = Mock(return_value="127.0.0.1")
    handler.controller = Mock()
    handler.controller.users = Mock(wraps=HelperUsers)
    handler.controller.users.user_jsonschema_props = {
        "password": {"type": "string"},
        "theme": {"type": "string"},
        "disable_password_auth": {"type": "boolean"},
    }
    handler.controller.authentication = authentication
    handler.controller.crafty_perms.get_api_key_permissions_list = (
        PermissionsCrafty.get_api_key_permissions_list
    )
    handler.helper = Mock()
    handler.helper.root_dir = ROOT_DIR
    handler.helper.get_setting.return_value = False  # No forced MFA.
    handler.helper.translation = Translation(handler.helper)
    response = Response()
    handler.finish_json = response
    return handler, response


@pytest.mark.parametrize("full_access", [False, True])
@pytest.mark.parametrize(("handler_class", "method", "args"), CREDENTIAL_ROUTES)
def test_api_key_cannot_manage_mfa_or_passkeys(
    admin, handler_class, method, args, full_access
) -> None:
    key = add_key(admin, full_access=full_access)
    handler, response = build_handler(handler_class, admin, key, {"name": "x"})

    getattr(handler, method)(*args)

    assert response.refused
    assert not handler.controller.totp.method_calls
    assert not handler.controller.passkey.method_calls


@pytest.mark.parametrize(
    ("handler_class", "method", "called"),
    [
        (APIUsersTOTPIndexHandler, "post", "totp.create_user_totp"),
        (ApiUsersPasskeyIndexHandler, "post", "passkey.generate_registration_options"),
    ],
)
@pytest.mark.parametrize(("target", "allowed"), [("managed", True), ("other", False)])
def test_user_config_key_resets_only_managed_mfa(
    admin, handler_class, method, called, target, allowed
) -> None:
    targets = {
        "managed": add_user("managed", manager=admin),
        "other": add_user("other"),
    }
    key = add_key(admin, USER_CONFIG)
    handler, response = build_handler(handler_class, admin, key)
    handler.controller.users.get_user_by_id = HelperUsers.get_user

    getattr(handler, method)(str(targets[target]))

    controller_name, method_name = called.split(".")
    reached = getattr(getattr(handler.controller, controller_name), method_name)
    assert response.refused is not allowed
    assert reached.called is allowed


def test_session_can_add_totp(admin) -> None:
    handler, response = build_handler(APIUsersTOTPIndexHandler, admin)

    handler.post("@me")

    assert response.status == 200
    handler.controller.totp.create_user_totp.assert_called_once_with(admin)


@pytest.mark.parametrize(
    "change", [{"password": "new-password"}, {"disable_password_auth": True}]
)
@pytest.mark.parametrize("full_access", [False, True])
def test_api_key_cannot_change_own_sign_in(admin, full_access, change) -> None:
    key = add_key(admin, full_access=full_access)
    handler, response = build_handler(ApiUsersUserIndexHandler, admin, key, change)

    handler.patch("@me")

    assert response.refused
    stored = Users.get_by_id(admin)
    assert stored.password == OLD_PASSWORD
    assert stored.disable_password_auth is False


def test_user_config_key_can_reset_a_managed_password(admin) -> None:
    managed = add_user("managed", manager=admin)
    key = add_key(admin, USER_CONFIG)
    handler, response = build_handler(
        ApiUsersUserIndexHandler, admin, key, {"password": "new-password"}
    )
    handler.controller.users.update_user = Mock()
    handler.helper.get_setting.side_effect = lambda key, default=False: default

    handler.patch(str(managed))

    assert response.status == 200
    handler.controller.users.update_user.assert_called_once()
    assert handler.controller.users.update_user.call_args.args[0] == str(managed)


def test_api_key_cannot_edit_an_unmanaged_user(admin) -> None:
    other = add_user("other")
    key = add_key(admin, USER_CONFIG)
    handler, response = build_handler(
        ApiUsersUserIndexHandler, admin, key, {"theme": "dark"}
    )

    handler.patch(str(other))

    assert response.refused
    assert Users.get_by_id(other).theme == "default"


@pytest.mark.parametrize(
    ("crafty_mask", "target", "allowed"),
    [
        (NO_PERMS, "self", False),
        (USER_CONFIG, "self", True),
        (USER_CONFIG, "managed", True),
        (USER_CONFIG, "unmanaged", False),
    ],
)
def test_can_modify_user_with_api_key(admin, crafty_mask, target, allowed) -> None:
    targets = {
        "self": admin,
        "managed": add_user("managed", manager=admin),
        "unmanaged": add_user("unmanaged"),
    }
    key = add_key(admin, crafty_mask)
    handler, _ = build_handler(ApiUsersUserIndexHandler, admin, key)
    auth_data = handler.authenticate_user()

    assert handler.can_modify_user(auth_data[1], auth_data, targets[target]) is allowed


def test_session_can_modify_self(admin) -> None:
    handler, _ = build_handler(ApiUsersUserIndexHandler, admin)
    auth_data = handler.authenticate_user()

    assert handler.can_modify_user(auth_data[1], auth_data, "@me") is True
