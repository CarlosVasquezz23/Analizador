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
# 3. VALIDADOR DE CORRELACIONES (CONFLICTOS DE APUESTAS)
# =========================================================
def detectar_correlaciones(apuestas: List[Dict[str, Any]]) -> List[str]:
    alertas = []
    eventos_map = {}
    for ap in apuestas:
        ev = ap['evento']
        eventos_map.setdefault(ev, []).append(ap)

    for ev, selecciones in eventos_map.items():
        if len(selecciones) > 1:
            mercados = [s['mercado'] for s in selecciones]
            opciones = [s['seleccion'] for s in selecciones]

            if mercados.count("1X2 (Ganador)") > 1:
                alertas.append(f"⚠️ **{ev}**: Selección contradictoria en el mercado 1X2 ({', '.join(opciones)}).")

            if "1X2 (Ganador)" in mercados and "Doble Oportunidad" in mercados:
                for s in selecciones:
                    if s['seleccion'] == "Local" and any(x in ["X2 (Visitante o Empate)"] for x in opciones):
                        alertas.append(f"⚠️ **{ev}**: Incompatibilidad entre Local y X2.")
                    elif s['seleccion'] == "Visitante" and any(x in ["1X (Local o Empate)"] for x in opciones):
                        alertas.append(f"⚠️ **{ev}**: Incompatibilidad entre Visitante y 1X.")

            if "Goles Más/Menos 2.5" in mercados and "Ambos Anotan (BTTS)" in mercados:
                if "Menos de 2.5" in opciones and "Sí" in opciones:
                    alertas.append(f"⚠️ **{ev}**: Conflicto de alta correlación negativa (Menos de 2.5 Goles y Ambos Anotan Sí).")

    return alertas

# =========================================================
# 4. TEMA VISUAL CSS
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

    div[data-testid="stAlert"] { border-radius: 12px !important; border: 1px solid var(--rg-border) !important; }
    div[data-testid="stExpander"] { border: 1px solid var(--rg-border) !important; border-radius: 12px !important; background: var(--rg-card-alt); overflow: hidden; }

    div[data-baseweb="select"] > div, div[data-baseweb="input"] > div, textarea {
        border-radius: 10px !important; background: var(--rg-card-alt) !important; border-color: var(--rg-border) !important;
    }

    .kpi-card {
        border-radius: 14px; padding: 16px 18px; border: 1px solid var(--rg-border);
        background: linear-gradient(160deg, var(--rg-card) 0%, #0f131b 100%); height: 100%;
    }
    .kpi-label { font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--rg-text-soft); margin-bottom: 6px; }
    .kpi-value { font-family: 'JetBrains Mono', monospace; font-size: 26px; font-weight: 700; line-height: 1.1; }
    .kpi-sub { font-size: 12.5px; margin-top: 4px; font-weight: 600; }
    </style>
