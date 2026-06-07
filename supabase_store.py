from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from auth import AppUser, _normalize_role


SCHEMA_PATH = Path(__file__).resolve().parent / "database" / "supabase_schema.sql"


class SupabaseStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupabaseConfig:
    database_url: str
    project_url: str = ""
    publishable_key: str = ""

    @classmethod
    def from_secrets(cls, secrets: Mapping) -> "SupabaseConfig | None":
        raw_config = secrets.get("supabase", {})
        if not isinstance(raw_config, Mapping):
            return None

        database_url = str(
            raw_config.get("database_url")
            or raw_config.get("postgres_url")
            or raw_config.get("connection_string")
            or ""
        ).strip()
        if not database_url:
            return None

        return cls(
            database_url=database_url,
            project_url=str(raw_config.get("project_url", "")).strip(),
            publishable_key=str(raw_config.get("publishable_key", "")).strip(),
        )


@dataclass(frozen=True)
class ManagedUser:
    username: str
    display_name: str
    role: str
    is_active: bool
    client_ids: frozenset[str]
    dashboard_ids: frozenset[str]


def _psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise SupabaseStoreError(
            "O pacote psycopg nao esta instalado. Execute `pip install -r requirements.txt`."
        ) from exc
    return psycopg, dict_row, Jsonb


def _safe_error_message(exc: Exception, config: SupabaseConfig) -> str:
    message = str(exc).replace(config.database_url, "[database-url]")
    parsed = urlsplit(config.database_url)
    if parsed.password:
        message = message.replace(parsed.password, "[senha]")
    if (
        "getaddrinfo failed" in message
        and parsed.hostname
        and parsed.hostname.startswith("db.")
        and parsed.hostname.endswith(".supabase.co")
    ):
        message += (
            ". A connection string direta do Supabase pode exigir IPv6. "
            "Copie a connection string do Connection pooler no painel do Supabase "
            "e use-a em [supabase].database_url."
        )
    return message


def _connect(config: SupabaseConfig, *, row_factory=None):
    psycopg, dict_row, _ = _psycopg()
    try:
        return psycopg.connect(
            config.database_url,
            autocommit=True,
            row_factory=row_factory or dict_row,
        )
    except Exception as exc:
        detail = _safe_error_message(exc, config)
        raise SupabaseStoreError(f"Nao foi possivel conectar ao banco Supabase. Detalhe: {detail}") from exc


def ensure_schema(config: SupabaseConfig) -> None:
    if not SCHEMA_PATH.exists():
        raise SupabaseStoreError(f"Schema SQL nao encontrado em {SCHEMA_PATH}.")

    with _connect(config) as connection:
        try:
            connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SupabaseStoreError("Nao foi possivel criar as tabelas no Supabase.") from exc


def load_users(config: SupabaseConfig) -> dict[str, AppUser]:
    query = """
        select
            u.username,
            u.display_name,
            u.password_hash,
            coalesce(u.role, 'client') as role,
            coalesce(
                (
                    select array_agg(uc.client_id order by uc.client_id)
                    from app_user_clients uc
                    where uc.username = u.username
                ),
                '{}'
            ) as client_ids,
            coalesce(
                (
                    select array_agg(ud.client_id || ':' || ud.dashboard_id order by ud.client_id, ud.dashboard_id)
                    from app_user_dashboards ud
                    where ud.username = u.username
                ),
                '{}'
            ) as dashboard_ids
        from app_users u
        where u.is_active is true
        order by u.username
    """
    try:
        with _connect(config) as connection:
            rows = list(connection.execute(query))
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel carregar usuarios do Supabase.") from exc

    users: dict[str, AppUser] = {}
    for row in rows:
        username = str(row["username"]).strip().casefold()
        password_hash = str(row["password_hash"]).strip()
        client_ids = frozenset(str(client_id).strip() for client_id in row["client_ids"])
        dashboard_ids = frozenset(str(dashboard_id).strip() for dashboard_id in row["dashboard_ids"])
        role = _normalize_role(row.get("role"), username)
        if username and password_hash and client_ids:
            users[username] = AppUser(
                username=username,
                display_name=str(row["display_name"]).strip() or username,
                password_hash=password_hash,
                client_ids=client_ids,
                role=role,
                dashboard_ids=dashboard_ids,
            )
    return users


