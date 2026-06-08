from __future__ import annotations

import inspect
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from threading import Lock
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from catalog import ClientDefinition
from evo_service import (
    EvoApiConfig,
    EvoServiceError,
    MembershipAnalytics,
    build_membership_analytics,
    demo_membership_analytics,
    fetch_members,
    load_membership_analytics,
)
from supabase_store import (
    SupabaseConfig,
    SupabaseStoreError,
    count_members,
    delete_stale_members,
    finish_sync_run,
    latest_sync_run,
    load_members,
    start_sync_run,
    upsert_members,
)


CACHE_TTL_SECONDS = 3600
SYNC_MAX_LOAD_SECONDS = 900.0
SOURCE_SUPABASE = "Banco Supabase"
SOURCE_API = "API EVO"
SOURCE_DEMO = "Demonstracao"


@dataclass
class _AnalyticsCache:
    entries: dict[EvoApiConfig, tuple[float, MembershipAnalytics]] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)


@st.cache_resource
def _analytics_cache() -> _AnalyticsCache:
    # Only safe aggregates are cached. Raw member profiles never leave the service call.
    return _AnalyticsCache()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_supabase_status(
    config: SupabaseConfig,
    client_id: str,
) -> tuple[int, dict | None]:
    return count_members(config, client_id=client_id), latest_sync_run(config, client_id=client_id)


def _supabase_cache_key(sync_run: dict | None) -> str:
    if sync_run is None:
        return "sem-sincronizacao"
    return "|".join(
        str(sync_run.get(key) or "")
        for key in ("status", "started_at", "finished_at", "records_count")
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False, persist=True)
def _cached_supabase_analytics(
    config: SupabaseConfig,
    client_id: str,
    cache_key: str,
    fetched_at: datetime,
) -> MembershipAnalytics | None:
    records = load_members(config, client_id=client_id)
    if not records:
        return None
    return build_membership_analytics(records, fetched_at=fetched_at, source="Supabase - EVO Members")


def _clear_supabase_cache() -> None:
    _cached_supabase_status.clear()
    _cached_supabase_analytics.clear()


def _load_with_timer(label: str, loader):
    started_at = time.monotonic()
    timer = st.empty()
    spinner_kwargs = {"show_time": True} if "show_time" in inspect.signature(st.spinner).parameters else {}
    with st.spinner(label, **spinner_kwargs):
        result = loader()
    elapsed = time.monotonic() - started_at
    if result is not None:
        timer.caption(f"{label} concluido em {elapsed:.1f}s.")
    else:
        timer.empty()
    return result


def _cached_api_analytics(
    config: EvoApiConfig,
    progress_callback=None,
) -> tuple[MembershipAnalytics, bool]:
    cache = _analytics_cache()
    with cache.lock:
        cached = cache.entries.get(config)
        if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1], True
        analytics = load_membership_analytics(config, progress_callback)
        cache.entries[config] = (time.monotonic(), analytics)
        return analytics, False


def _clear_api_cache(config: EvoApiConfig | None) -> None:
    cache = _analytics_cache()
    with cache.lock:
        if config is None:
            cache.entries.clear()
        else:
            cache.entries.pop(config, None)


def _brl(value: float) -> str:
    formatted = f"{value:,.2f}"
    return f"R$ {formatted.replace(',', '_').replace('.', ',').replace('_', '.')}"


def _integer(value: int | float) -> str:
    return f"{int(value):,}".replace(",", ".")


def _filter_by_date(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    dates = frame["periodo_date"].dt.date
    return frame[(dates >= start) & (dates <= end)].copy()


def _default_period(date_min: date, date_max: date) -> tuple[date, date]:
    current_month_start = date.today().replace(day=1)
    latest_month_start = date_max.replace(day=1)
    start = current_month_start if date_min <= current_month_start <= date_max else latest_month_start
    start = max(date_min, min(start, date_max))
    return start, date_max


def _bar_chart(
    frame: pd.DataFrame,
    *,
    x: str,
    y: str,
    color: str,
    orientation: str = "v",
) -> None:
    if frame.empty:
        st.info("Nao ha dados para esta analise.")
        return
    figure = go.Figure()
    figure.add_bar(x=frame[x], y=frame[y], marker_color=color, orientation=orientation)
    figure.update_layout(height=300, margin=dict(l=10, r=10, t=18, b=10), showlegend=False)
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})


def _render_flow_chart(monthly: pd.DataFrame) -> None:
    figure = go.Figure()
    figure.add_bar(
        name="Novos clientes",
        x=monthly["periodo"],
        y=monthly["novos_clientes"],
        marker_color="#22c55e",
    )
    figure.add_bar(
        name="Cancelamentos",
        x=monthly["periodo"],
        y=monthly["cancelamentos"],
        marker_color="#ef4444",
    )
    figure.update_layout(
        barmode="group",
        height=300,
        margin=dict(l=10, r=10, t=20, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})


