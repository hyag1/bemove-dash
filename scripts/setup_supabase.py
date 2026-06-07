from __future__ import annotations

from pathlib import Path
import os
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth import load_users
from supabase_store import SupabaseConfig, SupabaseStoreError, ensure_schema, upsert_users


SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"


def _load_secrets() -> dict:
    if not SECRETS_PATH.exists():
        raise SupabaseStoreError(f"Arquivo de segredos nao encontrado: {SECRETS_PATH}")
    with SECRETS_PATH.open("rb") as handle:
        return tomllib.load(handle)


def main() -> int:
    try:
        secrets = _load_secrets()
        config = SupabaseConfig.from_secrets(secrets)
        if config is None and os.environ.get("SUPABASE_DATABASE_URL"):
            config = SupabaseConfig(
                database_url=os.environ["SUPABASE_DATABASE_URL"],
                project_url=os.environ.get("SUPABASE_PROJECT_URL", ""),
                publishable_key=os.environ.get("SUPABASE_PUBLISHABLE_KEY", ""),
            )
        if config is None:
            raise SupabaseStoreError("Configure a secao [supabase] em .streamlit/secrets.toml.")

        ensure_schema(config)
        users = load_users(secrets)
        synced_users = upsert_users(config, users.values())
    except SupabaseStoreError as exc:
        print(f"Erro: {exc}")
        return 1

    print(f"Schema Supabase aplicado. Usuarios sincronizados: {synced_users}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