def upsert_users(config: SupabaseConfig, users: Iterable[AppUser]) -> int:
    users = list(users)
    if not users:
        return 0

    with _connect(config) as connection:
        try:
            for user in users:
                connection.execute(
                    """
                    insert into app_users (username, display_name, password_hash, role, is_active, updated_at)
                    values (%s, %s, %s, %s, true, now())
                    on conflict (username) do update set
                        display_name = excluded.display_name,
                        password_hash = excluded.password_hash,
                        role = excluded.role,
                        is_active = true,
                        updated_at = now()
                    """,
                    (user.username, user.display_name, user.password_hash, user.role),
                )
                connection.execute("delete from app_user_clients where username = %s", (user.username,))
                connection.execute("delete from app_user_dashboards where username = %s", (user.username,))
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        insert into app_user_clients (username, client_id)
                        values (%s, %s)
                        on conflict do nothing
                        """,
                        [(user.username, client_id) for client_id in sorted(user.client_ids)],
                    )
                    dashboard_rows = [
                        (user.username, client_id, dashboard_id)
                        for client_id, dashboard_id in _dashboard_rows(user.dashboard_ids)
                    ]
                    if dashboard_rows:
                        cursor.executemany(
                            """
                            insert into app_user_dashboards (username, client_id, dashboard_id)
                            values (%s, %s, %s)
                            on conflict do nothing
                            """,
                            dashboard_rows,
                        )
        except Exception as exc:
            raise SupabaseStoreError("Nao foi possivel sincronizar usuarios no Supabase.") from exc
    return len(users)


def _dashboard_rows(dashboard_ids: Iterable[str]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for value in dashboard_ids:
        client_id, separator, dashboard_id = str(value).partition(":")
        client_id = client_id.strip()
        dashboard_id = dashboard_id.strip()
        if separator and client_id and dashboard_id:
            rows.append((client_id, dashboard_id))
    return rows


def list_managed_users(config: SupabaseConfig) -> list[ManagedUser]:
    query = """
        select
            u.username,
            u.display_name,
            coalesce(u.role, 'client') as role,
            u.is_active,
            coalesce(
                (
                    select array_agg(uc.client_id order by uc.client_id)
                    from app_user_clients uc
                    where uc.username = u.username
                ),
                '{}'
            ) as client_ids,
            coalesce(
                (
                    select array_agg(ud.client_id || ':' || ud.dashboard_id order by ud.client_id, ud.dashboard_id)
                    from app_user_dashboards ud
                    where ud.username = u.username
                ),
                '{}'
            ) as dashboard_ids
        from app_users u
        order by u.username
    """
    try:
        with _connect(config) as connection:
            rows = list(connection.execute(query))
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel listar usuarios do Supabase.") from exc

    return [
        ManagedUser(
            username=str(row["username"]).strip().casefold(),
            display_name=str(row["display_name"]).strip(),
            role=_normalize_role(row.get("role"), str(row["username"]).strip().casefold()),
            is_active=bool(row["is_active"]),
            client_ids=frozenset(str(client_id).strip() for client_id in row["client_ids"]),
            dashboard_ids=frozenset(str(dashboard_id).strip() for dashboard_id in row["dashboard_ids"]),
        )
        for row in rows
    ]


def save_managed_user(
    config: SupabaseConfig,
    *,
    username: str,
    display_name: str,
    role: str,
    is_active: bool,
    client_ids: Iterable[str],
    dashboard_ids: Iterable[str],
    password_hash: str | None = None,
) -> None:
    username = username.strip().casefold()
    display_name = display_name.strip() or username
    role = _normalize_role(role, username)
    client_ids = frozenset(str(client_id).strip() for client_id in client_ids if str(client_id).strip())
    dashboard_rows = _dashboard_rows(dashboard_ids)
    client_ids = client_ids | frozenset(client_id for client_id, _ in dashboard_rows)

    if not username:
        raise SupabaseStoreError("Informe um usuario.")
    if not client_ids:
        raise SupabaseStoreError("Selecione ao menos um cliente.")

    try:
        with _connect(config) as connection:
            if password_hash:
                connection.execute(
                    """
                    insert into app_users (username, display_name, password_hash, role, is_active, updated_at)
                    values (%s, %s, %s, %s, %s, now())
                    on conflict (username) do update set
                        display_name = excluded.display_name,
                        password_hash = excluded.password_hash,
                        role = excluded.role,
                        is_active = excluded.is_active,
                        updated_at = now()
                    """,
                    (username, display_name, password_hash, role, is_active),
                )
            else:
                existing = connection.execute(
                    "select 1 from app_users where username = %s",
                    (username,),
                ).fetchone()
                if existing is None:
                    raise SupabaseStoreError("Informe uma senha para criar um usuario novo.")
                connection.execute(
                    """
                    update app_users
                    set display_name = %s,
                        role = %s,
                        is_active = %s,
                        updated_at = now()
                    where username = %s
                    """,
                    (display_name, role, is_active, username),
                )

            connection.execute("delete from app_user_clients where username = %s", (username,))
            connection.execute("delete from app_user_dashboards where username = %s", (username,))
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    insert into app_user_clients (username, client_id)
                    values (%s, %s)
                    on conflict do nothing
                    """,
                    [(username, client_id) for client_id in sorted(client_ids)],
                )
                if dashboard_rows:
                    cursor.executemany(
                        """
                        insert into app_user_dashboards (username, client_id, dashboard_id)
                        values (%s, %s, %s)
                        on conflict do nothing
                        """,
                        [(username, client_id, dashboard_id) for client_id, dashboard_id in dashboard_rows],
                    )
    except SupabaseStoreError:
        raise
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel salvar o usuario no Supabase.") from exc


