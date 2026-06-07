from __future__ import annotations

import time
from collections.abc import Mapping

import streamlit as st

from auth import AppUser, authenticate, hash_password, load_users
from catalog import (
    CLIENTS,
    DASHBOARDS,
    authorized_clients_for_user,
    authorized_dashboards_for_client,
    dashboard_permission_id,
    evo_api_config,
)
from dashboards.membership import render_membership_dashboard
from supabase_store import (
    ManagedUser,
    SupabaseConfig,
    SupabaseStoreError,
    create_app_session,
    delete_app_session,
    list_managed_users,
    load_users as load_database_users,
    save_managed_user,
    validate_app_session,
)


st.set_page_config(
    page_title="Portal de Insights",
    page_icon=":bar_chart:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;600;700;800&display=swap');
      html, body, [class*="css"] { font-family: 'Sora', sans-serif; }
      .main { background: #f7f8fc; }
      .stMainBlockContainer {
        max-width: 1380px;
        padding: 2.25rem 2rem 3rem;
      }
      .page-title { color: #111827; font-size: clamp(1.55rem, 2.4vw, 2rem); font-weight: 800; margin: 0; }
      .eyebrow {
        color: #6b7280; font-size: .72rem; font-weight: 700;
        letter-spacing: 1.6px; margin-bottom: 4px; text-transform: uppercase;
      }
      .subtle { color: #6b7280; font-size: .88rem; }
      .client-card {
        background: #fff; border: 1px solid #e5e7eb; border-radius: 16px;
        min-height: 120px; padding: 20px 22px;
      }
      .client-name { color: #111827; font-size: 1.1rem; font-weight: 800; }
      .client-meta { color: #6b7280; font-size: .82rem; margin-top: 8px; }
      .privacy-note {
        background: #eef2ff; border-radius: 12px; color: #4338ca;
        font-size: .78rem; padding: 10px 14px;
      }
      [data-testid="stMetric"] {
        background: #fff; border: 1px solid #e5e7eb; border-radius: 14px;
        min-height: 104px; padding: 14px 16px;
      }
      [data-testid="stMetric"] * { color: #111827 !important; }
      [data-testid="stMetricLabel"] { min-height: 2.2rem; color: #6b7280 !important; }
      [data-testid="stMetricLabel"] * { color: #6b7280 !important; }
      [data-testid="stMetricValue"] { color: #111827 !important; font-size: clamp(1.35rem, 2vw, 1.75rem); }
      [data-testid="stMetricDelta"] { color: #374151 !important; }
      [data-testid="stPlotlyChart"] {
        background: #fff; border: 1px solid #eef0f4; border-radius: 14px;
        overflow: hidden; padding: 4px;
      }
      [data-testid="stDateInput"] { max-width: 230px; }
      [data-testid="stTabs"] { margin-top: .35rem; }
      .stAlert { border-radius: 12px; }

      @media (max-width: 1100px) {
        .stMainBlockContainer { max-width: 980px; padding: 1.75rem 1.4rem 2.5rem; }
        [data-testid="stMetric"] { min-height: 96px; padding: 12px 13px; }
        [data-testid="stMetricLabel"] { font-size: .8rem; }
      }

      @media (max-width: 760px) {
        .stMainBlockContainer { padding: 1.25rem .85rem 2rem; }
        [data-testid="stHorizontalBlock"] { gap: .7rem; }
        [data-testid="stMetric"] { min-height: 88px; padding: 10px 11px; }
        [data-testid="stMetricLabel"] { min-height: auto; }
        [data-testid="stPlotlyChart"] { padding: 0; }
        [data-testid="stDateInput"] { max-width: none; }
        .privacy-note { font-size: .74rem; padding: 9px 11px; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def _secrets() -> Mapping:
    try:
        return st.secrets
    except FileNotFoundError:
        return {}


def _config_value(secrets: Mapping, key: str, default):
    app_config = secrets.get("app", {})
    return app_config.get(key, default) if isinstance(app_config, Mapping) else default


def _query_value(key: str) -> str:
    value = st.query_params.get(key, "")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def _set_query_state(
    *,
    page: str | None = None,
    client_id: str | None = None,
    dashboard_id: str | None = None,
    session_token: str | None = None,
) -> None:
    if session_token is not None:
        st.query_params["session_token"] = session_token
    if page is not None:
        st.query_params["page"] = page
    if client_id is not None:
        st.query_params["client"] = client_id
    if dashboard_id is not None:
        st.query_params["dashboard"] = dashboard_id


def _clear_query_state() -> None:
    for key in ("session_token", "page", "client", "dashboard"):
        if key in st.query_params:
            del st.query_params[key]


def _restore_page_from_query() -> None:
    page = _query_value("page")
    if page in {"clients", "dashboards", "dashboard", "admin_users"}:
        st.session_state["page"] = page
    client_id = _query_value("client")
    if client_id:
        st.session_state["selected_client"] = client_id
    dashboard_id = _query_value("dashboard")
    if dashboard_id:
        st.session_state["selected_dashboard"] = dashboard_id


def _load_app_users(secrets: Mapping, store_config: SupabaseConfig | None) -> dict[str, AppUser]:
    users = load_users(secrets)
    if store_config is None:
        return users
    try:
        database_users = load_database_users(store_config)
    except SupabaseStoreError as exc:
        st.warning(f"Nao foi possivel carregar usuarios do Supabase. Usando secrets.toml. Detalhe: {exc}")
        return users
    return {**users, **database_users}


def _session_user(
    users: dict[str, AppUser],
    store_config: SupabaseConfig | None,
    timeout_minutes: int,
) -> AppUser | None:
    session = st.session_state.get("authenticated_user")
    if isinstance(session, dict) and session.get("expires_at", 0) > time.time():
        user = users.get(session.get("username", ""))
        if user is not None:
            return user
        st.session_state.pop("authenticated_user", None)

    token = _query_value("session_token")
    if store_config is None or not token:
        return None
    try:
        username = validate_app_session(store_config, token)
    except SupabaseStoreError as exc:
        st.warning(str(exc))
        return None
    if username is None:
        _clear_query_state()
        return None
    user = users.get(username)
    if user is None:
        _clear_query_state()
        return None

    st.session_state["authenticated_user"] = {
        "username": user.username,
        "expires_at": time.time() + timeout_minutes * 60,
    }
    st.session_state["session_token"] = token
    _restore_page_from_query()
    return user


def _start_session(
    user: AppUser,
    timeout_minutes: int,
    store_config: SupabaseConfig | None,
) -> None:
    st.session_state["authenticated_user"] = {
        "username": user.username,
        "expires_at": time.time() + timeout_minutes * 60,
    }
    st.session_state["page"] = "clients"
    st.session_state.pop("login_attempts", None)
    st.session_state.pop("login_blocked_until", None)
    if store_config is None:
        _set_query_state(page="clients")
        return
    try:
        token = create_app_session(store_config, username=user.username, timeout_minutes=timeout_minutes)
    except SupabaseStoreError as exc:
        st.warning(str(exc))
        _set_query_state(page="clients")
        return
    st.session_state["session_token"] = token
    _set_query_state(page="clients", session_token=token)


def _logout(store_config: SupabaseConfig | None = None) -> None:
    token = str(st.session_state.get("session_token") or _query_value("session_token"))
    if store_config is not None and token:
        try:
            delete_app_session(store_config, token)
        except SupabaseStoreError as exc:
            st.warning(str(exc))
    for key in [
        "authenticated_user",
        "session_token",
        "page",
        "selected_client",
        "selected_dashboard",
        "admin_selected_user",
    ]:
        st.session_state.pop(key, None)
    _clear_query_state()


def _go_to(page: str, *, client_id: str | None = None, dashboard_id: str | None = None) -> None:
    st.session_state["page"] = page
    if page == "clients":
        st.session_state.pop("selected_client", None)
        st.session_state.pop("selected_dashboard", None)
        _set_query_state(page=page, client_id="", dashboard_id="")
        if "client" in st.query_params:
            del st.query_params["client"]
        if "dashboard" in st.query_params:
            del st.query_params["dashboard"]
        return
    if client_id is not None:
        st.session_state["selected_client"] = client_id
    if dashboard_id is not None:
        st.session_state["selected_dashboard"] = dashboard_id
    _set_query_state(
        page=page,
        client_id=st.session_state.get("selected_client", ""),
        dashboard_id=st.session_state.get("selected_dashboard", ""),
    )


def _dashboard_options(client_ids: set[str] | frozenset[str]) -> list[str]:
    options: list[str] = []
    for client_id in sorted(client_ids):
        client = CLIENTS.get(client_id)
        if client is None:
            continue
        options.extend(dashboard_permission_id(client.id, dashboard_id) for dashboard_id in client.dashboard_ids)
    return options


def _dashboard_label(permission_id: str) -> str:
    client_id, _, dashboard_id = permission_id.partition(":")
    client = CLIENTS.get(client_id)
    dashboard = DASHBOARDS.get(dashboard_id)
    if client is None or dashboard is None:
        return permission_id
    return f"{client.name} / {dashboard.name}"


def _safe_password_hash(password: str, confirmation: str) -> str | None:
    if not password and not confirmation:
        return None
    if password != confirmation:
        st.error("As senhas informadas nao coincidem.")
        return None
    try:
        return hash_password(password)
    except ValueError as exc:
        st.error(str(exc))
        return None


def _save_user_from_form(
    *,
    store_config: SupabaseConfig,
    username: str,
    display_name: str,
    role: str,
    is_active: bool,
    client_ids: list[str],
    dashboard_ids: list[str],
    password_hash: str | None,
) -> bool:
    try:
        save_managed_user(
            store_config,
            username=username,
            display_name=display_name,
            role=role,
            is_active=is_active,
            client_ids=client_ids,
            dashboard_ids=dashboard_ids,
            password_hash=password_hash,
        )
    except SupabaseStoreError as exc:
        st.error(str(exc))
        return False
    st.success("Usuario salvo com sucesso.")
    return True


def _render_configuration_required() -> None:
    st.markdown('<p class="eyebrow">Configuracao inicial</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-title">Portal protegido ainda nao configurado</p>', unsafe_allow_html=True)
    st.write(
        "Cadastre ao menos um usuario da aplicacao em `.streamlit/secrets.toml`. "
        "Use `.streamlit/secrets.example.toml` como referencia."
    )
    st.code("python scripts/hash_password.py", language="powershell")
    st.info("As credenciais da EVO ficam apenas no servidor e nao devem ser digitadas no navegador.")


def _render_login(
    users: dict[str, AppUser],
    timeout_minutes: int,
    store_config: SupabaseConfig | None,
) -> None:
    st.markdown('<p class="eyebrow">Acesso restrito</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-title">Portal de Insights</p>', unsafe_allow_html=True)
    st.write("Entre com sua conta para visualizar os clientes e paineis autorizados.")

    blocked_until = float(st.session_state.get("login_blocked_until", 0))
    blocked_seconds = max(0, int(blocked_until - time.time()))
    if blocked_seconds:
        st.warning(f"Muitas tentativas invalidas. Aguarde {blocked_seconds + 1} segundos.")

    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Usuario", autocomplete="username")
        password = st.text_input("Senha", type="password", autocomplete="current-password")
        submitted = st.form_submit_button(
            "Entrar",
            type="primary",
            use_container_width=True,
            disabled=bool(blocked_seconds),
        )

    if not submitted or blocked_seconds:
        return

    user = authenticate(users, username, password)
    if user is None:
        attempts = int(st.session_state.get("login_attempts", 0)) + 1
        st.session_state["login_attempts"] = attempts
        if attempts >= 5:
            st.session_state["login_blocked_until"] = time.time() + 30
            st.session_state["login_attempts"] = 0
        st.error("Usuario ou senha invalidos.")
        return

    _start_session(user, timeout_minutes, store_config)
    st.rerun()


def _render_sidebar(user: AppUser, store_config: SupabaseConfig | None) -> None:
    with st.sidebar:
        st.markdown("### Portal de Insights")
        st.caption(f"Conectado como {user.display_name}")
        st.divider()
        if st.button("Inicio", use_container_width=True):
            _go_to("clients")
            st.rerun()
        if user.is_admin and store_config is not None:
            if st.button("Usuarios", use_container_width=True):
                _go_to("admin_users")
                st.rerun()
        if st.button("Sair", use_container_width=True):
            _logout(store_config)
            st.rerun()
        st.divider()
        st.caption("Acesso validado no servidor. Nenhuma credencial de integracao e exibida no navegador.")


def _render_clients(user: AppUser) -> None:
    clients = authorized_clients_for_user(user)
    st.markdown('<p class="eyebrow">Inicio</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-title">Clientes disponiveis</p>', unsafe_allow_html=True)
    st.write("Selecione um cliente para acessar os paineis liberados para sua conta.")

    if not clients:
        st.warning("Sua conta esta ativa, mas ainda nao possui clientes autorizados.")
        return

    columns = st.columns(min(3, len(clients)))
    for index, client in enumerate(clients):
        with columns[index % len(columns)]:
            st.markdown(
                (
                    '<div class="client-card">'
                    f'<div class="client-name">{client.name}</div>'
                    f'<div class="client-meta">{client.description}</div>'
                    f'<div class="client-meta">{len(client.dashboard_ids)} painel(is) disponivel(is)</div>'
                    "</div>"
                ),
                unsafe_allow_html=True,
            )
            if st.button("Acessar cliente", key=f"open-client-{client.id}", use_container_width=True):
                _go_to("dashboards", client_id=client.id)
                st.rerun()


def _authorized_client(user: AppUser):
    client_id = st.session_state.get("selected_client")
    if not user.is_admin and client_id not in user.client_ids:
        return None
    client = CLIENTS.get(client_id)
    if client is None:
        return None
    if not authorized_dashboards_for_client(user, client):
        return None
    return client


def _render_dashboards(user: AppUser) -> None:
    client = _authorized_client(user)
    if client is None:
        _go_to("clients")
        st.warning("Cliente nao autorizado para esta conta.")
        return

    if st.button("Voltar para clientes"):
        _go_to("clients")
        st.rerun()

    st.markdown('<p class="eyebrow">Cliente</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="page-title">{client.name}</p>', unsafe_allow_html=True)
    st.write("Escolha o painel que deseja analisar.")

    for dashboard in authorized_dashboards_for_client(user, client):
        with st.container(border=True):
            left, right = st.columns([5, 1])
            with left:
                st.markdown(f"### {dashboard.name}")
                st.caption(dashboard.description)
            with right:
                if st.button("Abrir painel", key=f"open-dashboard-{dashboard.id}", use_container_width=True):
                    _go_to("dashboard", client_id=client.id, dashboard_id=dashboard.id)
                    st.rerun()


def _render_users_table(users: list[ManagedUser]) -> None:
    rows = []
    for user in users:
        rows.append(
            {
                "USUARIO": user.username,
                "NOME": user.display_name,
                "PAPEL": "Admin" if user.role == "admin" else "Cliente",
                "ATIVO": "Sim" if user.is_active else "Nao",
                "CLIENTES": ", ".join(CLIENTS[client_id].name for client_id in sorted(user.client_ids) if client_id in CLIENTS),
                "PAINEIS": ", ".join(_dashboard_label(dashboard_id) for dashboard_id in sorted(user.dashboard_ids))
                or "Todos do cliente",
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)


def _render_create_user_form(store_config: SupabaseConfig) -> None:
    client_options = list(CLIENTS.keys())
    with st.form("create-user-form", clear_on_submit=False):
        username = st.text_input("Usuario", placeholder="cliente_bemove")
        display_name = st.text_input("Nome de exibicao", placeholder="BE.MOVE Cidade Jardim")
        role = st.selectbox("Papel", ["client", "admin"], format_func=lambda value: "Cliente" if value == "client" else "Admin")
        is_active = st.checkbox("Usuario ativo", value=True)
        client_ids = st.multiselect(
            "Clientes liberados",
            client_options,
            format_func=lambda client_id: CLIENTS[client_id].name,
        )
        dashboard_options = _dashboard_options(set(CLIENTS))
        dashboard_ids = st.multiselect(
            "Paineis liberados",
            dashboard_options,
            format_func=_dashboard_label,
            help="Se deixar vazio, o usuario acessa todos os paineis dos clientes liberados.",
        )
        password = st.text_input("Senha", type="password")
        confirmation = st.text_input("Confirmar senha", type="password")
        submitted = st.form_submit_button("Criar usuario", type="primary", use_container_width=True)

    if not submitted:
        return
    password_hash = _safe_password_hash(password, confirmation)
    if password_hash is None:
        st.error("Informe uma senha para criar o usuario.")
        return
    if _save_user_from_form(
        store_config=store_config,
        username=username,
        display_name=display_name,
        role=role,
        is_active=is_active,
        client_ids=client_ids,
        dashboard_ids=dashboard_ids,
        password_hash=password_hash,
    ):
        st.rerun()


def _render_edit_user_form(store_config: SupabaseConfig, managed_users: list[ManagedUser]) -> None:
    if not managed_users:
        st.info("Nenhum usuario cadastrado no Supabase.")
        return

    users_by_name = {user.username: user for user in managed_users}
    usernames = sorted(users_by_name)
    selected_username = st.selectbox("Usuario para editar", usernames, key="admin_selected_user")
    selected = users_by_name[selected_username]
    client_options = list(CLIENTS.keys())
    selected_clients = [client_id for client_id in sorted(selected.client_ids) if client_id in CLIENTS]
    dashboard_options = _dashboard_options(set(CLIENTS))
    selected_dashboards = [
        dashboard_id
        for dashboard_id in sorted(selected.dashboard_ids)
        if dashboard_id in set(dashboard_options)
    ]
    if not selected_dashboards:
        selected_dashboards = _dashboard_options(set(selected_clients))

    with st.form(f"edit-user-form-{selected.username}", clear_on_submit=False):
        display_name = st.text_input("Nome de exibicao", value=selected.display_name)
        role = st.selectbox(
            "Papel",
            ["client", "admin"],
            index=0 if selected.role == "client" else 1,
            format_func=lambda value: "Cliente" if value == "client" else "Admin",
        )
        is_active = st.checkbox("Usuario ativo", value=selected.is_active)
        client_ids = st.multiselect(
            "Clientes liberados",
            client_options,
            default=selected_clients,
            format_func=lambda client_id: CLIENTS[client_id].name,
        )
        dashboard_options = _dashboard_options(set(client_ids))
        dashboard_ids = st.multiselect(
            "Paineis liberados",
            dashboard_options,
            default=[dashboard_id for dashboard_id in selected_dashboards if dashboard_id in dashboard_options],
            format_func=_dashboard_label,
            help="Se deixar vazio, o usuario acessa todos os paineis dos clientes liberados.",
        )
        password = st.text_input("Nova senha", type="password", help="Deixe em branco para manter a senha atual.")
        confirmation = st.text_input("Confirmar nova senha", type="password")
        submitted = st.form_submit_button("Salvar alteracoes", type="primary", use_container_width=True)

    if not submitted:
        return
    password_hash = _safe_password_hash(password, confirmation)
    if password_hash is None and (password or confirmation):
        return
    if _save_user_from_form(
        store_config=store_config,
        username=selected.username,
        display_name=display_name,
        role=role,
        is_active=is_active,
        client_ids=client_ids,
        dashboard_ids=dashboard_ids,
        password_hash=password_hash,
    ):
        st.rerun()


def _render_admin_users(store_config: SupabaseConfig | None) -> None:
    st.markdown('<p class="eyebrow">Administracao</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-title">Usuarios e permissoes</p>', unsafe_allow_html=True)
    if store_config is None:
        st.warning("Configure o Supabase para gerenciar usuarios pelo painel.")
        return

    try:
        managed_users = list_managed_users(store_config)
    except SupabaseStoreError as exc:
        st.error(str(exc))
        return

    _render_users_table(managed_users)
    create_tab, edit_tab = st.tabs(["Criar usuario", "Editar usuario"])
    with create_tab:
        _render_create_user_form(store_config)
    with edit_tab:
        _render_edit_user_form(store_config, managed_users)


def _render_dashboard(user: AppUser, secrets: Mapping) -> None:
    client = _authorized_client(user)
    dashboard_id = st.session_state.get("selected_dashboard")
    dashboard = DASHBOARDS.get(dashboard_id)
    if (
        client is None
        or dashboard is None
        or dashboard.id not in {item.id for item in authorized_dashboards_for_client(user, client)}
    ):
        _go_to("clients")
        st.warning("Painel nao autorizado para esta conta.")
        return

    if st.button("Voltar para paineis"):
        _go_to("dashboards", client_id=client.id)
        st.rerun()

    allow_demo = bool(_config_value(secrets, "allow_demo_data", False))
    if dashboard.renderer == "membership":
        render_membership_dashboard(
            client=client,
            api_config=evo_api_config(secrets, client.id),
            allow_demo=allow_demo,
            store_config=SupabaseConfig.from_secrets(secrets),
            can_manage_data=user.is_admin,
        )


secrets = _secrets()
store_config = SupabaseConfig.from_secrets(secrets)
users = _load_app_users(secrets, store_config)
timeout_minutes = int(_config_value(secrets, "session_timeout_minutes", 30))
timeout_minutes = min(max(timeout_minutes, 5), 720)

if not users:
    _render_configuration_required()
    st.stop()

current_user = _session_user(users, store_config, timeout_minutes)
if current_user is None:
    _render_login(users, timeout_minutes, store_config)
    st.stop()

_render_sidebar(current_user, store_config)
page = st.session_state.get("page", "clients")
if page == "admin_users":
    if current_user.is_admin:
        _render_admin_users(store_config)
    else:
        _go_to("clients")
        st.warning("Acesso administrativo restrito.")
elif page == "dashboards":
    _render_dashboards(current_user)
elif page == "dashboard":
    _render_dashboard(current_user, secrets)
else:
    _render_clients(current_user)