def _render_active_chart(monthly: pd.DataFrame) -> None:
    figure = go.Figure()
    figure.add_scatter(
        x=monthly["periodo"],
        y=monthly["base_ativa"],
        mode="lines+markers",
        fill="tozeroy",
        fillcolor="rgba(79,70,229,.08)",
        line=dict(color="#4f46e5", width=3),
    )
    figure.update_layout(height=300, margin=dict(l=10, r=10, t=18, b=10), showlegend=False)
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})


def _render_metrics(analytics: MembershipAnalytics, monthly: pd.DataFrame) -> None:
    totals = analytics.totals
    new_members = int(monthly["novos_clientes"].sum())
    adhesions = int(monthly["adesoes"].sum())
    renewals = int(monthly["renovacoes"].sum())
    cancellations = int(monthly["cancelamentos"].sum())

    first_row = st.columns(4, gap="small")
    first_row[0].metric("Membros ativos", _integer(totals["membros_ativos"]))
    first_row[1].metric("Membros inativos", _integer(totals["membros_inativos"]))
    first_row[2].metric("Risco de evasao", _integer(totals["risco_evasao"]), help="Ativos sem acesso ha mais de 30 dias.")
    first_row[3].metric("Acessos bloqueados", _integer(totals["acessos_bloqueados"]))

    second_row = st.columns(4, gap="small")
    second_row[0].metric("Novos clientes", _integer(new_members))
    second_row[1].metric("Novas adesoes", _integer(adhesions))
    second_row[2].metric("Renovacoes", _integer(renewals))
    second_row[3].metric("Cancelamentos", _integer(cancellations))

    insights = [
        f"Saldo liquido de clientes no intervalo: **{new_members - cancellations:+d}**.",
        f"Clientes ativos em risco por baixa frequencia: **{_integer(totals['risco_evasao'])}**.",
    ]
    if not analytics.plans.empty:
        top_plan = analytics.plans.sort_values("matriculas_ativas", ascending=False).iloc[0]["plano"]
        insights.append(f"Plano com maior base ativa: **{top_plan}**.")
    st.info("  \n".join(insights))


def _load_supabase_analytics(
    *,
    store_config: SupabaseConfig,
    client_id: str,
    sync_run: dict | None,
) -> MembershipAnalytics | None:
    fetched_at = sync_run.get("finished_at") or sync_run.get("started_at") if sync_run is not None else None
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc)
    try:
        analytics = _cached_supabase_analytics(
            store_config,
            client_id,
            _supabase_cache_key(sync_run),
            fetched_at,
        )
    except SupabaseStoreError as exc:
        st.error(str(exc))
        return None

    if analytics is None:
        st.info("Ainda nao ha clientes sincronizados no Supabase. Use Sincronizar EVO no menu lateral.")
        return None

    return analytics


def _sync_members_to_supabase(
    *,
    store_config: SupabaseConfig,
    client: ClientDefinition,
    api_config: EvoApiConfig | None,
) -> bool:
    if api_config is None:
        st.error("Credenciais da EVO nao configuradas no servidor para este cliente.")
        return False

    progress = st.progress(0, text="Iniciando sincronizacao com a EVO...")
    message = st.empty()
    sync_run_id: int | None = None
    saved_records = 0
    sync_config = replace(
        api_config,
        max_load_seconds=max(api_config.max_load_seconds, SYNC_MAX_LOAD_SECONDS),
    )

    def update_progress(pages: int, records: int, elapsed: float) -> None:
        percent = min(int((elapsed / sync_config.max_load_seconds) * 100), 95)
        progress.progress(
            percent,
            text=f"Sincronizando clientes: pagina {pages}, {records} registros recebidos...",
        )
        message.caption("A carga completa pode levar alguns minutos, mas ficara salva no Supabase.")

    def save_page(_page: int, batch: list[dict], _elapsed: float) -> None:
        nonlocal saved_records
        saved_records += upsert_members(
            store_config,
            client_id=client.id,
            records=batch,
            sync_run_id=sync_run_id or 0,
        )

    try:
        sync_run_id = start_sync_run(store_config, client.id)
        records = fetch_members(sync_config, update_progress, save_page)
        delete_stale_members(store_config, client_id=client.id, sync_run_id=sync_run_id)
        finish_sync_run(
            store_config,
            sync_run_id,
            status="success",
            records_count=saved_records,
        )
    except (EvoServiceError, SupabaseStoreError) as exc:
        if sync_run_id is not None:
            try:
                finish_sync_run(
                    store_config,
                    sync_run_id,
                    status="error",
                    records_count=saved_records,
                    error_message=str(exc),
                )
            except SupabaseStoreError:
                pass
        progress.empty()
        message.empty()
        st.error(str(exc))
        return False

    progress.progress(100, text="Sincronizacao concluida.")
    message.caption(f"{saved_records} clientes salvos no Supabase.")
    if len(records) != saved_records:
        st.info(f"{len(records) - saved_records} registros sem idMember foram ignorados.")
    _clear_supabase_cache()
    return True


