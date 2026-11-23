"""Keep one source of truth for "is this caller a superuser".

The account flag and the credential are different questions. A superuser who
issues an API key without full access has made a credential that is not a
superuser, and only BaseHandler.effective_superuser knows that. Reading the
account flag out of a user record instead is how a narrowed key ends up with
the run of the panel, so every new occurrence has to be justified here.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[3] / "app" / "classes" / "web"

# A read of the superuser column off a user record, in any of its spellings.
ACCOUNT_FLAG = re.compile(r'\b\w*user\w*\["superuser"\]|auth_data\[4\]\["superuser"\]')

# Every occurrence that is deliberately not an authorization decision, with the
# reason it is allowed to read the account flag. Anything else is a bug: use
# self.is_superuser(auth_data), self.require_superuser(auth_data), or
# BaseHandler.effective_superuser.
ALLOWED = {
    # The one place the flag is combined with the key.
    ("base_handler.py", 'if not user["superuser"]:'),
    # Whether the account must carry MFA, which is a property of the account.
    (
        "base_handler.py",
        'return ((user["superuser"] and su_mfa) or role_mfa) and not token_data.get(',
    ),
    # Narrowing here would hide every server from a superuser's non-full-access
    # key, because server_id_authorized is role based. See check_server_id.
    ("panel_handler.py", 'superuser = exec_user["superuser"]'),
    # A fact about the user being edited, not about the caller.
    ("panel_handler.py", 'if target_user["superuser"]:'),
}


def find_account_flag_reads() -> set:
    """Collect every read of the superuser column in the web layer."""
    found = set()
    for path in sorted(WEB.rglob("*.py")):
        in_docstring = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.count('"""') == 1:
                in_docstring = not in_docstring
                continue
            if in_docstring or stripped.startswith("#"):
                continue
            if ACCOUNT_FLAG.search(stripped):
                found.add((path.name, stripped))
    return found


def test_no_unreviewed_superuser_account_flag_reads() -> None:
    unexpected = find_account_flag_reads() - ALLOWED
    assert not unexpected, (
        "These read the superuser account flag instead of asking whether the "
        "caller has superuser authority. Use self.is_superuser(auth_data) or "
        "self.require_superuser(auth_data), or add the line to ALLOWED with a "
        f"reason: {sorted(unexpected)}"
    )


def test_allowlist_has_no_stale_entries() -> None:
    stale = ALLOWED - find_account_flag_reads()
    assert not stale, f"ALLOWED names lines that no longer exist: {sorted(stale)}"