""", unsafe_allow_html=True)

# =========================================================
# 5. METODOS DE CLIENTES API
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
    "🇪檐 La Liga (España)": "soccer_spain_la_liga",
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
# 6. INICIALIZACIÓN DE ESTADOS
# =========================================================
if 'historial_apuestas' not in st.session_state:
    st.session_state.historial_apuestas = BitacoraManager.cargar()
if 'version_ticket' not in st.session_state:
    st.session_state.version_ticket = 0
if 'datos_cargados' not in st.session_state:
    st.session_state.datos_cargados = {}
if 'datos_cargados_previos' not in st.session_state:
    st.session_state.datos_cargados_previos = {}
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
# 7. CONTROLES DEL SIDEBAR
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

    mercados_sels = st.multiselect("Mercados de Análisis:", list(diccionario_mercados.keys()), default=["1X2 (Ganador)"])
    tiempo_sel = st.selectbox("Rango Temporal:", ["24 Horas", "48 Horas", "72 Horas"], index=1)
    limite_h = int(tiempo_sel.split()[0])
    
    st.markdown("---")
    st.subheader("🧮 Gestión Financiera (Kelly)")
    bankroll_total = st.number_input("Banca Total ($):", min_value=10.0, value=200.0, step=10.0)
    fraccion_kelly = st.slider("Fracción de Kelly:", min_value=0.1, max_value=1.0, value=0.25, step=0.05, help="0.25 = Cuarto de Kelly (Conservador)")
    monto_inversion = st.number_input("Inversión Base ($):", min_value=1.0, value=10.0, step=1.0)

    consultar = st.button("🔍 Consultar Radar Múltiple", type="primary", use_container_width=True)
    
    st.markdown("---")
    st.subheader("🎯 Filtros Parlay Automático")
    perfil_estrategia = st.selectbox("Perfil de Estrategia:", ["📈 Mayor Probabilidad", "🛡️ Conservador (Favoritos)", "🔥 Cazador de Valor (+EV)", "⚖️ Equilibrado (Doble Oportunidad)"])
    num_eventos_auto = st.slider("Eventos:", min_value=2, max_value=6, value=3)
    rango_cuota_auto = st.slider("Rango de Cuota por Selección:", min_value=1.10, max_value=3.50, value=(1.25, 2.20), step=0.05)
    prob_min_auto = st.slider("Probabilidad Mínima (%):", min_value=40, max_value=90, value=55, step=5)
    generar_auto = st.button("🎲 ¡Pre-seleccionar Muestras!", use_container_width=True)

    st.markdown("---")
    with st.expander("🔔 Notificaciones por Telegram"):
        st.session_state['tg_token'] = st.text_input("Bot Token:", value=st.session_state.get('tg_token', ''), type="password")
        st.session_state['tg_chat_id'] = st.text_input("Chat ID:", value=st.session_state.get('tg_chat_id', ''))

# =========================================================
# 8. PROCESADORES Y CÁLCULOS MATEMÁTICOS DE CUOTAS
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
# 9. PESTAÑAS Y VISTA DE USUARIO
# =========================================================
pestana_radar, pestana_verificador, pestana_historial = st.tabs([
    "🚀 RADAR MULTI-MERCADO & VALUEBETS", 
    "🧮 CALCULADORA DE PROBABILIDAD (PARLAY EXTERNO)",
    "📊 BITÁCORA PRO & AUDITORÍA ROI"
])

# ---------------------------------------------------------
# PESTAÑA 1: RADAR AUTOMÁTICO
# ---------------------------------------------------------
with pestana_radar:
    st.title("⚽ Radar Avanzado Multi-Mercado Global")
    st.caption("Escaneo de cuotas en tiempo real · Value bets · Ticket parlay automático")

    if consultar:
        if (len(ligas_sels) > 0 or len(ligas_af_sels) > 0) and len(mercados_sels) > 0:
            st.cache_data.clear()
            consolidador = {}
            st.session_state.ha_consultado = True

            mercados_featured = [m for m in mercados_sels if diccionario_mercados[m] in ("h2h", "totals")]
            total_ligas = len(ligas_sels) + len(ligas_af_sels)

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

    dict_partidos = st.session_state.datos_cargados
    dict_previos = st.session_state.datos_cargados_previos

    if generar_auto and dict_partidos:
        opciones_todas = []
        for p_id, part in dict_partidos.items():
            for nombre_m, m_info in part['mercados'].items():
                for opcion, val_data in m_info['value_bets'].items():
                    cuota_op = m_info['max_cuotas'][opcion]
                    prob_op = val_data['prob_real']
                    ev_op = val_data['ev']
                    
                    cumple_perfil = True
                    if "Conservador" in perfil_estrategia: cumple_perfil = prob_op >= 65.0
                    elif "Value Hunter" in perfil_estrategia: cumple_perfil = ev_op > 0.02
                    elif "Equilibrado" in perfil_estrategia: cumple_perfil = nombre_m == "Doble Oportunidad"

                    if cumple_perfil and (rango_cuota_auto[0] <= cuota_op <= rango_cuota_auto[1]) and (prob_op >= prob_min_auto):
                        opciones_todas.append({
                            "partido_id": part['id'], "clave": f"ap_{part['id']}_{nombre_m}_{opcion}",
                            "prob_real": prob_op, "mercado": nombre_m, "seleccion": opcion
                        })
        
        opciones_todas = sorted(opciones_todas, key=lambda x: x['prob_real'], reverse=True)
        partidos_usados = set()
        mejores_opciones = []
        for op in opciones_todas:
            if op['partido_id'] not in partidos_usados:
                partidos_usados.add(op['partido_id'])
                mejores_opciones.append(op)
            if len(mejores_opciones) == num_eventos_auto: break
        
        if mejores_opciones:
            st.session_state.claves_auto = set([x['clave'] for x in mejores_opciones])
            st.session_state.version_ticket += 1
            st.success(f"🎯 Marcados automáticamente {len(mejores_opciones)} eventos.")

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

                                                chk = st.checkbox(f"{opcion} ({cuota_m}) {lbl_val}", value=marcado, key=f"render_{clave_base}_v{st.session_state.version_ticket}")
                                                st.markdown(f"<small>🏠 {m_info['max_bookies'][opcion]}<br>🎯 Prob: {round(val['prob_real'],1)}%</small>", unsafe_allow_html=True)

                                                with st.expander("🏬 Comparar Casas"):
                                                    todas_casas = m_info.get('todas_cuotas', {}).get(opcion, [])
                                                    if todas_casas:
                                                        df_casas = pd.DataFrame(todas_casas, columns=["Cuota", "Casa de Apuestas"]).sort_values("Cuota", ascending=False)
                                                        st.dataframe(df_casas, use_container_width=True, hide_index=True)

                                                if chk:
                                                    apuestas_seleccionadas.append({
                                                        "evento": f"{part['local']} vs {part['visitante']}",
                                                        "liga": part['liga_origen'], "mercado": text_m,
                                                        "seleccion": opcion, "cuota": cuota_m, "casa": m_info['max_bookies'][opcion],
                                                        "prob_real": val['prob_real']
                                                    })

        with col_der:
            st.subheader("🎟️ Configuración de Parlay")
            if apuestas_seleccionadas:
                alertas_correlacion = detectar_correlaciones(apuestas_seleccionadas)
                for al in alertas_correlacion:
                    st.error(al)

                cuota_acumulada = 1.0
                prob_combinada = 1.0
                texto_whatsapp = "🚀 *TICKET PARLAY SUGERIDO DESDE RADAR GLOBAL* 🚀\n\n"
                with st.container(border=True, key="ticket_card"):
                    st.markdown("<div class='ticket-titulo'>🎟️ Boleto Parlay</div>", unsafe_allow_html=True)
                    for ap in apuestas_seleccionadas:
                        cuota_acumulada *= float(ap['cuota'])
                        prob_combinada *= (float(ap['prob_real']) / 100.0)
                        st.markdown(f"<div class='ticket-item'>✔️ <b>{ap['evento']}</b><br>➔ <code>{ap['seleccion']}</code> | <span class='ticket-cuota-tag'>x{ap['cuota']}</span></div>", unsafe_allow_html=True)
                        texto_whatsapp += f"⚽ *{ap['evento']}*\n🎯 {ap['mercado']}: *{ap['seleccion']}* (x{ap['cuota']}) - 🏢 {ap['casa']}\n\n"

                    b = cuota_acumulada - 1.0
                    p = prob_combinada
                    q = 1.0 - p
                    f_kelly = ((b * p) - q) / b if b > 0 else 0
                    stake_kelly = max(0.0, f_kelly * fraccion_kelly * bankroll_total)

                    ganancia_neta = (cuota_acumulada * monto_inversion) - monto_inversion
                    st.metric("Cuota Final", f"x{round(cuota_acumulada, 2)}")
                    st.metric("Ganancia Neta Base", f"${round(ganancia_neta, 2)}")
                    st.metric("💡 Stake Kelly Sugerido", f"${round(stake_kelly, 2)}", help=f"Recomendación para tu Bankroll de ${bankroll_total}")

                    with st.expander("🛡️ Calculadora de Cobertura (Hedge)"):
                        st.caption("Usa esta herramienta si acertaste tus primeros eventos y deseas asegurar ganancias en el último partido.")
                        cuota_contra = st.number_input("Cuota Contra-opción último partido:", min_value=1.01, value=2.10, step=0.05)
                        retorno_potencial = monto_inversion * cuota_acumulada
                        stake_hedge = retorno_potencial / cuota_contra
                        ganancia_asegurada = retorno_potencial - monto_inversion - stake_hedge
                        st.info(f"👉 Apostar **${round(stake_hedge, 2)}** a la contraopción para **garantizar ${round(ganancia_asegurada, 2)} libres de riesgo**.")

                    if st.button("💾 Registrar en Bitácora", type="primary", use_container_width=True):
                        st.session_state.historial_apuestas.append({
                            "Fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                            "Detalles": f"{len(apuestas_seleccionadas)} combinadas",
                            "Liga": apuestas_seleccionadas[0]['liga'] if apuestas_seleccionadas else "Varias",
                            "Market": apuestas_seleccionadas[0]['mercado'] if len(apuestas_seleccionadas)==1 else "Multi-Mercado",
                            "Cuota": cuota_acumulada,
                            "Inversión": monto_inversion,
                            "Estado": "Pendiente",
                            "Ganancia Potencial": ganancia_neta
                        })
                        BitacoraManager.guardar(st.session_state.historial_apuestas)
                        st.toast("¡Guardado localmente!", icon="💾")
                        st.rerun()

                    html_ticket = f"""
                    <div style="font-family: Arial; border:2px solid #00d2d3; padding:15px; border-radius:10px; background-color:#12161f; color:white;">
                        <h3 style="color:#00d2d3; text-align:center;">🎟️ BOLETO DE APUESTAS PARLAY</h3>
                        <p><b>Fecha:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p><hr>
                    """
                    for ap in apuestas_seleccionadas:
                        html_ticket += f"<p>⚽ <b>{ap['evento']}</b><br>🎯 {ap['mercado']} - {ap['seleccion']} (x{ap['cuota']})</p>"
                    html_ticket += f"<hr><h4>Cuota Total: x{round(cuota_acumulada,2)} | Inversión: ${monto_inversion}</h4></div>"
                    
                    st.download_button(
                        "📄 Descargar Boleto HTML/PDF",
                        data=html_ticket,
                        file_name=f"Boleto_Parlay_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                        mime="text/html",
                        use_container_width=True
                    )

                    msg_encoded = urllib.parse.quote(texto_whatsapp)
                    st.markdown(f'<a href="https://api.whatsapp.com/send?text={msg_encoded}" target="_blank" style="text-decoration:none;"><button style="border:none; background-color:#25D366; color:white; padding:10px; border-radius:8px; font-weight:bold; width:100%; cursor:pointer;">📲 Compartir en WhatsApp</button></a>', unsafe_allow_html=True)

                    if st.button("📤 Enviar a Telegram", use_container_width=True, key="telegram_btn"):
                        if enviar_telegram(texto_whatsapp):
                            st.toast("¡Enviado a Telegram!", icon="📤")

# ---------------------------------------------------------
# PESTAÑA 2: CALCULADORA DE PARLAY EXTERNO (CORREGIDA)
# ---------------------------------------------------------
with pestana_verificador:
    st.title("🧮 Analizador & Verificador de Parlays Externos")
    st.caption("Ingresa los eventos y cuotas que armaste en cualquier casa de apuestas (Betano, Bet365, Ecuabet, etc.) para calcular su probabilidad real matemática.")

    col_ingreso, col_resultados = st.columns([1.1, 1])

    with col_ingreso:
        st.subheader("📌 Armar / Pegar Selecciones")
        num_partidos_ext = st.number_input("Número de Partidos en tu Ticket:", min_value=1, max_value=10, value=3, step=1)
        margen_estimado_casa = st.slider("Comisión/Margen estimado de la casa (%):", min_value=1.0, max_value=15.0, value=5.0, step=0.5, help="La mayoría de casas cobran entre 4% y 7% de margen sobre las cuotas.")

        partidos_externos = []
        for i in range(int(num_partidos_ext)):
            with st.expander(f"⚽ Selección #{i+1}", expanded=True):
                col1, col2 = st.columns([2, 1])
                with col1:
                    nombre_partido = st.text_input(f"Partido/Selección #{i+1}:", f"Evento #{i+1}", key=f"ext_name_{i}")
                with col2:
                    cuota_partido = st.number_input(f"Cuota:", min_value=1.01, value=1.50, step=0.05, key=f"ext_odd_{i}")
                partidos_externos.append({"nombre": nombre_partido, "cuota": cuota_partido})

    with col_resultados:
        st.subheader("📊 Análisis Matemático del Ticket")

        cuota_total_ext = 1.0
        prob_real_total = 1.0
        factor_comision = 1.0 + (margen_estimado_casa / 100.0)

        detalles_tabla = []

        for p in partidos_externos:
            c = float(p['cuota'])
            cuota_total_ext *= c
            
            # 1. Probabilidad implícita que incluye el margen de la casa
            prob_implicita_casa = (1.0 / c)
            
            # 2. Probabilidad Real Fair (revolviendo matemáticamente la comisión)
            prob_real_evento = prob_implicita_casa / factor_comision
            prob_real_total *= prob_real_evento

            detalles_tabla.append({
                "Selección": p['nombre'],
                "Cuota Casa": f"x{round(c, 2)}",
                "Prob. Implícita Casa": f"{round(prob_implicita_casa * 100, 1)}%",
                "Prob. Real Estimada": f"{round(prob_real_evento * 100, 1)}%"
            })

        prob_porcentaje = prob_real_total * 100.0
        cuota_justa = (1.0 / prob_real_total) if prob_real_total > 0 else 0
        ev_ticket = (cuota_total_ext * prob_real_total) - 1.0

        st.markdown(f"""
            <div class="kpi-card" style="border-left: 5px solid #00d2d3; margin-bottom: 15px;">
                <div class="kpi-label">🎯 PROBABILIDAD REAL DE ACERTAR ESTE PARLAY</div>
                <div class="kpi-value" style="color: #00d2d3; font-size: 34px;">{round(prob_porcentaje, 2)}%</div>
                <div class="kpi-sub" style="color:#a4b0be;">Equivale a acertar 1 de cada {round(100/prob_porcentaje, 1) if prob_porcentaje > 0 else 0} intentos</div>
            </div>
        """, unsafe_allow_html=True)

        k1, k2 = st.columns(2)
        k1.metric("Cuota Total de la Casa", f"x{round(cuota_total_ext, 2)}")
        k2.metric("Cuota Justa Sin Margen", f"x{round(cuota_justa, 2)}", help="La cuota que verdaderamente debería pagar el parlay si la casa no cobrara comisión.")

        if ev_ticket > 0.01:
            st.success(f"🔥 **TICKET CON VALOR ESPERADO POSITIVO (+EV: {round(ev_ticket*100, 1)}%)**: Las cuotas de tu ticket valen la pena.")
        elif ev_ticket >= -0.05:
            st.info(f"⚖️ **TICKET EQUILIBRADO (EV: {round(ev_ticket*100, 1)}%)**: El cobro de comisión se mantiene dentro del rango estándar de la casa.")
        else:
            st.warning(f"⚠️ **TICKET DESFAVORABLE (EV: {round(ev_ticket*100, 1)}%)**: La casa está aplicando un margen de comisión muy desfavorable sobre tu combinado.")

        st.write("**Desglose Selección por Selección:**")
        st.dataframe(pd.DataFrame(detalles_tabla), use_container_width=True, hide_index=True)

# ---------------------------------------------------------
# PESTAÑA 3: AUDITORÍA Y BITÁCORA
# ---------------------------------------------------------
with pestana_historial:
    st.title("📊 Módulo de Auditoría Financiera Avanzada")

    with st.expander("🗄️ Copias de Seguridad (Backup & Restore JSON)"):
        col_exp_j, col_imp_j = st.columns(2)
        with col_exp_j:
            json_data = json.dumps(st.session_state.historial_apuestas, ensure_ascii=False, indent=4)
            st.download_button("📥 Descargar Respaldo JSON", data=json_data, file_name="bitacora_backup.json", mime="application/json", use_container_width=True)
        with col_imp_j:
            uploaded_json = st.file_uploader("📤 Restaurar desde JSON", type=["json"])
            if uploaded_json is not None:
                try:
                    data_restaurada = json.load(uploaded_json)
                    st.session_state.historial_apuestas = data_restaurada
                    BitacoraManager.guardar(data_restaurada)
                    st.success("✅ ¡Bitácora restaurada exitosamente!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al procesar archivo JSON: {e}")

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
        
        st.subheader("📈 Curva de Crecimiento de Patrimonio")
        balance_acumulado = []
        cabal = 0.0
        for _, r in df_act.iterrows():
            if r['Estado'] == "Ganado":
                cabal += (r['Inversión'] * r['Cuota']) - r['Inversión']
            elif r['Estado'] == "Perdido":
                cabal -= r['Inversión']
            balance_acumulado.append(cabal)
        
        df_act['Balance Acumulado ($)'] = balance_acumulado
        st.line_chart(df_act['Balance Acumulado ($)'], use_container_width=True)

        st.subheader("📊 Análisis de Rendimiento por Liga y Mercado")
        col_an_liga, col_an_mercado = st.columns(2)
        with col_an_liga:
            if "Liga" in df_act.columns:
                df_liga = df_act.groupby("Liga")['Inversión'].count().reset_index().rename(columns={"Inversión": "Total Apuestas"})
                st.write("**Apuestas Totales por Liga:**")
                st.bar_chart(df_liga.set_index("Liga"))
        with col_an_mercado:
            if "Market" in df_act.columns:
                df_market = df_act.groupby("Market")['Inversión'].count().reset_index().rename(columns={"Inversión": "Total Apuestas"})
                st.write("**Apuestas Totales por Mercado:**")
                st.bar_chart(df_market.set_index("Market"))

        st.dataframe(df_act, use_container_width=True)

        col_d, col_b = st.columns([3, 1])
        col_d.download_button("📊 Descargar Bitácora (CSV)", data=df_act.to_csv(index=False).encode('utf-8'), file_name="Reporte_Apuestas.csv", mime='text/csv', use_container_width=True)
        if col_b.button("🗑️ Reiniciar Bitácora", use_container_width=True):
            BitacoraManager.limpiar()
            st.rerun()
    else:
        st.info("Aún no tienes apuestas registradas en la bitácora.")