def _format_sync_timestamp(value) -> str:
    if value is None:
        return "sem horario registrado"
    try:
        return value.astimezone().strftime("%d/%m/%Y %H:%M")
    except AttributeError:
        return str(value)


def _render_supabase_status(store_config: SupabaseConfig, client_id: str) -> dict | None:
    try:
        total, sync_run = _cached_supabase_status(store_config, client_id)
    except SupabaseStoreError as exc:
        st.caption(f"Supabase configurado, mas sem status disponivel: {exc}")
        return None

    st.caption(f"{total} clientes salvos no Supabase.")
    if sync_run is None:
        st.caption("Nenhuma sincronizacao registrada.")
        return None

    timestamp = _format_sync_timestamp(sync_run.get("finished_at") or sync_run.get("started_at"))
    st.caption(f"Ultima sincronizacao: {sync_run.get('status')} em {timestamp}.")
    return sync_run


def _load_analytics(
    *,
    client_id: str,
    api_config: EvoApiConfig | None,
    source: str,
    store_config: SupabaseConfig | None,
    sync_run: dict | None = None,
) -> MembershipAnalytics | None:
    if source == SOURCE_DEMO:
        return demo_membership_analytics()
    if source == SOURCE_SUPABASE:
        if store_config is None:
            st.error("Supabase nao configurado no servidor.")
            return None
        return _load_with_timer(
            "Carregando dados do Supabase e montando indicadores",
            lambda: _load_supabase_analytics(
                store_config=store_config,
                client_id=client_id,
                sync_run=sync_run,
            ),
        )
    if api_config is None:
        st.error("Credenciais da EVO nao configuradas no servidor para este cliente.")
        return None
    progress = st.progress(0, text="Preparando consulta da EVO...")
    message = st.empty()

    def update_progress(pages: int, records: int, elapsed: float) -> None:
        percent = min(int((elapsed / api_config.max_load_seconds) * 100), 95)
        progress.progress(
            percent,
            text=f"Carregando clientes: pagina {pages}, {records} registros recebidos...",
        )
        message.caption(f"Tempo decorrido: {elapsed:.0f}s. O primeiro carregamento pode levar alguns minutos.")

    try:
        analytics, was_cached = _cached_api_analytics(api_config, update_progress)
        progress.progress(100, text="Indicadores agregados atualizados.")
        if was_cached:
            message.caption("Dados agregados reaproveitados do cache seguro do servidor.")
        else:
            message.caption("Carga concluida. Os dados agregados ficarao em cache por 1 hora.")
        return analytics
    except EvoServiceError as exc:
        progress.empty()
        message.empty()
        st.error(str(exc))
        return None


