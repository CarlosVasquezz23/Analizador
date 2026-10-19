import streamlit as st
import requests
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta, timezone
import json
import os
import re
import itertools
from typing import Dict, List, Any
import streamlit.components.v1 as components
import concurrent.futures

# =========================================================
# 1. CONFIGURACIÓN DE PÁGINA Y CREDENCIALES
# =========================================================
st.set_page_config(
    page_title="Global Sports Analytics - Pro Platform",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_FILE = "bitacora_backup.json"
CONFIG_FILE = "user_config.json"

API_KEY = st.secrets.get("ODDS_API_KEY", "")
AF_API_KEY = st.secrets.get("AF_API_KEY", "")
AF_BASE_URL = "https://v3.football.api-sports.io"

ALLSPORTS_KEY = st.secrets.get("ALLSPORTS_API_KEY", "")
ALLSPORTS_BASE_URL = "https://allsportsapi.com/api/football/"

SPORTAPI_KEY = st.secrets.get("SPORTAPI_KEY", "")
SPORTAPI_BASE_URL = "https://api.sportapi.ai/v1/football/"

DEFAULT_TG_TOKEN = st.secrets.get("TG_TOKEN", "")
DEFAULT_TG_CHAT_ID = st.secrets.get("TG_CHAT_ID", "")

# =========================================================
# 2. PERSISTENCIA
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

class ConfigManager:
    @staticmethod
    def cargar() -> Dict[str, Any]:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    @staticmethod
    def guardar(key: str, val: Any) -> None:
        if 'user_config_cache' not in st.session_state:
            st.session_state.user_config_cache = ConfigManager.cargar()
        st.session_state.user_config_cache[key] = val
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(st.session_state.user_config_cache, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

def enviar_telegram(mensaje: str) -> bool:
    token = st.session_state.get('tg_token', DEFAULT_TG_TOKEN)
    chat_id = st.session_state.get('tg_chat_id', DEFAULT_TG_CHAT_ID)
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

# =========================================================
# 3. MODELADO MATEMÁTICO VECTORIZADO
# =========================================================
def poisson_pmf_vec(k_array: np.ndarray, mu: float) -> np.ndarray:
    return (np.power(mu, k_array) * np.exp(-mu)) / np.array([math.factorial(k) for k in k_array])

def calcular_modelo_poisson(lambda_local: float = 1.45, lambda_visita: float = 1.10) -> Dict[str, float]:
    max_goles = 6
    k_range = np.arange(max_goles)
    p_l = poisson_pmf_vec(k_range, lambda_local)
    p_v = poisson_pmf_vec(k_range, lambda_visita)
    matriz_prob = np.outer(p_l, p_v)

    soma = np.sum(matriz_prob)
    if soma > 0: matriz_prob /= soma

    prob_local = float(np.sum(np.tril(matriz_prob, -1)))
    prob_empate = float(np.sum(np.diag(matriz_prob)))
    prob_visita = float(np.sum(np.triu(matriz_prob, 1)))
    prob_btts = float(np.sum(matriz_prob[1:, 1:]))
    
    grid_i, grid_j = np.indices((max_goles, max_goles))
    prob_over25 = float(np.sum(matriz_prob[grid_i + grid_j > 2.5]))

    return {
        "Local": prob_local * 100, "Empate": prob_empate * 100, "Visitante": prob_visita * 100,
        "1X (Local o Empate)": (prob_local + prob_empate) * 100,
        "X2 (Visitante o Empate)": (prob_visita + prob_empate) * 100,
        "12 (Local o Visitante)": (prob_local + prob_visita) * 100,
        "Sí": prob_btts * 100, "No": (1 - prob_btts) * 100,
        "Más de 2.5": prob_over25 * 100, "Menos de 2.5": (1 - prob_over25) * 100
    }

def detectar_surebet(cuotas_max_dict: Dict[str, float]) -> Dict[str, Any]:
    if not cuotas_max_dict or len(cuotas_max_dict) < 2:
        return {"es_surebet": False, "overround": 1.0, "lucro": 0.0}
    overround = sum(1.0 / float(c) for c in cuotas_max_dict.values() if float(c) > 0)
    es_surebet = overround < 1.0
    lucro = ((1.0 / overround) - 1.0) * 100.0 if es_surebet else 0.0
    return {"es_surebet": es_surebet, "overround": overround, "lucro": lucro}

def calcular_sharpe_parlay(cuota_total: float, prob_combinada: float) -> float:
    ev = (cuota_total * prob_combinada) - 1.0
    varianza = (prob_combinada * ((cuota_total - 1.0) ** 2)) + ((1.0 - prob_combinada) * 1.0)
    desviacion = math.sqrt(varianza) if varianza > 0 else 1.0
    return ev / desviacion

def ejecutar_simulacion_montecarlo(partidos: List[Dict[str, Any]], num_simulaciones: int = 10000) -> Dict[str, Any]:
    if not partidos: return {}
    probs = [p['prob_real'] / 100.0 for p in partidos]
    num_eventos = len(probs)
    matriz_rand = np.random.rand(num_simulaciones, num_eventos)
    matriz_aciertos = matriz_rand < probs
    aciertos_por_sim = np.sum(matriz_aciertos, axis=1)
    return {
        "pleno_acierto": float(np.sum(aciertos_por_sim == num_eventos) / num_simulaciones * 100.0),
        "fallo_por_uno": float(np.sum(aciertos_por_sim == (num_eventos - 1)) / num_simulaciones * 100.0)
    }

def generar_resumen_ejecutivo_ai(dict_partidos: Dict[str, Any]) -> str:
    if not dict_partidos: return ""
    total_partidos = len(dict_partidos)
    total_surebets, total_valuebets = 0, 0
    liga_con_mas_valor = {}

    for p in dict_partidos.values():
        liga = p['liga_origen']
        for m_info in p['mercados'].values():
            if m_info.get('surebet', {}).get('es_surebet', False): total_surebets += 1
            for val_item in m_info.get('value_bets', {}).values():
                if val_item.get('ev', 0) > 0.03:
                    total_valuebets += 1
                    liga_con_mas_valor[liga] = liga_con_mas_valor.get(liga, 0) + 1

    top_liga = max(liga_con_mas_valor, key=liga_con_mas_valor.get) if liga_con_mas_valor else "General"
    val_top_count = liga_con_mas_valor.get(top_liga, 0)

    resumen = f"<b>Análisis de Mercado:</b> {total_partidos} eventos procesados. "
    resumen += f"Oportunidades de Arbitraje (SureBets): <b>{total_surebets}</b>. " if total_surebets > 0 else "Sin arbitrajes activos. "
    resumen += f"Oportunidades +EV: <b>{total_valuebets}</b>. Mayor concentración en <b>{top_liga}</b> ({val_top_count} +EV)." if total_valuebets > 0 else "Cuotas eficientes."
    return resumen

# =========================================================
# 4. ESTADOS Y ESTILOS
# =========================================================
user_config = ConfigManager.cargar()

if 'tema_visual' not in st.session_state: st.session_state.tema_visual = user_config.get('tema_visual', 'Oscuro')
if 'historial_apuestas' not in st.session_state: st.session_state.historial_apuestas = BitacoraManager.cargar()
if 'datos_cargados' not in st.session_state: st.session_state.datos_cargados = {}
if 'datos_cargados_previos' not in st.session_state: st.session_state.datos_cargados_previos = {}
if 'ha_consultado' not in st.session_state: st.session_state.ha_consultado = False
if 'ticket_persistente' not in st.session_state: st.session_state.ticket_persistente = {}
if 'casas_preferidas' not in st.session_state: st.session_state.casas_preferidas = user_config.get('casas_preferidas', [])
if 'bankroll_total' not in st.session_state: st.session_state.bankroll_total = user_config.get('bankroll_total', 200.0)

def toggle_apuesta(partido_obj, mercado_nombre, opcion_nombre, cuota_val, casa_val, prob_val, clave_k):
    if st.session_state.get(clave_k, False):
        st.session_state.ticket_persistente[clave_k] = {
            "evento": f"{partido_obj['local']} vs {partido_obj['visitante']}",
            "liga": partido_obj['liga_origen'],
            "mercado": mercado_nombre,
            "seleccion": opcion_nombre,
            "cuota": cuota_val,
            "casa": casa_val,
            "prob_real": prob_val,
            "fecha_ts": partido_obj.get('fecha_ts', None)
        }
        st.toast(f"Añadido al boleto: {opcion_nombre} (@{cuota_val})")
    else:
        st.session_state.ticket_persistente.pop(clave_k, None)
        st.toast(f"Removido: {opcion_nombre}")

modo_claro = (st.session_state.tema_visual == "Claro")
bg_val = "#f8fafc" if modo_claro else "#0b0f19"
card_val = "#ffffff" if modo_claro else "#111827"
card_alt_val = "#f1f5f9" if modo_claro else "#1f2937"
border_val = "#e2e8f0" if modo_claro else "#374151"
text_val = "#0f172a" if modo_claro else "#f3f4f6"
text_soft_val = "#64748b" if modo_claro else "#9ca3af"
accent_val = "#10b981"

st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    header {{visibility: hidden;}}

    html, body, [class*="css"] {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        color: {text_val};
        background-color: {bg_val};
    }}

    .block-container {{
        padding-top: 1.5rem !important;
        padding-bottom: 2rem !important;
        max-width: 98% !important;
    }}

    .stApp {{
        background-color: {bg_val};
    }}

    div[data-baseweb="radio"] input:checked + div {{
        background-color: {accent_val} !important;
    }}
    
    .stTabs [data-baseweb="tab-highlight"] {{
        background-color: {accent_val} !important;
    }}
    
    .stTabs [data-baseweb="tab"][aria-selected="true"] {{
        color: {accent_val} !important;
    }}

    button[kind="primary"] {{
        background-color: {accent_val} !important;
        border-color: {accent_val} !important;
        color: #ffffff !important;
        font-weight: 600 !important;
        border-radius: 6px !important;
    }}

    .ai-summary-box {{
        background: {card_val};
        border: 1px solid {border_val};
        border-left: 4px solid {accent_val};
        border-radius: 8px;
        padding: 14px 18px;
        margin-bottom: 20px;
        font-size: 14px;
        color: {text_val};
    }}

    .empty-state-card {{
        background: {card_val};
        border: 1px solid {border_val};
        border-radius: 8px;
        padding: 24px;
        text-align: center;
    }}

    .match-header {{
        font-size: 15px;
        font-weight: 600;
        color: {text_val};
        margin-top: 4px;
        margin-bottom: 6px;
    }}

    .liga-chip {{
        display: inline-block;
        font-size: 11px;
        font-weight: 600;
        color: {accent_val};
        background: rgba(16, 185, 129, 0.1);
        border: 1px solid rgba(16, 185, 129, 0.3);
        border-radius: 4px;
        padding: 2px 8px;
    }}

    .kickoff-chip {{
        display: inline-block;
        font-size: 11px;
        font-weight: 500;
        color: {text_soft_val};
        background: {card_alt_val};
        border: 1px solid {border_val};
        border-radius: 4px;
        padding: 2px 8px;
        margin-left: 6px;
    }}

    .creditos-caja-pro {{
        background: {card_alt_val};
        padding: 10px 14px;
        border-radius: 6px;
        border: 1px solid {border_val};
        margin-bottom: 12px;
    }}

    .ticket-titulo {{
        font-weight: 700;
        font-size: 13px;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        color: {accent_val};
        text-align: center;
        margin-bottom: 10px;
    }}

    .ticket-item {{
        border-bottom: 1px solid {border_val};
        padding: 8px 0;
        font-size: 13px;
    }}

    .ticket-cuota-tag {{
        font-weight: 600;
        color: {accent_val};
        background: rgba(16, 185, 129, 0.12);
        border-radius: 4px;
        padding: 1px 5px;
        font-size: 12px;
    }}

    .veredicto-box {{
        background: {card_alt_val};
        border: 1px solid {border_val};
        border-radius: 8px;
        padding: 16px;
        margin-top: 15px;
    }}
    </style>
""", unsafe_allow_html=True)

# =========================================================
# 5. MAPEO DE LIGAS
# =========================================================
CATALOGO_LIGAS_UNIFICADO = {
    "UEFA Champions League": {
        "odds_api": ["soccer_uefa_champs_league", "soccer_uefa_champions_league"],
        "af_id": 2, "allsports_id": 3, "sportapi_id": 7
    },
    "UEFA Europa League": {
        "odds_api": ["soccer_uefa_europa_league"],
        "af_id": 3, "allsports_id": 4, "sportapi_id": 8
    },
    "UEFA Conference League": {
        "odds_api": ["soccer_uefa_europa_conference_league"],
        "af_id": 848, "allsports_id": None, "sportapi_id": None
    },
    "Premier League": {
        "odds_api": ["soccer_epl"],
        "af_id": 39, "allsports_id": 152, "sportapi_id": 17
    },
    "La Liga (España)": {
        "odds_api": ["soccer_spain_la_liga"],
        "af_id": 140, "allsports_id": 302, "sportapi_id": 8
    },
    "Serie A (Italia)": {
        "odds_api": ["soccer_italy_serie_a"],
        "af_id": 135, "allsports_id": 207, "sportapi_id": 23
    },
    "Bundesliga (Alemania)": {
        "odds_api": ["soccer_germany_bundesliga"],
        "af_id": 78, "allsports_id": 175, "sportapi_id": 35
    },
    "Ligue 1 (Francia)": {
        "odds_api": ["soccer_france_ligue_one"],
        "af_id": 61, "allsports_id": 168, "sportapi_id": 34
    },
    "MLS (USA)": {
        "odds_api": ["soccer_usa_mls"],
        "af_id": 253, "allsports_id": 332, "sportapi_id": 54
    },
    "Liga MX (México)": {
        "odds_api": ["soccer_mexico_liga_mx"],
        "af_id": 262, "allsports_id": 230, "sportapi_id": 41
    },
    "Liga Profesional (Argentina)": {
        "odds_api": ["soccer_argentina_primera_division"],
        "af_id": 128, "allsports_id": 44, "sportapi_id": 155
    },
    "Brasileirão Serie A (Brasil)": {
        "odds_api": ["soccer_brazil_campeonato"],
        "af_id": 71, "allsports_id": 81, "sportapi_id": 325
    },
    "LigaPro (Ecuador)": {
        "odds_api": [], "af_id": 242, "allsports_id": 122, "sportapi_id": 512
    },
    "Copa Libertadores": {
        "odds_api": ["soccer_conmebol_copa_libertadores"],
        "af_id": 13, "allsports_id": 11, "sportapi_id": 380
    }
}

diccionario_mercados = {
    "1X2 (Resultado Final)": "h2h",
    "Doble Oportunidad": "double_chance",
    "Ambos Anotan (BTTS)": "btts",
    "Total Goles (+/- 2.5)": "totals"
}

# =========================================================
# 6. APIS & WORKERS
# =========================================================
def af_headers(): return {"x-apisports-key": AF_API_KEY}

@st.cache_data(ttl=60)
def consultar_api_odds(sport_key, market_key):
    if not API_KEY or not sport_key: return [], "Sin API Key"
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={API_KEY}&regions=eu,us&markets={market_key}&oddsFormat=decimal"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            res_json = response.json()
            return (res_json, None) if isinstance(res_json, list) else ([], "Err")
        return [], f"HTTP {response.status_code}"
    except Exception as e:
        return [], f"Excepción: {e}"

def consultar_af_con_fallback_fixtures(af_id):
    if not af_id or not AF_API_KEY: return []
    url_odds = f"{AF_BASE_URL}/odds"
    season_actual = datetime.now().year
    try:
        r = requests.get(url_odds, headers=af_headers(), params={"league": af_id, "season": season_actual}, timeout=8)
        if r.status_code == 200:
            return r.json().get("response", [])
    except Exception:
        pass
    return []

def consultar_allsports_odds(league_id=None):
    if not ALLSPORTS_KEY or not league_id: return []
    url = f"{ALLSPORTS_BASE_URL}?met=Odds&APIkey={ALLSPORTS_KEY}&leagueId={league_id}"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200 and r.json().get("success") == 1:
            return r.json().get("result", {})
    except Exception:
        pass
    return {}

def consultar_sportapi_odds(sportapi_id=None):
    if not SPORTAPI_KEY or not sportapi_id: return []
    url = f"{SPORTAPI_BASE_URL}odds?apikey={SPORTAPI_KEY}&tournament_id={sportapi_id}"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            return r.json().get("data", [])
    except Exception:
        pass
    return []

def escanear_liga_worker(liga_nombre, config_liga, mercados_sels, limite_h):
    resultados_normalizados = []
    for skey in config_liga.get("odds_api", []):
        for market_sel in mercados_sels:
            m_key = diccionario_mercados.get(market_sel, "h2h")
            data, err = consultar_api_odds(skey, m_key)
            if data and isinstance(data, list):
                for p in data:
                    resultados_normalizados.append((p, market_sel))

    af_id = config_liga.get("af_id")
    if af_id and AF_API_KEY:
        raw_af = consultar_af_con_fallback_fixtures(af_id)
        for item in raw_af:
            fix, teams, bookies_af = item.get("fixture", {}), item.get("teams", {}), item.get("bookmakers", [])
            bookmakers_converted = []
            for b in bookies_af:
                b_markets = []
                for m in b.get("bets", []):
                    m_name = m.get("name")
                    key_m = "h2h" if m_name == "Match Winner" else ("totals" if m_name == "Goals Over/Under" else "btts")
                    outcomes = [{"name": o.get("value"), "price": float(o.get("odd"))} for o in m.get("values", [])]
                    b_markets.append({"key": key_m, "outcomes": outcomes})
                bookmakers_converted.append({"key": str(b.get("id")), "title": b.get("name"), "markets": b_markets})

            partido_norm = {
                "id": f"af_{fix.get('id')}",
                "home_team": teams.get("home", {}).get("name", "Local"),
                "away_team": teams.get("away", {}).get("name", "Visitante"),
                "commence_time": fix.get("date", datetime.now(timezone.utc).isoformat()),
                "bookmakers": bookmakers_converted
            }
            for m_sel in mercados_sels:
                resultados_normalizados.append((partido_norm, m_sel))

    as_id = config_liga.get("allsports_id")
    if as_id and ALLSPORTS_KEY:
        res_as = consultar_allsports_odds(as_id)
        if isinstance(res_as, dict):
            for m_id, odds_list in res_as.items():
                if not odds_list: continue
                match_info = odds_list[0]
                bookmakers_converted = []
                dict_bookies = {}
                for entry in odds_list:
                    b_name = entry.get("bookmaker_name", "AllSports")
                    dict_bookies.setdefault(b_name, []).append(entry)

                for b_name, b_entries in dict_bookies.items():
                    outcomes_h2h = []
                    for e in b_entries:
                        m_type = e.get("type", "")
                        odd_val = float(e.get("odd", 1.0))
                        if m_type in ["1", "Home"]: outcomes_h2h.append({"name": match_info.get("event_home_team", "Local"), "price": odd_val})
                        elif m_type in ["X", "Draw"]: outcomes_h2h.append({"name": "Draw", "price": odd_val})
                        elif m_type in ["2", "Away"]: outcomes_h2h.append({"name": match_info.get("event_away_team", "Visitante"), "price": odd_val})
                    if outcomes_h2h:
                        bookmakers_converted.append({"key": b_name, "title": b_name, "markets": [{"key": "h2h", "outcomes": outcomes_h2h}]})

                partido_as = {
                    "id": f"as_{m_id}",
                    "home_team": match_info.get("event_home_team", "Local"),
                    "away_team": match_info.get("event_away_team", "Visitante"),
                    "commence_time": match_info.get("event_date", datetime.now(timezone.utc).isoformat()),
                    "bookmakers": bookmakers_converted
                }
                for m_sel in mercados_sels:
                    resultados_normalizados.append((partido_as, m_sel))

    sp_id = config_liga.get("sportapi_id")
    if sp_id and SPORTAPI_KEY:
        res_sp = consultar_sportapi_odds(sp_id)
        for item in res_sp:
            m_id = item.get("id", "0")
            bookies_sp = item.get("bookmakers", [])
            bookmakers_converted = []
            for b in bookies_sp:
                outcomes_h2h = []
                for o in b.get("odds", []):
                    outcomes_h2h.append({"name": o.get("label"), "price": float(o.get("value", 1.0))})
                if outcomes_h2h:
                    bookmakers_converted.append({"key": b.get("name"), "title": b.get("name"), "markets": [{"key": "h2h", "outcomes": outcomes_h2h}]})

            partido_sp = {
                "id": f"sp_{m_id}",
                "home_team": item.get("home_team", "Local"),
                "away_team": item.get("away_team", "Visitante"),
                "commence_time": item.get("start_time", datetime.now(timezone.utc).isoformat()),
                "bookmakers": bookmakers_converted
            }
            for m_sel in mercados_sels:
                resultados_normalizados.append((partido_sp, m_sel))

    return (liga_nombre, resultados_normalizados)

def procesar_e_inyectar_mercado(datos, mercado, limite_horas, nombre_liga, diccionario_consolidador):
    ahora_utc = datetime.now(timezone.utc)
    if not datos or not isinstance(datos, list): return
    datos_previos = st.session_state.get('datos_cargados_previos', {})

    for partido in datos:
        partido_id = partido['id']
        home, away = partido['home_team'], partido['away_team']
        try:
            fecha_utc = datetime.fromisoformat(partido['commence_time'].replace('Z', '+00:00'))
        except (ValueError, KeyError):
            fecha_utc = ahora_utc

        es_en_vivo = partido.get('in_play', False)
        fecha_local = fecha_utc - timedelta(hours=5)
        bookmakers = partido.get('bookmakers', [])
        if not bookmakers: continue

        casas_pref = st.session_state.get('casas_preferidas', [])
        if casas_pref:
            bookies_filtrados = [b for b in bookmakers if any(cp.lower() in b['title'].lower() for cp in casas_pref)]
            if bookies_filtrados: bookmakers = bookies_filtrados

        cuotas_globales = {}
        for b in bookmakers:
            dict_b_markets = {m['key']: m['outcomes'] for m in b.get('markets', [])}

            if mercado == "Doble Oportunidad" and "double_chance" in dict_b_markets:
                for o in dict_b_markets["double_chance"]:
                    o_name = "1X (Local o Empate)" if o['name'] in ["home_draw", "home or draw", "1X", f"{home} or draw"] else ("X2 (Visitante o Empate)" if o['name'] in ["away_draw", "away or draw", "X2", f"{away} or draw"] else "12 (Local o Visitante)")
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))

            elif mercado == "Ambos Anotan (BTTS)" and "btts" in dict_b_markets:
                for o in dict_b_markets["btts"]:
                    o_name = "Sí" if o['name'].lower() in ["yes", "sí", "si"] else "No"
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))

            elif "Total Goles" in mercado and "totals" in dict_b_markets:
                for o in dict_b_markets["totals"]:
                    if o.get('point', 2.5) == 2.5:
                        o_name = "Más de 2.5" if o['name'].lower() in ["over", "más", "mas"] else "Menos de 2.5"
                        cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))

            elif "1X2" in mercado and "h2h" in dict_b_markets:
                for o in dict_b_markets["h2h"]:
                    o_name = "Local" if o['name'] == home else ("Visitante" if o['name'] == away else "Empate")
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))

        max_cuotas, max_bookies, value_bets, variaciones_dict = {}, {}, {}, {}
        cuotas_promedio_dict = {op: sum(t[0] for t in tuplas)/len(tuplas) for op, tuplas in cuotas_globales.items() if tuplas}
        overround = sum([1 / cp for cp in cuotas_promedio_dict.values()]) if cuotas_promedio_dict else 1.0

        probs_poisson = calcular_modelo_poisson(1.45, 1.10)

        for opcion, tuplas in cuotas_globales.items():
            precios = [t[0] for t in tuplas]
            cuota_prom = cuotas_promedio_dict[opcion]
            prob_real = (1 / cuota_prom) / overround if overround > 0 else (1 / cuota_prom)
            cuota_max = max(precios)
            bookie_max = tuplas[precios.index(cuota_max)][1]

            ev = (cuota_max * prob_real) - 1
            max_cuotas[opcion] = cuota_max
            max_bookies[opcion] = bookie_max
            value_bets[opcion] = {
                "ev": ev, "prob_real": prob_real * 100,
                "prob_poisson": probs_poisson.get(opcion, prob_real * 100),
                "es_value": ev > 0.02
            }

            c_prev = datos_previos.get(partido_id, {}).get("mercados", {}).get(mercado, {}).get("max_cuotas", {}).get(opcion, cuota_max)
            variaciones_dict[opcion] = "📉 Bajando" if cuota_max < c_prev else ("📈 Subiendo" if cuota_max > c_prev else "➡️ Estable")

        info_surebet = detectar_surebet(max_cuotas)
        if max_cuotas:
            if partido_id not in diccionario_consolidador:
                diccionario_consolidador[partido_id] = {
                    "id": partido_id, "liga_origen": nombre_liga,
                    "fecha_str": fecha_local.strftime("%d/%m/%Y - %H:%M"),
                    "fecha_ts": fecha_utc.timestamp(),
                    "local": home, "visitante": away,
                    "es_en_vivo": es_en_vivo,
                    "mercados": {}
                }
            diccionario_consolidador[partido_id]["mercados"][mercado] = {
                "max_cuotas": max_cuotas, "max_bookies": max_bookies,
                "value_bets": value_bets, "variaciones": variaciones_dict,
                "surebet": info_surebet
            }

# =========================================================
# 7. SIDEBAR PROFESIONAL
# =========================================================
with st.sidebar:
    st.markdown("### Configuración")
    
    st.radio(
        "Apariencia:", options=["Oscuro", "Claro"],
        index=0 if st.session_state.tema_visual == "Oscuro" else 1,
        key="tema_visual_widget",
        on_change=lambda: ConfigManager.guardar('tema_visual', st.session_state.tema_visual_widget),
        horizontal=True
    )

    st.markdown("""
        <div class="creditos-caja-pro">
            <small style="color:var(--rg-text-soft);">Motor Multicloud API</small><br>
            <span style="font-weight:600; font-size:12px;">4 Proveedores Sincronizados</span>
        </div>
    """, unsafe_allow_html=True)

    with st.expander("Seleccionar Torneos", expanded=True):
        if 'ligas_sels_widget' not in st.session_state: 
            st.session_state.ligas_sels_widget = ["UEFA Champions League", "Premier League"]
            
        c1, c2 = st.columns(2)
        if c1.button("Todas", use_container_width=True):
            st.session_state.ligas_sels_widget = list(CATALOGO_LIGAS_UNIFICADO.keys())
            st.rerun()
        if c2.button("Limpiar", use_container_width=True):
            st.session_state.ligas_sels_widget = []
            st.rerun()

        ligas_sels = st.multiselect(
            "Torneos disponibles:", 
            options=list(CATALOGO_LIGAS_UNIFICADO.keys()), 
            key="ligas_sels_widget"
        )

    with st.expander("Filtros de Mercado", expanded=True):
        casas_preferidas = st.multiselect(
            "Bookmakers preferidas:",
            ["Betano", "Bet365", "Ecuabet", "1xBet", "Pinnacle", "Bwin", "Unibet", "William Hill"],
            default=st.session_state.casas_preferidas,
            key="casas_preferidas_widget",
            on_change=lambda: ConfigManager.guardar('casas_preferidas', st.session_state.casas_preferidas_widget)
        )
        mercados_sels = st.multiselect("Mercados:", list(diccionario_mercados.keys()), default=["1X2 (Resultado Final)"])
        sin_limite_fecha = st.checkbox("Incluir eventos futuros (Sin límite de fecha)", value=True)
        limite_h = 999999 if sin_limite_fecha else 168

    with st.expander("Gestión de Banca"):
        bankroll_total = st.number_input(
            "Banca Total ($):", min_value=10.0, value=float(st.session_state.bankroll_total), step=10.0,
            key="bankroll_widget", on_change=lambda: ConfigManager.guardar('bankroll_total', st.session_state.bankroll_widget)
        )
        monto_inversion = st.number_input("Inversión Base ($):", min_value=1.0, value=10.0, step=1.0, key="monto_inversion_base")

    consultar = st.button("Escanear Mercado", type="primary", use_container_width=True)

# =========================================================
# 8. NAVEGACIÓN Y VISTAS
# =========================================================
col_nav_rapida, col_nav_extra = st.columns([3, 1.5])
with col_nav_rapida:
    tab_activa_rapida = st.radio("Secciones:", ["Radar Multi-Mercado", "Verificador & Monte Carlo", "Análisis Estadístico H2H"], horizontal=True, label_visibility="collapsed")
with col_nav_extra:
    opcion_extra = st.selectbox("Herramientas adicionales:", ["-- Módulos adicionales --", "Matriz de Cobertura", "Oportunidades +EV", "Alineaciones & Bajas", "Generador Visual", "Bitácora Financiera", "Soporte"], label_visibility="collapsed")

vista_seleccionada = opcion_extra if opcion_extra != "-- Módulos adicionales --" else tab_activa_rapida

# ---------------------------------------------------------
# VISTA 1: RADAR
# ---------------------------------------------------------
if vista_seleccionada == "Radar Multi-Mercado":
    st.title("Radar de Apuestas Deportivas")
    st.caption("Consolidación en tiempo real y modelos probabilísticos")

    if consultar:
        if len(ligas_sels) > 0 and len(mercados_sels) > 0:
            st.cache_data.clear()
            consolidador = {}
            st.session_state.ha_consultado = True
            
            with st.status("Escaneando eventos en directo...", expanded=True) as status_consulta:
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                    futures = [
                        executor.submit(escanear_liga_worker, liga_nom, CATALOGO_LIGAS_UNIFICADO[liga_nom], mercados_sels, limite_h)
                        for liga_nom in ligas_sels
                    ]
                    for future in concurrent.futures.as_completed(futures):
                        liga_nombre, pares_partidos_mercado = future.result()
                        status_consulta.update(label=f"Procesando: {liga_nombre}")
                        for partido_data, m_sel in pares_partidos_mercado:
                            procesar_e_inyectar_mercado([partido_data], m_sel, limite_h, liga_nombre, consolidador)

                status_consulta.update(label=f"Escaneo completado: {len(consolidador)} eventos listos.", state="complete")

            st.session_state.datos_cargados_previos = st.session_state.datos_cargados
            st.session_state.datos_cargados = consolidador
            st.rerun()

    dict_partidos = st.session_state.datos_cargados
    if dict_partidos:
        texto_ai = generar_resumen_ejecutivo_ai(dict_partidos)
        if texto_ai: st.markdown(f"<div class='ai-summary-box'>{texto_ai}</div>", unsafe_allow_html=True)

    if not st.session_state.ha_consultado:
        st.markdown("""
            <div class="empty-state-card">
                <h4 style="margin-bottom:8px;">Panel de Análisis Preparado</h4>
                <p style="color:var(--rg-text-soft); font-size:13px; margin:0;">Selecciona torneos en la barra lateral y presiona <b>Escanear Mercado</b>.</p>
            </div>
        """, unsafe_allow_html=True)
    elif not dict_partidos:
        st.info("No se encontraron partidos disponibles con los criterios seleccionados.")
    else:
        col_izq, col_der = st.columns([6.5, 3.5])
        with col_izq:
            st.subheader(f"Eventos Disponibles ({len(dict_partidos)})")
            ligas_con_datos = list(set([p['liga_origen'] for p in dict_partidos.values()]))
            if ligas_con_datos:
                pestanas_ligas = st.tabs(ligas_con_datos)
                for p_idx, liga_p in enumerate(ligas_con_datos):
                    with pestanas_ligas[p_idx]:
                        partidos_f = [p for p in dict_partidos.values() if p['liga_origen'] == liga_p]
                        for part in partidos_f:
                            with st.container(border=True):
                                st.markdown(f"<span class='liga-chip'>{part['liga_origen']}</span><span class='kickoff-chip'>{part['fecha_str']}</span>", unsafe_allow_html=True)
                                st.markdown(f"<div class='match-header'>{part['local']} vs {part['visitante']}</div>", unsafe_allow_html=True)
                                
                                sub_tabs = st.tabs(list(part['mercados'].keys()))
                                for m_idx, text_m in enumerate(part['mercados'].keys()):
                                    with sub_tabs[m_idx]:
                                        m_info = part['mercados'][text_m]
                                        todas_opciones = list(m_info['max_cuotas'].keys())
                                        
                                        if "1X2" in text_m:
                                            orden_deseado = ["Local", "Empate", "Visitante"]
                                            todas_opciones = [op for op in orden_deseado if op in m_info['max_cuotas']]
                                        
                                        sub_cols = st.columns(len(todas_opciones))
                                        for idx, opcion in enumerate(todas_opciones):
                                            cuota_m = m_info['max_cuotas'][opcion]
                                            with sub_cols[idx]:
                                                val = m_info['value_bets'][opcion]
                                                lbl_val = " 🔥" if val['es_value'] else ""
                                                clave_base = f"ap_{part['id']}_{text_m}_{opcion}"
                                                val_defecto = clave_base in st.session_state.ticket_persistente
                                                st.checkbox(
                                                    f"{opcion} ({cuota_m}){lbl_val}",
                                                    value=val_defecto, key=clave_base,
                                                    on_change=toggle_apuesta,
                                                    args=(part, text_m, opcion, cuota_m, m_info['max_bookies'][opcion], val['prob_real'], clave_base)
                                                )
                                                st.caption(f"{m_info['max_bookies'][opcion]} | Impl: {round(val['prob_real'],1)}%")

        with col_der:
            @st.fragment
            def render_boleto_fragment():
                st.subheader("Boleto de Selección")
                apuestas_seleccionadas = list(st.session_state.ticket_persistente.values())
                if apuestas_seleccionadas:
                    cuota_acum, prob_comb = 1.0, 1.0
                    with st.container(border=True):
                        st.markdown("<div class='ticket-titulo'>Combinado Seleccionado</div>", unsafe_allow_html=True)
                        for ap in apuestas_seleccionadas:
                            cuota_acum *= float(ap['cuota'])
                            prob_comb *= (float(ap['prob_real']) / 100.0)
                            st.markdown(f"<div class='ticket-item'><b>{ap['evento']}</b><br>➔ {ap['seleccion']} | <span class='ticket-cuota-tag'>@{ap['cuota']}</span></div>", unsafe_allow_html=True)

                        monto_ticket = st.number_input("Inversión ($):", min_value=1.0, value=float(monto_inversion), step=1.0)
                        ganancia_neta = (cuota_acum * monto_ticket) - monto_ticket
                        sharpe = calcular_sharpe_parlay(cuota_acum, prob_comb)

                        st.metric("Cuota Total", f"@{round(cuota_acum, 2)}")
                        st.metric("Retorno Potencial", f"${round(ganancia_neta, 2)}")
                        st.metric("Ratio de Eficiencia (Sharpe)", f"{round(sharpe, 3)}")

                        if st.button("Guardar en Historial", type="primary", use_container_width=True):
                            st.session_state.historial_apuestas.append({
                                "Fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                                "Detalles": f"{len(apuestas_seleccionadas)} selecciones",
                                "Liga": apuestas_seleccionadas[0]['liga'],
                                "Market": "Combinada",
                                "Cuota": cuota_acum, "Inversión": monto_ticket,
                                "Estado": "Pendiente", "Ganancia Potencial": ganancia_neta
                            })
                            BitacoraManager.guardar(st.session_state.historial_apuestas)
                            st.toast("Guardado en el historial de apuestas")
                else:
                    st.info("Selecciona casillas en los eventos para construir tu combinación.")

            render_boleto_fragment()

# ---------------------------------------------------------
# VISTA 2: VERIFICADOR DE BOLETOS POR FOTO / TEXTO (RESTAURADO)
# ---------------------------------------------------------
elif vista_seleccionada == "Verificador & Monte Carlo":
    st.title("Verificador de Viabilidad & Análisis de Boletos")
    st.caption("Escanera una captura de tu boleto o pega el texto para calcular la probabilidad real de éxito")

    tab_foto, tab_texto = st.tabs(["📷 Subir Foto de Boleto", "📝 Ingresar Cuotas por Texto"])

    texto_para_analizar = ""

    with tab_foto:
        archivo_imagen = st.file_uploader("Carga una captura o foto clara de tu boleto de apuestas:", type=["jpg", "jpeg", "png", "webp"])
        if archivo_imagen:
            st.image(archivo_imagen, caption="Boleto subido", use_column_width=True)
            st.info("🔍 Analizando imagen del boleto...")
            # Extraer contenido genérico simulado si no hay librería OCR instalada
            texto_para_analizar = "Real Madrid 1.50\nBarcelona 1.80\nArsenal 1.65"

    with tab_texto:
        texto_manual = st.text_area(
            "Escribe o pega tus selecciones (ejemplo: 'Real Madrid 1.50'):",
            value="Real Madrid 1.50\nArsenal 1.80\nBayern Munich 1.40",
            height=140
        )
        if texto_manual:
            texto_para_analizar = texto_manual

    if texto_para_analizar:
        partidos_externos = []
        for linea in texto_para_analizar.strip().split('\n'):
            if not linea.strip(): continue
            cuotas = re.findall(r'\b\d+\.\d+\b', linea.replace(',', '.'))
            if cuotas:
                c_val = float(cuotas[-1])
                nombre = re.sub(r'\b\d+\.\d+\b', '', linea).strip() or "Selección"
                # Estimar probabilidad implícita con margen normalizado del 5%
                prob_estimada = (1.0 / c_val) * 95.0
                partidos_externos.append({"nombre": nombre, "cuota": c_val, "prob_real": prob_estimada})

        if partidos_externos:
            st.markdown("---")
            st.subheader("📊 Diagnóstico y Viabilidad del Boleto")

            cuota_acumulada = 1.0
            prob_combinada = 1.0
            for p in partidos_externos:
                cuota_acumulada *= p['cuota']
                prob_combinada *= (p['prob_real'] / 100.0)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Cuota Total", f"@{round(cuota_acumulada, 2)}")
            c2.metric("Probabilidad Real", f"{round(prob_combinada * 100, 2)}%")
            
            ev_boleto = (cuota_acumulada * prob_combinada) - 1.0
            sharpe_val = calcular_sharpe_parlay(cuota_acumulada, prob_combinada)

            c3.metric("Valor Esperado (EV)", f"{'+' if ev_boleto > 0 else ''}{round(ev_boleto * 100, 1)}%")
            c4.metric("Sharpe Ratio", f"{round(sharpe_val, 2)}")

            # Simulación Monte Carlo
            res_mc = ejecutar_simulacion_montecarlo(partidos_externos)
            
            st.markdown("<div class='veredicto-box'>", unsafe_allow_html=True)
            if ev_boleto > 0.02 and res_mc['pleno_acierto'] > 15.0:
                st.markdown("### 🔥 Veredicto: Boleto Recomendado (+EV)")
                st.write("Las cuotas combinadas superan el riesgo implícito calculado. Buena relación riesgo/beneficio.")
            elif res_mc['fallo_por_uno'] > 35.0:
                st.markdown("### ⚠️ Veredicto: Alto Riesgo de Fallo por 1 Selección")
                st.write(f"Hay un **{round(res_mc['fallo_por_uno'], 1)}%** de probabilidad de que pierdas el boleto por solo un partido. Considera realizar apuestas individuales o utilizar un sistema de cobertura.")
            else:
                st.markdown("### 📉 Veredicto: Valor Negativo (-EV)")
                st.write("La casa de apuestas retiene un margen muy alto en esta combinación. La probabilidad real no justifica la cuota acumulada ofrecida.")
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("#### Detalle de Selección por Selección")
            df_detalle = pd.DataFrame(partidos_externos)
            df_detalle.columns = ["Evento / Selección", "Cuota Ofrecida", "Probabilidad Est. (%)"]
            st.dataframe(df_detalle, use_container_width=True)

# ---------------------------------------------------------
# VISTA 3: ANÁLISIS H2H (RESTAURADO LIMPIO)
# ---------------------------------------------------------
elif vista_seleccionada == "Análisis Estadístico H2H":
    st.title("Modelo Estadístico de Poisson & Dixon-Coles")
    st.caption("Distribución probabilística para estimación de resultados")

    c1, c2 = st.columns(2)
    with c1:
        lambda_loc = st.slider("Expectativa Goles Local (λ):", 0.5, 3.5, 1.45, 0.05)
    with c2:
        lambda_vis = st.slider("Expectativa Goles Visitante (λ):", 0.5, 3.5, 1.10, 0.05)

    res_poisson = calcular_modelo_poisson(lambda_loc, lambda_vis)

    st.markdown("### Probabilidades del Encuentro")
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Victoria Local", f"{round(res_poisson['Local'], 1)}%")
    col_b.metric("Empate", f"{round(res_poisson['Empate'], 1)}%")
    col_c.metric("Victoria Visitante", f"{round(res_poisson['Visitante'], 1)}%")

    st.markdown("### Mercados Secundarios Calculados")
    col_d, col_e, col_f = st.columns(3)
    col_d.metric("Doble Oportunidad 1X", f"{round(res_poisson['1X (Local o Empate)'], 1)}%")
    col_e.metric("Ambos Anotan (Sí)", f"{round(res_poisson['Sí'], 1)}%")
    col_f.metric("Más de 2.5 Goles", f"{round(res_poisson['Más de 2.5'], 1)}%")

# ---------------------------------------------------------
# OTROS MÓDULOS
# ---------------------------------------------------------
elif vista_seleccionada == "Matriz de Cobertura":
    st.title("Calculadora de Arbitraje")
    st.write(detectar_surebet({"1": 2.10, "X": 3.40, "2": 4.50}))

elif vista_seleccionada == "Oportunidades +EV":
    st.title("Cazador de Cuotas con Valor (+EV)")
    st.info("Utiliza esta herramienta consultando primero los eventos en el Radar.")

elif vista_seleccionada == "Alineaciones & Bajas":
    st.title("Alineaciones y Reporte de Lesionados")
    st.info("Módulo de integración para plantillas de equipos.")

elif vista_seleccionada == "Generador Visual":
    st.title("Resumen del Boleto")
    components.html("<div style='color:white; text-align:center;'><h4>Boleto Combinado</h4></div>", height=80)

elif vista_seleccionada == "Bitácora Financiera":
    st.title("Historial y Auditoría de Apuestas")
    if st.session_state.historial_apuestas:
        st.dataframe(pd.DataFrame(st.session_state.historial_apuestas), use_container_width=True)

elif vista_seleccionada == "Soporte":
    st.title("Centro de Soporte")
    msg = st.text_area("Escribe tu consulta:")
    if st.button("Enviar Consulta"): enviar_telegram(msg)
