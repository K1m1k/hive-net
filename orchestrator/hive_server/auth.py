# SPDX-License-Identifier: AGPL-3.0-or-later  (commercial licence available: see COMMERCIAL-LICENSE.md)
"""Users, sessions and node tokens. Passwords and tokens are never stored in plain text."""
import hashlib
import hmac
import os
import re
import secrets

from .db import Database, now

SESSION_TTL = 30 * 24 * 3600
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


class AuthError(Exception):
    pass


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return f"pbkdf2$200000${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), dk)
    except Exception:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def create_session(db: Database, user_id: int) -> str:
    tok = new_token()
    with db.tx() as c:
        c.execute("INSERT INTO sessions(token_hash, user_id, expires) VALUES (?,?,?)",
                  (token_hash(tok), user_id, now() + SESSION_TTL))
        c.execute("DELETE FROM sessions WHERE expires < ?", (now(),))
    return tok


def user_from_token(db: Database, token: str):
    if not token:
        return None
    r = db.one("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?",
               (token_hash(token), now()))
    return r


def node_from_token(db: Database, token: str):
    if not token:
        return None
    return db.one("SELECT * FROM nodes WHERE token_hash=? AND revoked=0", (token_hash(token),))
