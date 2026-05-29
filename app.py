import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import base64

# ─── CONFIGURAÇÃO DA PÁGINA ───────────────────────────────────────────────────
st.set_page_config(
    page_title="BE.MOVE Dashboard",
    page_icon="🏋️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── CSS CUSTOMIZADO ──────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Sora:wght@300;400;600;700;800&display=swap');

  html, body, [class*="css"] { font-family: 'Sora', sans-serif; }

  .main { background: #f5f6fa; }

  .metric-card {
    background: white;
    border-radius: 16px;
    padding: 20px 28px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.06);
    text-align: center;
  }
  .metric-value { font-size: 2.4rem; font-weight: 800; color: #4f46e5; }
  .metric-label { font-size: 0.8rem; font-weight: 600; color: #9ca3af; letter-spacing: 1px; text-transform: uppercase; }

  .section-title {
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: #9ca3af;
    margin-bottom: 4px;
  }
  .page-title {
    font-size: 2rem;
    font-weight: 800;
    color: #111827;
    margin: 0;
  }
  .sync-label {
    font-size: 0.82rem;
    color: #9ca3af;
    margin-top: 2px;
  }

  div[data-testid="stDataFrame"] table {
    font-family: 'Sora', sans-serif !important;
    font-size: 0.85rem;
  }

  .stAlert { border-radius: 12px; }
</style>
""", unsafe_allow_html=True)


# ─── FUNÇÃO DE AUTENTICAÇÃO / BUSCA DE DADOS ─────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def buscar_dados_api(usuario: str, senha: str) -> pd.DataFrame | None:
    """
    Consome a API da EVO com Basic Auth e retorna um DataFrame.
    O cache evita requisições desnecessárias (TTL de 5 minutos).
    """
    url = "https://evo-integracao-api.w12app.com.br/api/v3/membermembership"

    # Parâmetros básicos — ajuste conforme a documentação da API
    params = {
        "take": 50,       # registros por página
        "skip": 0,        # offset
        "active": "true", # apenas ativos
    }

    try:
        response = requests.get(
            url,
            auth=(usuario, senha),
            params=params,
            timeout=15,
        )

        if response.status_code == 401:
            return "auth_error"
        if response.status_code != 200:
            return f"api_error:{response.status_code}"

        dados = response.json()

        # A API retorna lista direta ou objeto com chave — adaptar se necessário
        if isinstance(dados, list):
            return pd.DataFrame(dados)
        elif isinstance(dados, dict):
            # Tenta encontrar a lista dentro do objeto
            for key in ["memberships", "data", "items", "result", "records"]:
                if key in dados and isinstance(dados[key], list):
                    return pd.DataFrame(dados[key])
            return pd.DataFrame([dados])
        else:
            return pd.DataFrame()

    except requests.exceptions.ConnectionError:
        return "connection_error"
    except requests.exceptions.Timeout:
        return "timeout_error"
    except Exception as e:
        return f"error:{str(e)}"


# ─── DADOS FIXOS DE EXEMPLO (para quando não houver API conectada) ─────────────
def dados_exemplo() -> pd.DataFrame:
    """Gera dados demo idênticos ao dashboard da imagem."""
    meses = [
        "NOV/24","DEZ/24","JAN/25","FEV/25","MAR/25","ABR/25",
        "MAI/25","JUN/25","JUL/25","AGO/25","SET/25","OUT/25",
        "NOV/25","DEZ/25","JAN/26","FEV/26","MAR/26",
    ]
    novos     = [215,205,140,140,85,70,70,65,70,65,100,65,80,70,42,39,42]
    canc      = [25,30,50,160,60,50,45,40,40,35,45,40,55,60,73,63,44]
    desist    = [10,10,15,15,10,10,8,8,8,7,10,8,12,10,21,20,8]
    renov     = [30,35,20,15,12,10,8,10,10,12,10,10,15,10,12,5,7]
    retornos  = [20,18,25,20,15,12,10,12,14,12,15,12,18,15,19,31,16]

    registros = []
    ativos = 240
    for i, mes in enumerate(meses):
        total_in  = novos[i] + renov[i] + retornos[i]
        total_out = canc[i] + desist[i]
        ativos_fim = ativos + total_in - total_out
        registros.append({
            "periodo": mes,
            "ativos_inicio": ativos,
            "novos": novos[i],
            "renovacoes": renov[i],
            "retornos": retornos[i],
            "total_in": total_in,
            "cancelamentos": canc[i],
            "desistencias": desist[i],
            "total_out": total_out,
            "ativos_fim": ativos_fim,
        })
        ativos = ativos_fim
    return pd.DataFrame(registros)


# ─── GRÁFICO: MATRÍCULAS VS CANCELAMENTOS ────────────────────────────────────
def grafico_matriculas_cancelamentos(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Matrículas",
        x=df["periodo"], y=df["novos"],
        marker_color="#22c55e",
        width=0.35,
    ))
    fig.add_trace(go.Bar(
        name="Cancelamentos",
        x=df["periodo"], y=df["cancelamentos"],
        marker_color="#ef4444",
        width=0.35,
    ))
    fig.update_layout(
        barmode="group",
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(showgrid=False, tickfont=dict(size=10)),
        yaxis=dict(showgrid=True, gridcolor="#f3f4f6", tickfont=dict(size=10)),
        height=280,
    )
    return fig


# ─── GRÁFICO: EVOLUÇÃO DA BASE TOTAL ─────────────────────────────────────────
def grafico_evolucao_base(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["periodo"],
        y=df["ativos_fim"],
        mode="lines",
        fill="tozeroy",
        fillcolor="rgba(99,102,241,0.08)",
        line=dict(color="#4f46e5", width=2.5),
        name="Ativos",
    ))
    fig.update_layout(
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis=dict(showgrid=False, tickfont=dict(size=10)),
        yaxis=dict(showgrid=True, gridcolor="#f3f4f6", tickfont=dict(size=10)),
        showlegend=False,
        height=280,
    )
    return fig


# ─── TABELA: FLUXO MENSAL ─────────────────────────────────────────────────────
def render_tabela(df: pd.DataFrame):
    df_tabela = df[[
        "periodo","ativos_inicio","novos","renovacoes",
        "retornos","total_in","cancelamentos","desistencias",
        "total_out","ativos_fim",
    ]].copy().iloc[::-1].reset_index(drop=True)  # ordem decrescente

    df_tabela.columns = [
        "PERÍODO","ATIVOS INÍCIO","NOVOS","RENOV.",
        "RETORNOS","TOTAL IN","CANC.","DESIST.",
        "TOTAL OUT","ATIVOS FIM",
    ]

    st.dataframe(
        df_tabela,
        use_container_width=True,
        hide_index=True,
        column_config={
            "NOVOS":      st.column_config.NumberColumn("NOVOS",      format="%d", help="Novas matrículas no período"),
            "CANC.":      st.column_config.NumberColumn("CANC.",      format="%d", help="Cancelamentos confirmados"),
            "DESIST.":    st.column_config.NumberColumn("DESIST.",    format="%d", help="Desistências (sem cancelamento formal)"),
            "ATIVOS FIM": st.column_config.NumberColumn("ATIVOS FIM", format="%d", help="Base ativa ao final do mês"),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  LAYOUT PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════════════

# ─── SIDEBAR: LOGIN ───────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🔐 Autenticação")
    st.caption("Credenciais da API EVO/W12")

    # Lê de st.secrets se existir, senão exibe campos
    usuario_default = st.secrets.get("EVO_USER", "") if hasattr(st, "secrets") else ""
    senha_default   = st.secrets.get("EVO_PASS", "") if hasattr(st, "secrets") else ""

    usuario = st.text_input("Usuário", value=usuario_default, placeholder="seu@usuario.com")
    senha   = st.text_input("Senha",   value=senha_default,   type="password", placeholder="••••••••")

    conectar = st.button("🔗 Conectar API", use_container_width=True, type="primary")
    st.divider()
    usar_demo = st.toggle("📊 Usar dados de demonstração", value=True)
    st.caption("Ative para ver o dashboard com dados de exemplo enquanto configura a API.")

# ─── CABEÇALHO ────────────────────────────────────────────────────────────────
col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown('<p class="section-title">👁 Visão Detalhada</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-title">BE.MOVE CIDADE JARDIM</p>', unsafe_allow_html=True)
    st.markdown('<p class="sync-label">Sincronizado: Out/2024 — Mar/2026</p>', unsafe_allow_html=True)

# ─── BUSCA DE DADOS ───────────────────────────────────────────────────────────
df = None
erro = None

if usar_demo:
    df = dados_exemplo()
elif conectar or (usuario and senha):
    with st.spinner("Consultando API..."):
        resultado = buscar_dados_api(usuario, senha)

    if isinstance(resultado, str):
        if resultado == "auth_error":
            erro = "❌ Usuário ou senha inválidos. Verifique suas credenciais."
        elif resultado == "connection_error":
            erro = "🔌 Não foi possível conectar à API. Verifique sua internet ou a URL."
        elif resultado == "timeout_error":
            erro = "⏱ A API demorou demais para responder. Tente novamente."
        else:
            erro = f"⚠️ Erro inesperado: {resultado}"
    elif resultado is not None and not resultado.empty:
        df = resultado
    else:
        st.info("A API retornou dados vazios. Ative **Dados de demonstração** para visualizar o layout.")

if erro:
    st.error(erro)

# ─── DASHBOARD ────────────────────────────────────────────────────────────────
if df is not None:
    ativos_atual = int(df["ativos_fim"].iloc[-1]) if "ativos_fim" in df.columns else 0

    with col_status:
        st.markdown(f"""
        <div class="metric-card" style="margin-top:16px">
          <div class="metric-label">Status Atual</div>
          <div class="metric-value">{ativos_atual}</div>
          <div class="metric-label" style="color:#4f46e5">Ativos</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Gráficos ──────────────────────────────────────────────────────────────
    chart1, chart2 = st.columns(2)

    with chart1:
        with st.container(border=True):
            st.markdown("**👥 Matrículas vs Cancelamentos**")
            st.plotly_chart(
                grafico_matriculas_cancelamentos(df),
                use_container_width=True,
                config={"displayModeBar": False},
            )

    with chart2:
        with st.container(border=True):
            st.markdown("**📈 Evolução da Base Total**")
            st.plotly_chart(
                grafico_evolucao_base(df),
                use_container_width=True,
                config={"displayModeBar": False},
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Tabela ────────────────────────────────────────────────────────────────
    with st.container(border=True):
        tcol1, tcol2 = st.columns([3,1])
        with tcol1:
            st.markdown("**📋 Fluxo Mensal de Dados**")
            st.caption("Passe o mouse nos ícones ⓘ para definições estratégicas de cada coluna")
        with tcol2:
            st.markdown(
                '<div style="text-align:right;padding-top:4px">'
                '<span style="background:#f3f4f6;border-radius:8px;padding:4px 12px;'
                'font-size:0.75rem;font-weight:700;color:#4f46e5">BE.MOVE CIDADE JARDIM</span>'
                '</div>',
                unsafe_allow_html=True,
            )
        render_tabela(df)

else:
    if not usar_demo:
        st.info("👈 Insira suas credenciais na barra lateral e clique em **Conectar API**, ou ative os **Dados de demonstração**.")