def render_membership_dashboard(
    *,
    client: ClientDefinition,
    api_config: EvoApiConfig | None,
    allow_demo: bool,
    store_config: SupabaseConfig | None = None,
    can_manage_data: bool = False,
) -> None:
    source_options = ([SOURCE_SUPABASE] if store_config is not None else [])
    if can_manage_data or store_config is None:
        source_options += [SOURCE_API]
    source_options += [SOURCE_DEMO] if allow_demo else []
    sync_run: dict | None = None
    source = source_options[0]
    if can_manage_data:
        with st.sidebar:
            st.markdown("### Fonte de dados")
            source = st.radio("Origem", source_options, label_visibility="collapsed")
            if store_config is not None:
                sync_run = _render_supabase_status(store_config, client.id)
                if st.button("Sincronizar EVO", use_container_width=True):
                    if _sync_members_to_supabase(
                        store_config=store_config,
                        client=client,
                        api_config=api_config,
                    ):
                        st.rerun()
            if source == SOURCE_API and st.button("Atualizar cache API", use_container_width=True):
                _clear_api_cache(api_config)
                st.rerun()
    elif store_config is not None:
        try:
            _, sync_run = _cached_supabase_status(store_config, client.id)
        except SupabaseStoreError:
            sync_run = None

    st.markdown('<p class="eyebrow">Painel de clientes</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="page-title">{client.name}</p>', unsafe_allow_html=True)

    analytics = _load_analytics(
        client_id=client.id,
        api_config=api_config,
        source=source,
        store_config=store_config,
        sync_run=sync_run,
    )
    if analytics is None:
        return
    if analytics.monthly.empty:
        st.info("Nenhum dado agregado foi encontrado para montar este painel.")
        return

    st.caption(
        f"Base de clientes e matriculas | Fonte: {analytics.source} | "
        f"Atualizado em {analytics.fetched_at.astimezone().strftime('%d/%m/%Y %H:%M')}"
    )
   

    date_min = analytics.monthly["periodo_date"].min().date()
    date_max = analytics.monthly["periodo_date"].max().date()
    default_start, default_end = _default_period(date_min, date_max)
    start_key = f"membership_start_{client.id}"
    end_key = f"membership_end_{client.id}"
    bounds_key = f"membership_bounds_{client.id}"
    bounds = (date_min, date_max)
    if st.session_state.get(bounds_key) != bounds:
        st.session_state[start_key] = default_start
        st.session_state[end_key] = default_end
        st.session_state[bounds_key] = bounds
    st.session_state.setdefault(start_key, default_start)
    st.session_state.setdefault(end_key, default_end)

    filters = st.columns([1.2, 1.2, 3.6], gap="small")
    with filters[0]:
        start = st.date_input(
            "Periodo inicial",
            value=st.session_state[start_key],
            min_value=date_min,
            max_value=date_max,
            key=start_key,
        )
    with filters[1]:
        end = st.date_input(
            "Periodo final",
            value=st.session_state[end_key],
            min_value=date_min,
            max_value=date_max,
            key=end_key,
        )
    if start > end:
        st.warning("O periodo inicial deve ser anterior ao periodo final.")
        return

    monthly = _filter_by_date(analytics.monthly, start, end)
    if monthly.empty:
        st.info("Nenhum dado encontrado para o periodo selecionado.")
        return

    _render_metrics(analytics, monthly)
    overview, engagement, contracts, table = st.tabs(
        ["Visao geral", "Engajamento", "Matriculas atuais", "Tabela mensal"]
    )

    with overview:
        left, right = st.columns(2, gap="small")
        with left:
            st.markdown("#### Novos clientes x cancelamentos")
            _render_flow_chart(monthly)
        with right:
            st.markdown("#### Evolucao da base ativa estimada")
            _render_active_chart(monthly)
        st.caption(
            "A serie historica estima a base ativa pelas vigencias das matriculas. "
            "Os cards superiores usam o status atual informado diretamente pela EVO."
        )

    with engagement:
        left, right = st.columns(2, gap="small")
        with left:
            st.markdown("#### Recencia do ultimo acesso dos ativos")
            _bar_chart(analytics.access_health, x="faixa", y="membros", color="#f59e0b")
        with right:
            st.markdown("#### Membros ativos por unidade")
            _bar_chart(
                analytics.branches.sort_values("membros_ativos"),
                x="membros_ativos",
                y="unidade",
                color="#0ea5e9",
                orientation="h",
            )

    with contracts:
        summary = st.columns(3, gap="small")
        summary[0].metric("Matriculas ativas", _integer(analytics.totals["matriculas_ativas"]))
        summary[1].metric("Termos pendentes", _integer(analytics.totals["termos_pendentes"]))
        if analytics.totals["contratos_com_valor_proximo_mes"]:
            summary[2].metric("Previsao proximo mes", _brl(float(analytics.totals["valor_proximo_mes"])))
        else:
            summary[2].metric("Previsao proximo mes", "Nao disponivel")
        left, right = st.columns(2, gap="small")
        with left:
            st.markdown("#### Matriculas ativas por plano")
            _bar_chart(
                analytics.plans.sort_values("matriculas_ativas"),
                x="matriculas_ativas",
                y="plano",
                color="#4f46e5",
                orientation="h",
            )
        with right:
            st.markdown("#### Situacao da matricula mais recente")
            _bar_chart(analytics.contract_statuses, x="status", y="contratos", color="#8b5cf6")

    with table:
        display = monthly[
            [
                "periodo",
                "novos_clientes",
                "adesoes",
                "renovacoes",
                "cancelamentos",
                "saldo",
                "base_ativa",
            ]
        ].copy()
        display.columns = [
            "PERIODO",
            "NOVOS CLIENTES",
            "NOVAS ADESOES",
            "RENOVACOES",
            "CANCELAMENTOS",
            "SALDO",
            "BASE ATIVA ESTIMADA",
        ]
        st.dataframe(display.iloc[::-1], hide_index=True, use_container_width=True)
