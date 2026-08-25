import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
import urllib.parse
import json
import os
from typing import Dict, List, Any, Optional

# =========================================================
# 1. CONFIGURACIÓN DE PÁGINA Y CREDENCIALES
# =========================================================
st.set_page_config(
    page_title="Radar Enterprise Parlay Global",
    page_icon="⚽",
    layout="wide"
)

DB_FILE = "bitacora_backup.json"

# Uso de st.secrets con fallback a tus llaves actuales
API_KEY = st.secrets.get("ODDS_API_KEY", "e6414a3efabaf34994030cd0a8ea88b1")

HL_API_KEY = st.secrets.get("HL_API_KEY", "f18c6837-5aaf-4880-8148-9b7a133b5557")
HL_BASE_URL = "https://soccer.highlightly.net"

AF_API_KEY = st.secrets.get("AF_API_KEY", "5cca912e78e3ec42256f42db0b59fda2")
AF_BASE_URL = "https://v3.football.api-sports.io"

# =========================================================
# 2. CAPA DE PERSISTENCIA Y SERVICIOS
# =========================================================
class BitacoraManager:
    @staticmethod
    def cargar() -> List[Dict[str, Any]]:
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    @staticmethod
    def guardar(historial: List[Dict[str, Any]]) -> None:
        try:
            with open(DB_FILE, "w", encoding="utf-8") as f:
                json.dump(historial, f, ensure_ascii=False, indent=4)
        except Exception as e:
            st.sidebar.error(f"Error al guardar persistencia: {e}")

    @staticmethod
    def limpiar() -> None:
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)
        st.session_state['historial_apuestas'] = []