def _session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_app_session(config: SupabaseConfig, *, username: str, timeout_minutes: int) -> str:
    token = secrets.token_urlsafe(32)
    try:
        with _connect(config) as connection:
            connection.execute("delete from app_sessions where expires_at <= now()")
            connection.execute(
                """
                insert into app_sessions (token_hash, username, expires_at)
                values (%s, %s, now() + (%s * interval '1 minute'))
                """,
                (_session_token_hash(token), username, timeout_minutes),
            )
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel iniciar a sessao persistente.") from exc
    return token


def validate_app_session(config: SupabaseConfig, token: str) -> str | None:
    token = token.strip()
    if not token:
        return None
    try:
        with _connect(config) as connection:
            connection.execute("delete from app_sessions where expires_at <= now()")
            row = connection.execute(
                """
                select s.username
                from app_sessions s
                join app_users u on u.username = s.username
                where s.token_hash = %s
                  and s.expires_at > now()
                  and u.is_active is true
                """,
                (_session_token_hash(token),),
            ).fetchone()
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel validar a sessao persistente.") from exc
    return str(row["username"]).strip().casefold() if row is not None else None


def delete_app_session(config: SupabaseConfig, token: str) -> None:
    token = token.strip()
    if not token:
        return
    try:
        with _connect(config) as connection:
            connection.execute(
                "delete from app_sessions where token_hash = %s",
                (_session_token_hash(token),),
            )
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel encerrar a sessao persistente.") from exc


def start_sync_run(config: SupabaseConfig, client_id: str) -> int:
    try:
        with _connect(config) as connection:
            row = connection.execute(
                """
                insert into evo_sync_runs (client_id, status)
                values (%s, 'running')
                returning id
                """,
                (client_id,),
            ).fetchone()
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel iniciar a sincronizacao no Supabase.") from exc
    return int(row["id"])


