"""Connection settings, read from .env.

You fill in the id and password in .env; this builds the connection
strings from them. If your provider hands you a ready-made connection
string instead, put it in DATABASE_URL or REDIS_URL and it wins.

abc.md:246 - "Use a secret manager for model credentials, database
endpoints, signing keys ... Commit only a safe .env.example."
"""

import os
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def postgres_url() -> str:
    """postgresql://user:password@host:port/database"""
    ready_made = os.environ.get("DATABASE_URL", "").strip()
    if ready_made:
        return ready_made

    user = os.environ.get("POSTGRES_USER", "memory")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "memory")

    # quote() so a password containing @ or / does not break the URL.
    url = f"postgresql://{quote(user)}:{quote(password)}@{host}:{port}/{database}"
    if _flag("POSTGRES_SSL"):
        url += "?sslmode=require"
    return url


def redis_url() -> str:
    """redis://[:password@]host:port/db — rediss:// when TLS is on."""
    ready_made = os.environ.get("REDIS_URL", "").strip()
    if ready_made:
        return ready_made

    password = os.environ.get("REDIS_PASSWORD", "")
    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    db = os.environ.get("REDIS_DB", "0")

    scheme = "rediss" if _flag("REDIS_SSL") else "redis"
    credentials = f":{quote(password)}@" if password else ""
    return f"{scheme}://{credentials}{host}:{port}/{db}"


def redis_prefix() -> str:
    """Namespace for every Redis key this app writes.

    Redis databases are shared by whatever connects to them, so keys are
    prefixed as well as separated by database number. A key looks like
    `spotifymem:idem:user_001:key_1`, which cannot collide with another
    project on the same server.
    """
    return os.environ.get("REDIS_PREFIX", "spotifymem").rstrip(":")


def redis_key(*parts: str) -> str:
    """Build a namespaced key: redis_key("idem", subject, key)."""
    return ":".join([redis_prefix(), *parts])


def safe(url: str) -> str:
    """The same URL with the password masked, for printing and logging.

    abc.md:322 - logs carry identifiers and outcomes, not secrets.
    """
    if "@" not in url:
        return url
    prefix, _, rest = url.partition("://")
    credentials, _, host = rest.rpartition("@")
    if ":" in credentials:
        user, _, _ = credentials.partition(":")
        credentials = f"{user}:****"
    return f"{prefix}://{credentials}@{host}"
