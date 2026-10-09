"""Why this file exists
=====================

Listener accounts: sign up with a unique user id and a password, then log
in to get a pass (token) that opens only that listener's own data.

A pilot stand-in for Spotify's login. memory/auth.py explains that in
production the gateway mints tokens after checking the listener's Spotify
session; here, checking the password is that check.

    authentication  - this file: is this really the listener? (password)
                      memory/auth.py: is this pass genuine and unexpired?
    authorization   - memory/auth.py `bind_subject`: does this pass belong to
                      the listener whose data is asked for? Every endpoint
                      already calls it.

How passwords are kept
----------------------
Never as text. Each password is run through scrypt (Python's standard
library) with its own random salt, and only "scrypt$<salt>$<hash>" is stored.
Checking a login repeats the same steps and compares in constant time.

Guessing is limited: after 5 wrong passwords for one user id, logins for it
are refused for 15 minutes.

Where it is used
----------------
memory/api.py - POST /auth/signup and POST /auth/login.
scripts/setup.py - gives the five seeded test users a login when DEMO_PASSWORD
is set (local testing only).
"""

import hashlib
import hmac
import os
import secrets

from memory import cache, config, db

# The five seeded test users can be given a login for local testing. Their
# password comes from DEMO_PASSWORD in the private .env - never from the code
# or the docs, which are public. Unset (the default on a server), the test
# users get no login at all, so the only accounts are people who signed up.
DEMO_USERS = ("user_001", "user_002", "user_003", "user_004", "user_005")

# Wrong passwords allowed per user id before logins are paused.
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60


# Turn a password into "scrypt$salt$hash". The same password gives a
# different result every time, because the salt is new each time.
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


# Does this password match the stored hash?
def password_matches(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                            n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


# Is this user id already taken - by an account or by an existing subject?
def id_taken(subject_id: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM account WHERE subject_id = %s"
            " UNION SELECT 1 FROM consent WHERE subject_id = %s",
            (subject_id, subject_id),
        ).fetchone()
    return row is not None


# Store a new account. The caller checks id_taken first.
def create_account(subject_id: str, password: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO account (subject_id, password_hash) VALUES (%s, %s)",
            (subject_id, hash_password(password)),
        )


# Check a login. True only for the right user id and password.
def check_login(subject_id: str, password: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT password_hash FROM account WHERE subject_id = %s",
            (subject_id,),
        ).fetchone()
    if row is None:
        # Spend the same time as a real check, so a wrong user id and a
        # wrong password cannot be told apart by how long the answer takes.
        password_matches(password, hash_password("not-a-real-password"))
        return False
    return password_matches(password, row[0])


# --- Limiting password guessing --------------------------------------------

def _failures_key(subject_id: str) -> str:
    return config.redis_key("login_failures", subject_id)


# True when this user id has had too many wrong passwords recently.
def is_locked(subject_id: str) -> bool:
    count = cache.client().get(_failures_key(subject_id))
    return count is not None and int(count) >= MAX_FAILED_LOGINS


# Count one wrong password; the count clears itself after the lockout time.
def record_failure(subject_id: str) -> None:
    key = _failures_key(subject_id)
    if cache.client().incr(key) == 1:
        cache.client().expire(key, LOCKOUT_SECONDS)


# A successful login clears the count.
def clear_failures(subject_id: str) -> None:
    cache.client().delete(_failures_key(subject_id))


# Give the seeded test users a login, only if DEMO_PASSWORD is set.
def ensure_demo_accounts(password: str | None = None) -> int:
    password = password or os.environ.get("DEMO_PASSWORD", "").strip()
    if not password:
        return 0
    created = 0
    for subject_id in DEMO_USERS:
        with db.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM account WHERE subject_id = %s", (subject_id,)
            ).fetchone()
        if not exists:
            create_account(subject_id, password)
            created += 1
    return created