def enviar_telegram(mensaje: str) -> bool:
    token = st.session_state.get('tg_token', '')
    chat_id = st.session_state.get('tg_chat_id', '')
    if not token or not chat_id:
        st.warning("⚠️ Configura tu Bot Token y Chat ID de Telegram en el sidebar antes de enviar.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=10)
        if r.status_code == 200:
            return True
        st.error(f"❌ Error al enviar a Telegram ({r.status_code}): {r.text[:200]}")
        return False
    except Exception as e:
        st.error(f"💥 Error de conexión con Telegram: {e}")
        return False

# =========================================================
# 3. TEMA VISUAL CSS
# =========================================================
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap');

    :root {
        --rg-bg: #0b0e14;
        --rg-card: #12161f;
        --rg-card-alt: #171c27;
        --rg-border: #232a38;
        --rg-border-soft: #1b212c;
        --rg-accent: #00d2d3;
        --rg-accent-2: #7c5cff;
        --rg-success: #2ecc71;
        --rg-warn: #f1c40f;
        --rg-danger: #e74c3c;
        --rg-gold: #feca57;
        --rg-text-soft: #8a94a6;
    }

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    h1, h2, h3 { font-family: 'Inter', sans-serif; font-weight: 800 !important; letter-spacing: -0.02em; }
    h1 { background: linear-gradient(90deg, #ffffff 0%, #9fb4c7 100%); -webkit-background-clip: text; background-clip: text; }

    .prob-alta { color: var(--rg-success); font-weight: 700; }
    .prob-media { color: var(--rg-warn); font-weight: 700; }
    .prob-baja { color: var(--rg-danger); font-weight: 700; }
    .movimiento-sube { color: var(--rg-success); font-weight: bold; }
    .movimiento-baja { color: var(--rg-danger); font-weight: bold; }

    .match-header { font-size: 18px; font-weight: 700; margin-bottom: 2px; letter-spacing: -0.01em; }
    .liga-chip {
        display: inline-block; font-size: 11px; font-weight: 700; text-transform: uppercase;
        letter-spacing: 0.04em; color: var(--rg-accent); background: rgba(0,210,211,0.10);
        border: 1px solid rgba(0,210,211,0.35); border-radius: 999px; padding: 2px 10px; margin-bottom: 6px;
    }
    .kickoff-chip {
        display: inline-block; font-size: 12px; font-weight: 600; color: #cfd8e3;
        background: var(--rg-card-alt); border: 1px solid var(--rg-border); border-radius: 999px;
        padding: 3px 10px; margin-top: 2px;
    }

    .value-pill {
        display: inline-block; font-size: 10.5px; font-weight: 800; letter-spacing: 0.03em;
        color: #0b0e14; background: linear-gradient(90deg, var(--rg-gold), #ff9f43);
        border-radius: 999px; padding: 1px 8px; margin-left: 4px; vertical-align: middle;
    }

    .creditos-caja {
        background: linear-gradient(135deg, #151b26 0%, #10141c 100%);
        padding: 12px 15px 12px 18px;
        border-radius: 10px;
        border-left: 4px solid var(--rg-accent);
        border-top: 1px solid var(--rg-border-soft);
        border-right: 1px solid var(--rg-border-soft);
        border-bottom: 1px solid var(--rg-border-soft);
        margin-bottom: 14px;
    }

    div[class*="st-key-match_"] {
        background: linear-gradient(180deg, var(--rg-card) 0%, #0f131b 100%) !important;
        border: 1px solid var(--rg-border) !important;
        border-radius: 16px !important;
        box-shadow: 0 2px 10px rgba(0,0,0,0.25);
    }

    div[class*="st-key-ticket_card"] {
        background: linear-gradient(180deg, #12161f 0%, #0d1017 100%) !important;
        border: 2px dashed rgba(0,210,211,0.45) !important;
        border-radius: 18px !important;
        box-shadow: 0 0 0 1px rgba(0,210,211,0.06), 0 6px 20px rgba(0,0,0,0.35);
    }
    .ticket-titulo {
        font-family: 'Inter', sans-serif; font-weight: 800; font-size: 15px; letter-spacing: 0.04em;
        text-transform: uppercase; color: var(--rg-accent); text-align: center; margin-bottom: 6px;
    }
    .ticket-item { border-bottom: 1px dashed var(--rg-border); padding: 8px 0 10px 0; }
    .ticket-item:last-of-type { border-bottom: none; }
    .ticket-cuota-tag {
        font-family: 'JetBrains Mono', monospace; font-weight: 700; color: var(--rg-accent);
        background: rgba(0,210,211,0.08); border-radius: 6px; padding: 1px 6px; font-size: 12.5px;
    }

    div.stButton > button {
        border-radius: 10px !important; font-weight: 600 !important;
        transition: transform .08s ease, box-shadow .15s ease;
        border: 1px solid var(--rg-border) !important;
    }
    div.stButton > button:hover { transform: translateY(-1px); }
    div.stButton > button[kind="primary"] {
        background: linear-gradient(90deg, #00b3b4, #00d2d3) !important;
        border: none !important; box-shadow: 0 4px 14px rgba(0,210,211,0.25);
    }

    div[data-testid="stMetric"] {
        background: var(--rg-card-alt); border: 1px solid var(--rg-border);
        border-radius: 12px; padding: 10px 14px;
    }

    button[data-baseweb="tab"] { font-weight: 600 !important; }
    div[data-baseweb="tab-highlight"] { background-color: var(--rg-accent) !important; }

    .welcome-card {
        background: linear-gradient(160deg, #141a24 0%, #0f131a 100%);
        border: 1px solid var(--rg-border); border-radius: 18px; padding: 28px 30px; text-align: left;
    }
    .welcome-card h3 { margin-top: 0; }
    .welcome-step { display: flex; align-items: center; gap: 12px; margin: 10px 0; color: #cfd8e3; font-size: 14.5px; }
    .welcome-step .num {
        flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%; background: rgba(0,210,211,0.12);
        border: 1px solid rgba(0,210,211,0.4); color: var(--rg-accent); font-weight: 700; font-size: 12.5px;
        display: flex; align-items: center; justify-content: center;
    }

    div[data-testid="stAlert"] { border-radius: 12px !important; border: 1px solid var(--rg-border) !important; }
    div[data-testid="stExpander"] { border: 1px solid var(--rg-border) !important; border-radius: 12px !important; background: var(--rg-card-alt); overflow: hidden; }
    div[data-testid="stExpander"] summary { font-weight: 600 !important; }

    div[data-baseweb="select"] > div, div[data-baseweb="input"] > div, textarea {
        border-radius: 10px !important; background: var(--rg-card-alt) !important; border-color: var(--rg-border) !important;
    }
    span[data-baseweb="tag"] { border-radius: 6px !important; background: rgba(0,210,211,0.16) !important; }
    hr { border-color: var(--rg-border) !important; opacity: 0.7; }

    .empty-card {
        background: linear-gradient(160deg, #1c1712 0%, #14110d 100%);
        border: 1px solid rgba(241,196,15,0.25); border-radius: 16px; padding: 22px 26px;
    }

    div[class*="st-key-telegram_btn"] button {
        background: linear-gradient(90deg, #229ED9, #1B8FC9) !important; color: white !important; border: none !important;
    }

    div[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; border: 1px solid var(--rg-border); }

    div[class*="st-key-chip_"] {
        border: 1px solid var(--rg-border); background: var(--rg-card-alt);
        border-radius: 12px; padding: 8px 10px 10px 10px; margin-bottom: 8px;
        transition: border-color .15s ease, background .15s ease;
    }
    div[class*="st-key-chip_"]:hover { border-color: rgba(0,210,211,0.5); }
    div[class*="st-key-chip_"]:has(input:checked) {
        background: rgba(0,210,211,0.10); border-color: var(--rg-accent); box-shadow: 0 0 0 1px rgba(0,210,211,0.18);
    }
    div[class*="st-key-chip_"] div[data-testid="stCheckbox"] label { font-weight: 600 !important; }

    .kpi-card {
        border-radius: 14px; padding: 16px 18px; border: 1px solid var(--rg-border);
        background: linear-gradient(160deg, var(--rg-card) 0%, #0f131b 100%); height: 100%;
    }
    .kpi-label { font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--rg-text-soft); margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
    .kpi-value { font-family: 'JetBrains Mono', monospace; font-size: 26px; font-weight: 700; line-height: 1.1; }
    .kpi-sub { font-size: 12.5px; margin-top: 4px; font-weight: 600; }

    @media (max-width: 640px) {
        .match-header { font-size: 14px; }
        .creditos-caja { padding: 8px 10px 8px 12px; margin-bottom: 8px; }
        .creditos-caja span { font-size: 15px !important; }
        div[data-testid="stMetricValue"] { font-size: 18px; }
        div.stButton > button { font-size: 13px !important; padding: 6px 8px !important; }
        small { font-size: 11px; }
        .welcome-card { padding: 18px 16px; }
        .liga-chip, .kickoff-chip { font-size: 10px !important; }
        .kpi-value { font-size: 20px; }
        .kpi-card { padding: 10px 12px; }
    }
    </style>
""", unsafe_allow_html=True)

# =========================================================
# 4. METODOS DE CLIENTES API
# =========================================================
def hl_headers(): return {"x-rapidapi-key": HL_API_KEY}

@st.cache_data(ttl=86400)
def hl_buscar_ligas(country_name):
    if not country_name: return []
    url = f"{HL_BASE_URL}/leagues"
    try:
        r = requests.get(url, headers=hl_headers(), params={"countryName": country_name, "limit": 100}, timeout=10)
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception:
        return []

@st.cache_data(ttl=300)
def hl_consultar_matches(league_id, fecha_str):
    if not league_id: return []
    url = f"{HL_BASE_URL}/matches"
    try:
        r = requests.get(url, headers=hl_headers(), params={"leagueId": league_id, "date": fecha_str, "limit": 100}, timeout=10)
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception:
        return []

@st.cache_data(ttl=300)
def hl_consultar_odds(match_id):
    if not match_id: return []
    url = f"{HL_BASE_URL}/odds"
    try:
        r = requests.get(url, headers=hl_headers(), params={"matchId": match_id, "oddsType": "prematch", "limit": 5}, timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            return data[0].get("odds", []) if data else []
        return []
    except Exception:
        return []

HL_LEAGUE_IDS = {
    "🇨🇴 Primera A (Colombia)": 204173,
    "🇪🇨 LigaPro (Ecuador)": 206726,
    "🇺🇾 Primera División Uruguay - Apertura": 228852,
    "🇺🇾 Primera División Uruguay - Clausura": 230554,
    "🇵🇪 Liga 1 (Perú)": 239915,
}

def af_headers(): return {"x-apisports-key": AF_API_KEY}

def _actualizar_creditos_af(response_headers):
    restante = response_headers.get('x-ratelimit-requests-remaining')
    limite = response_headers.get('x-ratelimit-requests-limit')
    if restante is not None:
        st.session_state.creditos_restantes_af = f"{restante}/{limite}" if limite else restante

@st.cache_data(ttl=86400)
def af_buscar_ligas(country_name):
    if not country_name: return []
    url = f"{AF_BASE_URL}/leagues"
    try:
        r = requests.get(url, headers=af_headers(), params={"country": country_name}, timeout=10)
        _actualizar_creditos_af(r.headers)
        return r.json().get("response", []) if r.status_code == 200 else []
    except Exception:
        return []

@st.cache_data(ttl=300)
def af_consultar_fixtures(league_id, season, fecha_desde, fecha_hasta):
    if not league_id: return []
    url = f"{AF_BASE_URL}/fixtures"
    try:
        r = requests.get(url, headers=af_headers(), params={"league": league_id, "season": season, "from": fecha_desde, "to": fecha_hasta}, timeout=10)
        _actualizar_creditos_af(r.headers)
        return r.json().get("response", []) if r.status_code == 200 else []
    except Exception:
        return []

@st.cache_data(ttl=300)
def af_consultar_odds(fixture_id):
    if not fixture_id: return []
    url = f"{AF_BASE_URL}/odds"
    try:
        r = requests.get(url, headers=af_headers(), params={"fixture": fixture_id}, timeout=10)
        _actualizar_creditos_af(r.headers)
        if r.status_code == 200:
            data = r.json().get("response", [])
            return data[0].get("bookmakers", []) if data else []
        return []
    except Exception:
        return []

@st.cache_data(ttl=60)
def af_consultar_status():
    try:
        r = requests.get(f"{AF_BASE_URL}/status", headers=af_headers(), timeout=10)
        _actualizar_creditos_af(r.headers)
        return r.status_code, r.json() if r.text else {}
    except Exception as e:
        return None, {"error": str(e)}

@st.cache_data(ttl=60)
def af_prueba_liga_grande():
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hasta = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        r = requests.get(f"{AF_BASE_URL}/fixtures", headers=af_headers(), params={"league": 71, "season": datetime.now(timezone.utc).year, "from": hoy, "to": hasta}, timeout=10)
        _actualizar_creditos_af(r.headers)
        return r.status_code, r.json() if r.text else {}
    except Exception as e:
        return None, {"error": str(e)}

AF_LEAGUE_IDS = {
    "🇨🇴 Primera A (Colombia)": 239,
    "🇪🇨 LigaPro (Ecuador)": 242,
    "🇺🇾 Primera División Uruguay - Apertura": 268,
    "🇺🇾 Primera División Uruguay - Clausura": 270,
    "🇵🇪 Liga 1 (Perú)": 281,
    "🇲🇽 Liga MX (México)": 262,
    "🇵🇾 División Profesional Paraguay - Apertura": 250,
    "🇵🇾 División Profesional Paraguay - Clausura": 252,
}

ligas_top = {
    "🇪🇺 Champions League (Europa)": "soccer_uefa_champions_league",
    "🇪🇺 Europa League (Europa)": "soccer_uefa_europa_league",
    "🏆 Copa Libertadores (CONMEBOL)": "soccer_conmebol_copa_distribuidores",
    "🥈 Copa Sudamericana (CONMEBOL)": "soccer_conmebol_copa_sudamericana"
}

ligas_locales = {
    "🇦🇷 Liga Profesional (Argentina)": "soccer_argentina_primera_division",
    "🇨🇱 Primera División (Chile)": "soccer_chile_campeonato",
    "🇪🇨 Copa Ecuador": "soccer_ecuador_copa_ecuador",
    "🇧🇷 Brasileirao Serie A": "soccer_brazil_campeonato",
    "🇧🇷 Copa de Brasil": "soccer_brazil_copa_do_brasil",
    "🇲🇽 Liga MX (México)": "soccer_mexico_liga_mx",
    "🇪🇸 La Liga (España)": "soccer_spain_la_liga",
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League (Inglaterra)": "soccer_epl",
    "🇮🇹 Serie A (Italia)": "soccer_italy_serie_a",
    "🇩🇪 Bundesliga (Alemania)": "soccer_germany_bundesliga",
    "🇫🇷 Ligue 1 (Francia)": "soccer_france_ligue_one",
    "🇳🇱 Eredivisie (Países Bajos)": "soccer_netherlands_eredivisie",
    "🇵🇹 Primeira Liga (Portugal)": "soccer_portugal_primeira_liga"
}

todas_las_ligas = {**ligas_top, **dict(sorted(ligas_locales.items()))}
diccionario_mercados = {
    "1X2 (Ganador)": "h2h",
    "Doble Oportunidad": "double_chance",
    "Ambos Anotan (BTTS)": "btts",
    "Goles Más/Menos 2.5": "totals"
}

# =========================================================
# 5. INICIALIZACIÓN DE ESTADOS
# =========================================================
if 'historial_apuestas' not in st.session_state:
    st.session_state.historial_apuestas = BitacoraManager.cargar()
if 'version_ticket' not in st.session_state:
    st.session_state.version_ticket = 0
if 'datos_cargados' not in st.session_state:
    st.session_state.datos_cargados = {}
if 'ha_consultado' not in st.session_state:
    st.session_state.ha_consultado = False
if 'versiones_partidos' not in st.session_state:
    st.session_state.versiones_partidos = {}
if 'claves_auto' not in st.session_state:
    st.session_state.claves_auto = set()
if 'creditos_restantes' not in st.session_state:
    st.session_state.creditos_restantes = "No consultado"
if 'creditos_restantes_af' not in st.session_state:
    st.session_state.creditos_restantes_af = "No consultado"

# =========================================================
# 6. CONTROLES DEL SIDEBAR
# =========================================================
with st.sidebar:
    st.header("⚙️ Filtros de Control Global")
    
    st.markdown(f"""
        <div class="creditos-caja">
            <small style="color:#a4b0be; text-transform:uppercase; font-weight:bold;">Créditos Restantes API</small><br>
            <span style="font-size:18px; font-weight:bold; color:#00d2d3;">🔑 {st.session_state.creditos_restantes}</span>
        </div>
    """, unsafe_allow_html=True)
    st.markdown(f"""
        <div class="creditos-caja" style="border-left-color:#feca57;">
            <small style="color:#a4b0be; text-transform:uppercase; font-weight:bold;">Créditos Restantes API-Football (100/día)</small><br>
            <span style="font-size:18px; font-weight:bold; color:#feca57;">🔑 {st.session_state.creditos_restantes_af}</span>
        </div>
    """, unsafe_allow_html=True)

    if 'ligas_sels_widget' not in st.session_state:
        st.session_state.ligas_sels_widget = []

    col_sel_todas, col_sel_limpiar = st.columns(2)
    with col_sel_todas:
        if st.button("✅ Todas", use_container_width=True):
            st.session_state.ligas_sels_widget = list(todas_las_ligas.keys())
            st.rerun()
    with col_sel_limpiar:
        if st.button("🧹 Ninguna", use_container_width=True):
            st.session_state.ligas_sels_widget = []
            st.rerun()

    ligas_sels = st.multiselect("Selecciona los Torneos a Analizar:", list(todas_las_ligas.keys()), key="ligas_sels_widget")

    st.markdown("---")
    st.caption("🌎 Ligas extra vía API-Football (Colombia, Ecuador, Uruguay, Perú) — plan gratis")
    habilitar_af = st.checkbox("✅ Habilitar ligas extra (API-Football, gratis)", value=True)
    ligas_af_sels = st.multiselect("Ligas extra a analizar (API-Football):", list(AF_LEAGUE_IDS.keys()), default=[]) if habilitar_af else []

    with st.expander("🔬 Diagnóstico API-Football"):
        if st.button("Ejecutar diagnóstico"):
            cod1, data1 = af_consultar_status()
            st.write(f"**/status:** HTTP {cod1}")
            st.json(data1)
            cod2, data2 = af_prueba_liga_grande()
            st.write(f"**Brasileirao:** HTTP {cod2}")
            st.json(data2)

    with st.expander("🔧 Buscar League ID en API-Football"):
        pais_busqueda_af = st.text_input("País (ej: Colombia)", "", key="pais_busqueda_af")
        if st.button("Buscar ligas en API-Football"):
            resultados_af = af_buscar_ligas(pais_busqueda_af)
            for liga_af in resultados_af:
                st.write(f"**ID {liga_af.get('league', {}).get('id')}** — {liga_af.get('league', {}).get('name')}")

    st.markdown("---")
    st.caption("🌎 Ligas extra vía Highlightly (requiere plan PRO)")
    habilitar_hl = st.checkbox("🔒 Habilitar ligas extra (Highlightly)", value=False)
    ligas_hl_sels = st.multiselect("Ligas extra a analizar (Highlightly):", list(HL_LEAGUE_IDS.keys()), default=[]) if habilitar_hl else []

    with st.expander("🔧 Buscar League ID en Highlightly"):
        pais_busqueda = st.text_input("País (ej: Colombia)", "")
        if st.button("Buscar ligas en Highlightly"):
            resultados_hl = hl_buscar_ligas(pais_busqueda)
            for liga_hl in resultados_hl:
                st.write(f"**ID {liga_hl.get('id')}** — {liga_hl.get('name')}")

    mercados_sels = st.multiselect("Mercados de Análisis:", list(diccionario_mercados.keys()), default=["1X2 (Ganador)"])
    tiempo_sel = st.selectbox("Rango Temporal:", ["24 Horas", "48 Horas", "72 Horas"], index=1)
    limite_h = int(tiempo_sel.split()[0])
    monto_inversion = st.number_input("Inversión Base ($):", min_value=1.0, value=10.0, step=1.0)

    consultar = st.button("🔍 Consultar Radar Múltiple", type="primary", use_container_width=True)
    num_eventos_auto = st.slider("Eventos para el Generador Automático:", min_value=2, max_value=6, value=3)
    generar_auto = st.button("🎲 ¡Pre-seleccionar Muestras!", use_container_width=True)

    st.markdown("---")
    habilitar_autorefresh = st.checkbox("🔁 Auto-refresco automático", value=False)
    intervalo_min = st.number_input("Minutos entre refrescos:", min_value=1, max_value=60, value=5, disabled=not habilitar_autorefresh)
    autorefresh_disparo = False
    if habilitar_autorefresh:
        try:
            from streamlit_autorefresh import st_autorefresh
            _contador_ref = st_autorefresh(interval=int(intervalo_min * 60 * 1000), key="autorefresh_radar_key")
            if 'ultimo_contador_autorefresh' not in st.session_state:
                st.session_state.ultimo_contador_autorefresh = _contador_ref
            elif _contador_ref != st.session_state.ultimo_contador_autorefresh:
                st.session_state.ultimo_contador_autorefresh = _contador_ref
                autorefresh_disparo = True
        except ImportError:
            st.warning("⚠️ Falta paquete 'streamlit-autorefresh'.")

    st.markdown("---")
    with st.expander("🔔 Notificaciones por Telegram"):
        st.session_state['tg_token'] = st.text_input("Bot Token:", value=st.session_state.get('tg_token', ''), type="password")
        st.session_state['tg_chat_id'] = st.text_input("Chat ID:", value=st.session_state.get('tg_chat_id', ''))

# =========================================================
# 7. PROCESADORES Y CÁLCULOS MATEMÁTICOS DE CUOTAS
# =========================================================
def actualizar_creditos(headers):
    if 'x-requests-remaining' in headers:
        st.session_state.creditos_restantes = headers['x-requests-remaining']

@st.cache_data(ttl=60)
def consultar_api_odds(sport_key, market_key):
    if not sport_key: return []
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={API_KEY}&regions=eu,us&markets={market_key}&oddsFormat=decimal"
    try:
        response = requests.get(url, timeout=10)
        actualizar_creditos(response.headers)
        return response.json() if response.status_code == 200 else []
    except Exception:
        return []

@st.cache_data(ttl=60)
def consultar_api_odds_evento(sport_key, event_id, market_key):
    if not sport_key or not event_id: return None
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/odds/?apiKey={API_KEY}&regions=eu,us&markets={market_key}&oddsFormat=decimal"
    try:
        response = requests.get(url, timeout=10)
        actualizar_creditos(response.headers)
        return response.json() if response.status_code == 200 else None
    except Exception:
        return None

def filtrar_partidos_por_fecha(datos, limite_horas):
    ahora_utc = datetime.now(timezone.utc)
    res = []
    if not datos or not isinstance(datos, list): return res
    for p in datos:
        try:
            fecha_utc = datetime.fromisoformat(p['commence_time'].replace('Z', '+00:00'))
        except (ValueError, KeyError):
            continue
        horas = (fecha_utc - ahora_utc).total_seconds() / 3600
        if -12.0 <= horas <= (limite_horas + 24):
            res.append(p)
    return res

def procesar_e_inyectar_mercado(datos, mercado, limite_horas, nombre_liga, diccionario_consolidador):
    ahora_utc = datetime.now(timezone.utc)
    if not datos or not isinstance(datos, list): return

    for partido in datos:
        partido_id = partido['id']
        home, away = partido['home_team'], partido['away_team']

        try:
            fecha_utc = datetime.fromisoformat(partido['commence_time'].replace('Z', '+00:00'))
        except (ValueError, KeyError):
            continue

        horas = (fecha_utc - ahora_utc).total_seconds() / 3600
        if horas < -12.0 or horas > (limite_horas + 24): continue

        fecha_local = fecha_utc - timedelta(hours=5)
        bookmakers = partido.get('bookmakers', [])
        if not bookmakers: continue

        cuotas_globales, betano_cuotas = {}, {}

        for b in bookmakers:
            b_key = b['key'].lower()
            dict_b_markets = {m['key']: m['outcomes'] for m in b.get('markets', [])}

            if mercado == "Doble Oportunidad":
                if "double_chance" in dict_b_markets:
                    for o in dict_b_markets["double_chance"]:
                        o_name = o['name']
                        if o_name in ["home_draw", "home or draw", "1X", f"{home} or draw"]: o_name = "1X (Local o Empate)"
                        elif o_name in ["away_draw", "away or draw", "X2", f"{away} or draw"]: o_name = "X2 (Visitante o Empate)"
                        elif o_name in ["home_away", "home or away", "12", f"{home} or {away}"]: o_name = "12 (Local o Visitante)"
                        cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                        if b_key == "betano": betano_cuotas[o_name] = float(o['price'])
                elif "h2h" in dict_b_markets:
                    precios_h2h = {o['name']: float(o['price']) for o in dict_b_markets["h2h"]}
                    draw_key = next((k for k in precios_h2h if k not in [home, away]), None)
                    if home in precios_h2h and away in precios_h2h and draw_key:
                        cH, cD, cA = precios_h2h[home], precios_h2h[draw_key], precios_h2h[away]
                        c1X = round((cH * cD) / (cH + cD), 2)
                        cX2 = round((cA * cD) / (cA + cD), 2)
                        c12 = round((cH * cA) / (cH + cA), 2)
                        cuotas_globales.setdefault("1X (Local o Empate)", []).append((c1X, b['title']))
                        cuotas_globales.setdefault("X2 (Visitante o Empate)", []).append((cX2, b['title']))
                        cuotas_globales.setdefault("12 (Local o Visitante)", []).append((c12, b['title']))

            elif mercado == "Ambos Anotan (BTTS)" and "btts" in dict_b_markets:
                for o in dict_b_markets["btts"]:
                    o_name = "Sí" if o['name'].lower() in ["yes", "sí", "si"] else "No"
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                    if b_key == "betano": betano_cuotas[o_name] = float(o['price'])

            elif "Goles" in mercado and "totals" in dict_b_markets:
                for o in dict_b_markets["totals"]:
                    if o.get('point', 2.5) == 2.5:
                        o_name = "Más de 2.5" if o['name'].lower() in ["over", "más", "mas"] else "Menos de 2.5"
                        cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                        if b_key == "betano": betano_cuotas[o_name] = float(o['price'])

            elif mercado == "1X2 (Ganador)" and "h2h" in dict_b_markets:
                for o in dict_b_markets["h2h"]:
                    o_name = "Local" if o['name'] == home else ("Visitante" if o['name'] == away else "Empate")
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                    if b_key == "betano": betano_cuotas[o_name] = float(o['price'])

        max_cuotas, max_bookies, value_bets = {}, {}, {}
        cuotas_promedio_dict = {op: sum(t[0] for t in tuplas)/len(tuplas) for op, tuplas in cuotas_globales.items() if tuplas}
        overround = sum([1 / cp for cp in cuotas_promedio_dict.values()]) if cuotas_promedio_dict else 1.0

        for opcion, tuplas in cuotas_globales.items():
            precios = [t[0] for t in tuplas]
            cuota_prom = cuotas_promedio_dict[opcion]
            prob_real = (1 / cuota_prom) / overround if overround > 0 else (1 / cuota_prom)
            cuota_max = max(precios)
            bookie_max = tuplas[precios.index(cuota_max)][1]

            ev = (cuota_max * prob_real) - 1
            max_cuotas[opcion] = cuota_max
            max_bookies[opcion] = bookie_max
            value_bets[opcion] = {"ev": ev, "prob_real": prob_real * 100, "es_value": ev > 0.02}

        if max_cuotas:
            if partido_id not in diccionario_consolidador:
                diccionario_consolidador[partido_id] = {
                    "id": partido_id, "liga_origen": nombre_liga,
                    "fecha_str": fecha_local.strftime("%d/%m/%Y - %H:%M"),
                    "fecha_ts": fecha_utc.timestamp(),
                    "local": home, "visitante": away, "mercados": {}
                }
            diccionario_consolidador[partido_id]["mercados"][mercado] = {
                "max_cuotas": max_cuotas, "max_bookies": max_bookies,
                "betano_cuotas": betano_cuotas, "value_bets": value_bets,
                "todas_cuotas": cuotas_globales
            }

# =========================================================
# 8. PESTAÑAS Y VISTA DE USUARIO
# =========================================================
pestana_radar, pestana_historial = st.tabs(["🚀 RADAR MULTI-MERCADO & VALUEBETS", "📊 BITÁCORA PRO & AUDITORÍA ROI"])

with pestana_radar:
    st.title("⚽ Radar Avanzado Multi-Mercado Global")
    st.caption("Escaneo de cuotas en tiempo real · Value bets · Ticket parlay automático")

    if consultar or autorefresh_disparo:
        if (len(ligas_sels) > 0 or len(ligas_hl_sels) > 0 or len(ligas_af_sels) > 0) and len(mercados_sels) > 0:
            st.cache_data.clear()
            consolidador = {}
            st.session_state.ha_consultado = True

            mercados_featured = [m for m in mercados_sels if diccionario_mercados[m] in ("h2h", "totals")]
            total_ligas = len(ligas_sels) + len(ligas_hl_sels) + len(ligas_af_sels)

            with st.status(f"🔄 Consultando {total_ligas} liga(s)...", expanded=True) as status_consulta:
                for idx_liga, liga in enumerate(ligas_sels, start=1):
                    status_consulta.update(label=f"🔄 Consultando ({idx_liga}/{total_ligas}): {liga}")
                    sport_key = todas_las_ligas[liga]
                    for m_sel in mercados_featured:
                        raw_data = consultar_api_odds(sport_key, market_key=diccionario_mercados[m_sel])
                        procesar_e_inyectar_mercado(raw_data, m_sel, limite_h, liga, consolidador)

                    if "Doble Oportunidad" in mercados_sels:
                        base_h2h = consultar_api_odds(sport_key, market_key="h2h")
                        procesar_e_inyectar_mercado(base_h2h, "Doble Oportunidad", limite_h, liga, consolidador)

                    if "Ambos Anotan (BTTS)" in mercados_sels:
                        base_para_filtrar = consultar_api_odds(sport_key, market_key="h2h")
                        for p_base in filtrar_partidos_por_fecha(base_para_filtrar, limite_h):
                            datos_evento = consultar_api_odds_evento(sport_key, p_base['id'], "btts")
                            if datos_evento:
                                procesar_e_inyectar_mercado([datos_evento], "Ambos Anotan (BTTS)", limite_h, liga, consolidador)

                status_consulta.update(label=f"✅ Consulta completa: {len(consolidador)} partidos procesados.", state="complete")

            st.session_state.datos_cargados_previos = st.session_state.datos_cargados
            st.session_state.datos_cargados = consolidador
            st.session_state.claves_auto = set()
            st.session_state.version_ticket += 1
            st.session_state.ultima_consulta = datetime.now()

    dict_partidos = st.session_state.datos_cargados

    if generar_auto:
        if dict_partidos:
            bolsa = []
            for p_id, part in dict_partidos.items():
                for nombre_m, m_info in part['mercados'].items():
                    for opcion, val_data in m_info['value_bets'].items():
                        bolsa.append({
                            "clave": f"ap_{part['id']}_{nombre_m}_{opcion}",
                            "prob_real": val_data['prob_real']
                        })
            bolsa = sorted(bolsa, key=lambda x: x['prob_real'], reverse=True)
            k = min(len(bolsa), num_eventos_auto)
            if k > 0:
                st.session_state.claves_auto = set([x['clave'] for x in bolsa[:k]])
                st.session_state.version_ticket += 1
                st.success(f"🎯 Marcados automáticamente los {k} mejores eventos.")

    apuestas_seleccionadas = []

    if not st.session_state.ha_consultado:
        st.markdown("""
            <div class="welcome-card">
                <h3>💡 Sistema en espera de instrucciones</h3>
                <p>Selecciona tus torneos en el panel lateral y haz clic en <b>🔍 Consultar Radar Múltiple</b>.</p>
            </div>
        """, unsafe_allow_html=True)
    elif not dict_partidos:
        st.warning("⚠️ No se encontraron partidos con los filtros aplicados.")
    else:
        col_busq, col_valor, col_orden = st.columns([2.2, 1.3, 1.5])
        with col_busq: busqueda_equipo = st.text_input("🔍 Buscador rápido por equipo:", "").strip().lower()
        with col_valor: solo_valor = st.checkbox("🔥 Solo VALOR")
        with col_orden: orden_sel = st.selectbox("Ordenar por:", ["🕐 Hora del partido", "📈 Mayor probabilidad"])

        dict_partidos_filtrados = {
            p_id: p for p_id, p in dict_partidos.items()
            if busqueda_equipo in p['local'].lower() or busqueda_equipo in p['visitante'].lower()
        }

        col_izq, col_der = st.columns([6.5, 3.5])

        with col_izq:
            st.subheader(f"📋 Eventos Encontrados ({len(dict_partidos_filtrados)})")
            ligas_con_datos = list(set([p['liga_origen'] for p in dict_partidos_filtrados.values()]))
            if ligas_con_datos:
                pestanas_ligas = st.tabs(ligas_con_datos)
                for p_idx, liga_p in enumerate(ligas_con_datos):
                    with pestanas_ligas[p_idx]:
                        partidos_f = [p for p in dict_partidos_filtrados.values() if p['liga_origen'] == liga_p]
                        for part in partidos_f:
                            with st.container(border=True, key=f"match_{part['id']}"):
                                st.markdown(f"<span class='liga-chip'>🏆 {part['liga_origen']}</span>", unsafe_allow_html=True)
                                st.markdown(f"<div class='match-header'>⚽ {part['local']} vs {part['visitante']}</div>", unsafe_allow_html=True)
                                st.markdown(f"<span class='kickoff-chip'>📅 {part['fecha_str']}</span>", unsafe_allow_html=True)

                                sub_tabs = st.tabs(list(part['mercados'].keys()))
                                for m_idx, text_m in enumerate(part['mercados'].keys()):
                                    with sub_tabs[m_idx]:
                                        m_info = part['mercados'][text_m]
                                        sub_cols = st.columns(len(m_info['max_cuotas']))
                                        for idx, (opcion, cuota_m) in enumerate(m_info['max_cuotas'].items()):
                                            with sub_cols[idx]:
                                                val = m_info['value_bets'][opcion]
                                                lbl_val = "**🔥 VALOR**" if val['es_value'] else ""
                                                clave_base = f"ap_{part['id']}_{text_m}_{opcion}"
                                                marcado = clave_base in st.session_state.claves_auto

                                                chk = st.checkbox(f"{opcion} ({cuota_m}) {lbl_val}", value=marcado, key=f"render_{clave_base}")
                                                st.markdown(f"<small>🏠 {m_info['max_bookies'][opcion]}<br>🎯 Prob: {round(val['prob_real'],1)}%</small>", unsafe_allow_html=True)

                                                if chk:
                                                    apuestas_seleccionadas.append({
                                                        "evento": f"{part['local']} vs {part['visitante']}",
                                                        "liga": part['liga_origen'], "mercado": text_m,
                                                        "seleccion": opcion, "cuota": cuota_m, "casa": m_info['max_bookies'][opcion]
                                                    })

        with col_der:
            st.subheader("🎟️ Configuración de Parlay")
            if apuestas_seleccionadas:
                cuota_acumulada = 1.0
                texto_whatsapp = "🚀 *TICKET PARLAY SUGERIDO DESDE RADAR GLOBAL* 🚀\n\n"
                with st.container(border=True, key="ticket_card"):
                    st.markdown("<div class='ticket-titulo'>🎟️ Boleto Parlay</div>", unsafe_allow_html=True)
                    for ap in apuestas_seleccionadas:
                        cuota_acumulada *= float(ap['cuota'])
                        st.markdown(f"<div class='ticket-item'>✔️ <b>{ap['evento']}</b><br>➔ <code>{ap['seleccion']}</code> | <span class='ticket-cuota-tag'>x{ap['cuota']}</span></div>", unsafe_allow_html=True)
                        texto_whatsapp += f"⚽ *{ap['evento']}*\n🎯 {ap['mercado']}: *{ap['seleccion']}* (x{ap['cuota']}) - 🏢 {ap['casa']}\n\n"

                    ganancia_neta = (cuota_acumulada * monto_inversion) - monto_inversion
                    st.metric("Cuota Final", f"x{round(cuota_acumulada, 2)}")
                    st.metric("Ganancia Neta", f"${round(ganancia_neta, 2)}")

                    if st.button("💾 Registrar en Bitácora", type="primary", use_container_width=True):
                        st.session_state.historial_apuestas.append({
                            "Fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                            "Detalles": f"{len(apuestas_seleccionadas)} combinadas",
                            "Market": "Multi-Mercado",
                            "Cuota": cuota_acumulada,
                            "Inversión": monto_inversion,
                            "Estado": "Pendiente",
                            "Ganancia Potencial": ganancia_neta
                        })
                        BitacoraManager.guardar(st.session_state.historial_apuestas)
                        st.toast("¡Guardado localmente!", icon="💾")
                        st.rerun()

                    msg_encoded = urllib.parse.quote(texto_whatsapp)
                    st.markdown(f'<a href="https://api.whatsapp.com/send?text={msg_encoded}" target="_blank" style="text-decoration:none;"><button style="border:none; background-color:#25D366; color:white; padding:10px; border-radius:8px; font-weight:bold; width:100%; cursor:pointer;">📲 Compartir en WhatsApp</button></a>', unsafe_allow_html=True)

                    if st.button("📤 Enviar a Telegram", use_container_width=True, key="telegram_btn"):
                        if enviar_telegram(texto_whatsapp):
                            st.toast("¡Enviado a Telegram!", icon="📤")

# =========================================================
# 9. PESTAÑA AUDITORÍA Y BITÁCORA
# =========================================================
with pestana_historial:
    st.title("📊 Módulo de Auditoría Financiera Avanzada")

    if st.session_state.historial_apuestas:
        df_act = pd.DataFrame(st.session_state.historial_apuestas)

        st.subheader("📝 Modificar Resultados Recientes")
        for idx, fila in df_act.iterrows():
            col_d, col_est = st.columns([3, 1])
            with col_d:
                st.write(f"🆔 **Ticket #{idx+1}** ({fila['Fecha']}) | Inversión: ${fila['Inversión']} | Cuota: x{round(fila['Cuota'],2)}")
            with col_est:
                opciones = ["Pendiente", "Ganado", "Perdido"]
                idx_est = opciones.index(fila['Estado']) if fila['Estado'] in opciones else 0
                nuevo = st.selectbox("Estado:", opciones, index=idx_est, key=f"est_{idx}")
                if nuevo != fila['Estado']:
                    st.session_state.historial_apuestas[idx]['Estado'] = nuevo
                    BitacoraManager.guardar(st.session_state.historial_apuestas)
                    st.rerun()

        total_inv = df_act['Inversión'].sum()
        ganados = df_act[df_act['Estado'] == "Ganado"]
        retorno = (ganados['Inversión'] * ganados['Cuota']).sum()
        neto = retorno - total_inv
        roi = (neto / total_inv * 100) if total_inv > 0 else 0
        terminados = df_act[df_act['Estado'] != "Pendiente"]
        acierto = (len(ganados) / len(terminados) * 100) if len(terminados) > 0 else 0

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.markdown(f'<div class="kpi-card"><div class="kpi-label">💵 Total Invertido</div><div class="kpi-value">${round(total_inv, 2)}</div></div>', unsafe_allow_html=True)
        kpi2.markdown(f'<div class="kpi-card"><div class="kpi-label">📈 Balance Neto</div><div class="kpi-value">${round(neto, 2)}</div><div class="kpi-sub">{round(roi, 2)}% ROI</div></div>', unsafe_allow_html=True)
        kpi3.markdown(f'<div class="kpi-card"><div class="kpi-label">🎯 Tasa Acierto</div><div class="kpi-value">{round(acierto, 1)}%</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.dataframe(df_act, use_container_width=True)

        col_d, col_b = st.columns([3, 1])
        col_d.download_button("📊 Descargar Bitácora (CSV)", data=df_act.to_csv(index=False).encode('utf-8'), file_name="Reporte_Apuestas.csv", mime='text/csv', use_container_width=True)
        if col_b.button("🗑️ Reiniciar Bitácora", use_container_width=True):
            BitacoraManager.limpiar()
            st.rerun()
    else:
        st.info("Aún no tienes apuestas registradas en la bitácora.")
