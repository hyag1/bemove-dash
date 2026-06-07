from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from evo_service import EvoApiConfig


@dataclass(frozen=True)
class DashboardDefinition:
    id: str
    name: str
    description: str
    renderer: str


@dataclass(frozen=True)
class ClientDefinition:
    id: str
    name: str
    description: str
    dashboard_ids: tuple[str, ...]


DASHBOARDS = {
    "membership-flow": DashboardDefinition(
        id="membership-flow",
        name="Base de Clientes e Matriculas",
        description="Clientes ativos, engajamento, risco de evasao, adesoes, renovacoes e cancelamentos.",
        renderer="membership",
    ),
}

CLIENTS = {
    "bemove": ClientDefinition(
        id="bemove",
        name="BE.MOVE Cidade Jardim",
        description="Indicadores operacionais e comerciais da unidade.",
        dashboard_ids=("membership-flow",),
    ),
}


def authorized_clients(client_ids: Iterable[str]) -> list[ClientDefinition]:
    return [CLIENTS[client_id] for client_id in client_ids if client_id in CLIENTS]


def dashboard_permission_id(client_id: str, dashboard_id: str) -> str:
    return f"{client_id}:{dashboard_id}"


def authorized_dashboards_for_client(user, client: ClientDefinition) -> list[DashboardDefinition]:
    if getattr(user, "is_admin", False):
        return [DASHBOARDS[dashboard_id] for dashboard_id in client.dashboard_ids]

    dashboard_ids = getattr(user, "dashboard_ids", frozenset())
    if not dashboard_ids:
        return [DASHBOARDS[dashboard_id] for dashboard_id in client.dashboard_ids]

    return [
        DASHBOARDS[dashboard_id]
        for dashboard_id in client.dashboard_ids
        if dashboard_permission_id(client.id, dashboard_id) in dashboard_ids
    ]


def authorized_clients_for_user(user) -> list[ClientDefinition]:
    if getattr(user, "is_admin", False):
        return list(CLIENTS.values())

    clients = authorized_clients(getattr(user, "client_ids", frozenset()))
    return [client for client in clients if authorized_dashboards_for_client(user, client)]


def _normalize_evo_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if not endpoint:
        return EvoApiConfig.endpoint
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("O endpoint da EVO deve ser uma URL HTTPS valida.")
    # Query parameters are controlled by the service to avoid duplicates in paginated calls.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _positive_int(config: Mapping, key: str, default: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float(config: Mapping, key: str, default: float) -> float:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def evo_api_config(secrets: Mapping, client_id: str) -> EvoApiConfig | None:
    clients = secrets.get("clients", {})
    client_config = clients.get(client_id, {}) if isinstance(clients, Mapping) else {}
    evo_config = client_config.get("evo", {}) if isinstance(client_config, Mapping) else {}
    evo_config = evo_config if isinstance(evo_config, Mapping) else {}

    user = evo_config.get("user", "")
    password = evo_config.get("password", "")
    endpoint = evo_config.get("endpoint", "")

    # Backward-compatible server-side fallback for the original local configuration.
    if client_id == "bemove":
        user = user or secrets.get("EVO_USER", "")
        password = password or secrets.get("EVO_PASS", "")

    if not user or not password:
        return None
    return EvoApiConfig(
        username=str(user),
        password=str(password),
        endpoint=_normalize_evo_endpoint(str(endpoint)),
        page_size=_positive_int(evo_config, "page_size", EvoApiConfig.page_size),
        max_pages=_positive_int(evo_config, "max_pages", EvoApiConfig.max_pages),
        timeout_seconds=_positive_int(evo_config, "timeout_seconds", EvoApiConfig.timeout_seconds),
        request_interval_seconds=_positive_float(
            evo_config,
            "request_interval_seconds",
            EvoApiConfig.request_interval_seconds,
        ),
        rate_limit_retries=_positive_int(evo_config, "rate_limit_retries", EvoApiConfig.rate_limit_retries),
        max_load_seconds=_positive_float(evo_config, "max_load_seconds", EvoApiConfig.max_load_seconds),
    )
