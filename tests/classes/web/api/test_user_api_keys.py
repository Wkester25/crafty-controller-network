"""Cover the session-only rule on the API key management routes.

The handler runs against real ``Users`` and ``ApiKeys`` rows in an in-memory
database, and authenticates a real token for each request. A change that lets a
key manage keys therefore fails here instead of passing against a mock.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock as Mock

import pytest
from peewee import SqliteDatabase

from app.classes.models.roles import Roles
from app.classes.models.users import ApiKeys, HelperUsers, UserRoles, Users
from app.classes.shared.authentication import Authentication
from app.classes.shared.translation import Translation
from app.classes.web.routes.api.users.user.api import ApiUsersUserKeyHandler

ROOT_DIR = Path(__file__).resolve().parents[4]
MODELS = [Users, ApiKeys, UserRoles, Roles]
SECRET = "test-secret"

NEW_KEY = {
    "name": "escalated",
    "server_permissions_mask": "11111111",
    "crafty_permissions_mask": "111",
    "full_access": True,
}


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


@pytest.fixture(name="admin")
def fixture_admin():
    """Bind the user tables to a fresh database holding one superuser."""
    database = SqliteDatabase(":memory:")
    with database.bind_ctx(MODELS, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(MODELS)
        yield Users.insert(
            {
                Users.username: "admin",
                Users.superuser: True,
                # Tokens are only valid if issued after this, so date it in the past.
                Users.valid_tokens_from: datetime.now(timezone.utc) - timedelta(days=1),
            }
        ).execute()
        database.close()


def add_key(user_id, name, full_access=False) -> ApiKeys:
    """Insert an API key for ``user_id`` and return the stored row."""
    key_id = HelperUsers.add_user_api_key(name, user_id, full_access, "11111111", "111")
    return HelperUsers.get_user_api_key(key_id)


def build_handler(user_id, api_key=None):
    """Build a key handler that carries a token for ``api_key``, or a session token.

    Args:
        user_id (int): The account the token authenticates as.
        api_key (Optional[ApiKeys]): The key the token names, None for a session.

    Returns:
        tuple: The handler and the Response that captures its answer.
    """
    authentication = Authentication.__new__(Authentication)
    authentication.secret = SECRET
    token = authentication.generate(
        user_id, {"token_id": api_key.token_id} if api_key else {}
    )

    handler = ApiUsersUserKeyHandler.__new__(ApiUsersUserKeyHandler)
    handler.request = Mock()
    handler.request.path = f"/api/v2/users/{user_id}/api/"
    handler.request.body = json.dumps(NEW_KEY).encode()
    handler.request.headers = {"Authorization": f"Bearer {token}"}
    handler.get_query_argument = Mock(return_value=None)
    handler.get_remote_ip = Mock(return_value="127.0.0.1")
    handler.controller = Mock()
    handler.controller.users = HelperUsers
    handler.controller.authentication = authentication
    handler.helper = Mock()
    handler.helper.root_dir = ROOT_DIR
    handler.helper.get_setting.return_value = False  # No forced MFA.
    handler.helper.translation = Translation(handler.helper)
    response = Response()
    handler.finish_json = response
    return handler, response


def test_api_key_cannot_create_a_key(admin) -> None:
    key = add_key(admin, "narrow")
    handler, response = build_handler(admin, key)

    handler.patch(str(admin))

    assert response.refused
    assert ApiKeys.select().count() == 1


def test_full_access_api_key_cannot_create_a_key(admin) -> None:
    key = add_key(admin, "wide", full_access=True)
    handler, response = build_handler(admin, key)

    handler.patch(str(admin))

    assert response.refused
    assert ApiKeys.select().where(ApiKeys.name == NEW_KEY["name"]).count() == 0


def test_session_can_create_a_key(admin) -> None:
    handler, response = build_handler(admin)

    handler.patch(str(admin))

    assert response.status == 200
    created = HelperUsers.get_user_api_key(response.body["data"]["id"])
    assert created.name == NEW_KEY["name"]
    assert created.full_access is True


def test_api_key_cannot_read_a_key_token(admin) -> None:
    key = add_key(admin, "narrow")
    handler, response = build_handler(admin, key)

    handler.get(str(admin), str(key.token_id))

    assert response.refused
    assert "data" not in response.body


def test_api_key_cannot_delete_a_key(admin) -> None:
    key = add_key(admin, "narrow")
    handler, response = build_handler(admin, key)

    handler.delete(str(admin), str(key.token_id))

    assert response.refused
    assert HelperUsers.get_user_api_key(key.token_id).name == "narrow"


def test_session_can_delete_a_key(admin) -> None:
    key = add_key(admin, "narrow")
    handler, response = build_handler(admin)

    handler.delete(str(admin), str(key.token_id))

    assert response.status == 200
    assert ApiKeys.select().where(ApiKeys.token_id == key.token_id).count() == 0
