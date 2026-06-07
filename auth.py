from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass


HASH_SCHEME = "pbkdf2_sha256"
HASH_ITERATIONS = 600_000


@dataclass(frozen=True)
class AppUser:
    username: str
    display_name: str
    password_hash: str
    client_ids: frozenset[str]
    role: str = "client"
    dashboard_ids: frozenset[str] = frozenset()

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_password(password: str, *, salt: bytes | None = None, iterations: int = HASH_ITERATIONS) -> str:
    if not password:
        raise ValueError("A senha nao pode ser vazia.")
    if iterations < 100_000:
        raise ValueError("O numero de iteracoes e insuficiente.")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{HASH_SCHEME}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, raw_iterations, raw_salt, expected_digest = encoded_hash.split("$", 3)
        if scheme != HASH_SCHEME:
            return False
        iterations = int(raw_iterations)
        if iterations < 100_000:
            return False
        salt = bytes.fromhex(raw_salt)
        calculated = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()
        return hmac.compare_digest(calculated, expected_digest)
    except (TypeError, ValueError):
        return False


def load_users(config: Mapping) -> dict[str, AppUser]:
    auth_config = config.get("auth", {})
    if not isinstance(auth_config, Mapping):
        return {}
    raw_users = auth_config.get("users", [])
    if not isinstance(raw_users, (list, tuple)):
        return {}

    users: dict[str, AppUser] = {}
    for raw_user in raw_users:
        if not isinstance(raw_user, Mapping):
            continue
        username = str(raw_user.get("username", "")).strip().casefold()
        display_name = str(raw_user.get("display_name", username)).strip()
        password_hash = str(raw_user.get("password_hash", "")).strip()
        role = _normalize_role(raw_user.get("role"), username)
        client_ids = frozenset(str(client_id).strip() for client_id in raw_user.get("clients", []))
        dashboard_ids = _dashboard_ids(raw_user)
        if username and password_hash and client_ids:
            users[username] = AppUser(
                username=username,
                display_name=display_name or username,
                password_hash=password_hash,
                client_ids=client_ids,
                role=role,
                dashboard_ids=dashboard_ids,
            )
    return users


def authenticate(users: dict[str, AppUser], username: str, password: str) -> AppUser | None:
    user = users.get(username.strip().casefold())
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


def _normalize_role(value: object, username: str = "") -> str:
    role = str(value or "").strip().casefold()
    if role in {"admin", "administrator", "administrador"}:
        return "admin"
    if username == "admin":
        return "admin"
    return "client"


def _dashboard_ids(raw_user: Mapping) -> frozenset[str]:
    dashboards = raw_user.get("dashboards", [])
    if isinstance(dashboards, Mapping):
        return frozenset(
            f"{str(client_id).strip()}:{str(dashboard_id).strip()}"
            for client_id, dashboard_ids in dashboards.items()
            for dashboard_id in (dashboard_ids if isinstance(dashboard_ids, (list, tuple, set)) else [])
            if str(client_id).strip() and str(dashboard_id).strip()
        )
    if isinstance(dashboards, (list, tuple, set)):
        return frozenset(str(dashboard_id).strip() for dashboard_id in dashboards if str(dashboard_id).strip())
    return frozenset()