def finish_sync_run(
    config: SupabaseConfig,
    sync_run_id: int,
    *,
    status: str,
    records_count: int,
    error_message: str | None = None,
) -> None:
    try:
        with _connect(config) as connection:
            connection.execute(
                """
                update evo_sync_runs
                set status = %s,
                    finished_at = now(),
                    records_count = %s,
                    error_message = %s
                where id = %s
                """,
                (status, records_count, error_message, sync_run_id),
            )
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel finalizar a sincronizacao no Supabase.") from exc


def _member_id(record: Mapping[str, Any]) -> str | None:
    value = record.get("idMember")
    if value in (None, ""):
        return None
    return str(value)


def upsert_members(
    config: SupabaseConfig,
    *,
    client_id: str,
    records: Iterable[dict],
    sync_run_id: int,
) -> int:
    _, _, Jsonb = _psycopg()
    fetched_at = datetime.now(timezone.utc)
    rows = [
        (client_id, member_id, Jsonb(record), sync_run_id, fetched_at, fetched_at)
        for record in records
        if isinstance(record, Mapping) and (member_id := _member_id(record)) is not None
    ]
    if not rows:
        return 0

    try:
        with _connect(config) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    insert into evo_members (
                        client_id,
                        id_member,
                        payload,
                        last_seen_sync_id,
                        fetched_at,
                        updated_at
                    )
                    values (%s, %s, %s, %s, %s, %s)
                    on conflict (client_id, id_member) do update set
                        payload = excluded.payload,
                        last_seen_sync_id = excluded.last_seen_sync_id,
                        fetched_at = excluded.fetched_at,
                        updated_at = excluded.updated_at
                    """,
                    rows,
                )
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel salvar clientes da EVO no Supabase.") from exc
    return len(rows)


def delete_stale_members(config: SupabaseConfig, *, client_id: str, sync_run_id: int) -> int:
    try:
        with _connect(config) as connection:
            result = connection.execute(
                """
                delete from evo_members
                where client_id = %s
                  and (last_seen_sync_id is null or last_seen_sync_id <> %s)
                """,
                (client_id, sync_run_id),
            )
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel remover clientes antigos da EVO no Supabase.") from exc
    return int(result.rowcount or 0)


def load_members(config: SupabaseConfig, *, client_id: str) -> list[dict]:
    try:
        with _connect(config) as connection:
            rows = list(
                connection.execute(
                    """
                    select jsonb_build_object(
                        'idMember', payload->'idMember',
                        'status', payload->'status',
                        'branchName', payload->'branchName',
                        'lastAccessDate', payload->'lastAccessDate',
                        'accessBlocked', payload->'accessBlocked',
                        'memberships', coalesce(payload->'memberships', '[]'::jsonb)
                    ) as payload
                    from evo_members
                    where client_id = %s
                    order by id_member
                    """,
                    (client_id,),
                )
            )
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel carregar clientes da EVO no Supabase.") from exc
    return [row["payload"] for row in rows if isinstance(row.get("payload"), dict)]


def count_members(config: SupabaseConfig, *, client_id: str) -> int:
    try:
        with _connect(config) as connection:
            row = connection.execute(
                "select count(*) as total from evo_members where client_id = %s",
                (client_id,),
            ).fetchone()
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel contar clientes da EVO no Supabase.") from exc
    return int(row["total"])


def latest_sync_run(config: SupabaseConfig, *, client_id: str) -> dict | None:
    try:
        with _connect(config) as connection:
            row = connection.execute(
                """
                select status, started_at, finished_at, records_count, error_message
                from evo_sync_runs
                where client_id = %s
                order by started_at desc
                limit 1
                """,
                (client_id,),
            ).fetchone()
    except Exception as exc:
        raise SupabaseStoreError("Nao foi possivel consultar a ultima sincronizacao no Supabase.") from exc
    return dict(row) if row is not None else None
