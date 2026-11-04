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
from html import escape
from zoneinfo import ZoneInfo
import plotly.express as px
from analytics import EXPECTED, valid_odds, normalize_outcome, surebet, parse_ticket, api_football_markets
from api_client import get_json
from pro_ui import apply_theme, header, dashboard, opportunity_table, quick_navigation, themed_chart
from workspace_logic import filter_events
from ticket_ui import render_ticket
from finance_ui import render_finance
from football_details import render as render_football_details


def secret(name):
    try:
        return str(st.secrets.get(name, ""))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return os.environ.get(name, "")


# =========================================================
# 1. CONFIGURACIÓN DE PÁGINA
# =========================================================
st.set_page_config(
    page_title="Analizador | Sports Workspace",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_FILE = "bitacora_backup.json"
CONFIG_FILE = "user_config.json"

API_KEY = secret("ODDS_API_KEY")
AF_API_KEY = secret("AF_API_KEY")
AF_BASE_URL = "https://v3.football.api-sports.io"

ALLSPORTS_KEY = secret("ALLSPORTS_API_KEY")
ALLSPORTS_BASE_URL = "https://allsportsapi.com/api/football/"

SPORTAPI_KEY = secret("SPORTAPI_KEY")
SPORTAPI_BASE_URL = "https://api.sportapi.ai/v1/football/"

DEFAULT_TG_TOKEN = secret("TG_TOKEN")
DEFAULT_TG_CHAT_ID = secret("TG_CHAT_ID")

# =========================================================
# 2. PERSISTENCIA
# =========================================================
class BitacoraManager:
    @staticmethod
    def cargar() -> List[Dict[str, Any]]:
        return st.session_state.get('historial_apuestas', [])

    @staticmethod
    def guardar(historial: List[Dict[str, Any]]) -> None:
        st.session_state.historial_apuestas = historial

class ConfigManager:
    @staticmethod
    def cargar() -> Dict[str, Any]:
        return st.session_state.get('user_config_cache', {})

    @staticmethod
    def guardar(key: str, val: Any) -> None:
        st.session_state.setdefault('user_config_cache', {})[key] = val
        st.session_state[key] = val

def enviar_telegram(mensaje: str) -> bool:
    token = st.session_state.get('tg_token', DEFAULT_TG_TOKEN)
    chat_id = st.session_state.get('tg_chat_id', DEFAULT_TG_CHAT_ID)
    if not token or not chat_id: return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": mensaje}, timeout=8)
        return r.status_code == 200
    except Exception:
        return False

# =========================================================
# 3. MODELADO MATEMÁTICO VECTORIZADO
# =========================================================
def poisson_pmf_vec(k_array: np.ndarray, mu: float) -> np.ndarray:
    return (np.power(mu, k_array) * np.exp(-mu)) / np.array([math.factorial(k) for k in k_array])

def calcular_modelo_poisson(lambda_local: float = 1.45, lambda_visita: float = 1.10) -> Dict[str, float]:
    if not all(math.isfinite(x) and 0 <= x <= 20 for x in (lambda_local, lambda_visita)):
        raise ValueError("Las medias de goles deben estar entre 0 y 20")
    max_goles = max(20, int(max(lambda_local, lambda_visita) + 10 * math.sqrt(max(lambda_local, lambda_visita))))
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
    return surebet(cuotas_max_dict)


def calcular_sharpe_parlay(cuota_total: float, prob_combinada: float) -> float:
    ev = (cuota_total * prob_combinada) - 1.0
    varianza = cuota_total ** 2 * prob_combinada * (1 - prob_combinada)
    desviacion = math.sqrt(varianza) if varianza > 0 else 1.0
    return ev / desviacion

def ejecutar_simulacion_montecarlo(partidos: List[Dict[str, Any]], num_simulaciones: int = 10000) -> Dict[str, Any]:
    if not partidos: return {}
    if not 1 <= num_simulaciones <= 1000000:
        raise ValueError("Número de simulaciones fuera de rango")
    probs = [p['prob_real'] / 100.0 for p in partidos]
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probs):
        raise ValueError("Probabilidad fuera de rango")
    num_eventos = len(probs)
    matriz_rand = np.random.default_rng(42).random((num_simulaciones, num_eventos))
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
        liga = escape(p['liga_origen'])
        for m_info in p['mercados'].values():
            if m_info.get('surebet', {}).get('es_surebet', False): total_surebets += 1
            for val_item in m_info.get('value_bets', {}).values():
                if val_item.get('ev', 0) > 0.03:
                    total_valuebets += 1
                    liga_con_mas_valor[liga] = liga_con_mas_valor.get(liga, 0) + 1

    top_liga = max(liga_con_mas_valor, key=liga_con_mas_valor.get) if liga_con_mas_valor else "General"
    val_top_count = liga_con_mas_valor.get(top_liga, 0)

    res = f"<b>Análisis de Mercado:</b> {total_partidos} eventos procesados. "
    res += f"Arbitrajes (SureBets): <b>{total_surebets}</b>. " if total_surebets > 0 else "Sin arbitrajes. "
    res += f"Oportunidades +EV: <b>{total_valuebets}</b> (Concentración en <b>{top_liga}</b>)." if total_valuebets > 0 else ""
    return res

# =========================================================
# 4. ESTADOS Y ESTILOS CSS (CORRIGIENDO VISIBILIDAD DE EQUIPOS)
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
        if len(st.session_state.ticket_persistente) >= 20 and clave_k not in st.session_state.ticket_persistente:
            st.session_state[clave_k] = False
            st.toast("El boleto admite hasta 20 selecciones. Quita una para añadir otra.")
            return
        st.session_state.ticket_persistente[clave_k] = {
            "evento": f"{partido_obj['local']} vs {partido_obj['visitante']}",
            "event_id": partido_obj['id'], "liga": partido_obj['liga_origen'], "mercado": mercado_nombre,
            "seleccion": opcion_nombre, "cuota": cuota_val, "casa": casa_val,
            "prob_real": prob_val, "fecha_ts": partido_obj.get('fecha_ts', None)
        }
        st.toast(f"Añadido: {opcion_nombre} (@{cuota_val})")
    else:
        st.session_state.ticket_persistente.pop(clave_k, None)

apply_theme(st.session_state.tema_visual == "Claro")
header()

# =========================================================
# 5. MAPEO DE LIGAS
# =========================================================
CATALOGO_LIGAS_UNIFICADO = {
    "UEFA Champions League": {"odds_api": ["soccer_uefa_champs_league"], "af_id": 2, "allsports_id": 3, "sportapi_id": 7},
    "UEFA Europa League": {"odds_api": ["soccer_uefa_europa_league"], "af_id": 3, "allsports_id": 4, "sportapi_id": 8},
    "UEFA Conference League": {"odds_api": ["soccer_uefa_europa_conference_league"], "af_id": 848, "allsports_id": None, "sportapi_id": None},
    "Premier League": {"odds_api": ["soccer_epl"], "af_id": 39, "allsports_id": 152, "sportapi_id": 17},
    "La Liga (España)": {"odds_api": ["soccer_spain_la_liga"], "af_id": 140, "allsports_id": 302, "sportapi_id": 8},
    "Serie A (Italia)": {"odds_api": ["soccer_italy_serie_a"], "af_id": 135, "allsports_id": 207, "sportapi_id": 23},
    "Bundesliga (Alemania)": {"odds_api": ["soccer_germany_bundesliga"], "af_id": 78, "allsports_id": 175, "sportapi_id": 35},
    "Ligue 1 (Francia)": {"odds_api": ["soccer_france_ligue_one"], "af_id": 61, "allsports_id": 168, "sportapi_id": 34},
    "MLS (USA)": {"odds_api": ["soccer_usa_mls"], "af_id": 253, "allsports_id": 332, "sportapi_id": 54},
    "Liga MX (México)": {"odds_api": ["soccer_mexico_liga_mx"], "af_id": 262, "allsports_id": 230, "sportapi_id": 41},
    "Liga Profesional (Argentina)": {"odds_api": ["soccer_argentina_primera_division"], "af_id": 128, "allsports_id": 44, "sportapi_id": 155},
    "Brasileirão Serie A (Brasil)": {"odds_api": ["soccer_brazil_campeonato"], "af_id": 71, "allsports_id": 81, "sportapi_id": 325},
    "LigaPro (Ecuador)": {"odds_api": [], "af_id": 242, "allsports_id": 122, "sportapi_id": 512},
    "Copa Libertadores": {"odds_api": ["soccer_conmebol_copa_libertadores"], "af_id": 13, "allsports_id": 11, "sportapi_id": 380}
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

@st.cache_data(ttl=90, max_entries=256, show_spinner=False)
def consultar_api_odds(sport_key, market_key):
    if not API_KEY or not sport_key: return [], "Sin API"
    # Featured odds endpoint only supports h2h/totals here. Other markets require
    # per-event endpoints and are obtained from the alternative providers.
    if market_key not in {"h2h", "totals"}: return [], "Mercado no disponible en endpoint general"
    data, error = get_json(f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/",
                           params={"apiKey": API_KEY, "regions": "eu,us", "markets": market_key, "oddsFormat": "decimal"})
    return (data, None) if isinstance(data, list) else ([], error or "Formato inesperado")

@st.cache_data(ttl=180, max_entries=128, show_spinner=False)
def consultar_af_con_fallback_fixtures(af_id):
    if not af_id or not AF_API_KEY: return [], "Sin clave o liga"
    # Resolve the active season instead of assuming the calendar year.
    leagues, error = get_json(f"{AF_BASE_URL}/leagues", headers=af_headers(), params={"id": af_id, "current": "true"})
    response = leagues.get("response", []) if isinstance(leagues, dict) else []
    seasons = response[0].get("seasons", []) if response else []
    current = next((season.get("year") for season in seasons if season.get("current")), None)
    if current is None: return [], error or "Temporada no disponible"
    data, error = get_json(f"{AF_BASE_URL}/odds", headers=af_headers(), params={"league": af_id, "season": current})
    if not isinstance(data, dict) or data.get("errors"): return [], error or "Proveedor devolvió un error"
    results = data.get("response", [])
    # Bound pagination to avoid exhausting free API quotas.
    for page in range(2, min(int(data.get("paging", {}).get("total", 1)), 3) + 1):
        extra, _ = get_json(f"{AF_BASE_URL}/odds", headers=af_headers(), params={"league": af_id, "season": current, "page": page})
        if isinstance(extra, dict): results.extend(extra.get("response", []))
    return (results, None) if isinstance(results, list) else ([], "Formato inesperado")

@st.cache_data(ttl=180, max_entries=128, show_spinner=False)
def consultar_allsports_odds(league_id=None):
    if not ALLSPORTS_KEY or not league_id: return {}, "Sin clave o liga"
    data, error = get_json(ALLSPORTS_BASE_URL, params={"met": "Odds", "APIkey": ALLSPORTS_KEY, "leagueId": league_id})
    return (data.get("result", {}), None) if isinstance(data, dict) and data.get("success") == 1 else ({}, error or "Proveedor sin respuesta válida")

@st.cache_data(ttl=180, max_entries=128, show_spinner=False)
def consultar_sportapi_odds(sportapi_id=None):
    if not SPORTAPI_KEY or not sportapi_id: return [], "Sin clave o liga"
    data, error = get_json(f"{SPORTAPI_BASE_URL}odds", params={"apikey": SPORTAPI_KEY, "tournament_id": sportapi_id})
    result = data.get("data", []) if isinstance(data, dict) else []
    return (result, error) if isinstance(result, list) else ([], error or "Formato inesperado")

def escanear_liga_worker(liga_nombre, config_liga, mercados_sels, limite_h):
    resultados_normalizados, diagnostics = [], []
    for skey in config_liga.get("odds_api", []):
        for market_sel in mercados_sels:
            data, error = consultar_api_odds(skey, diccionario_mercados.get(market_sel, "h2h"))
            if error: diagnostics.append(f"The Odds API · {market_sel}: {error}")
            if data and isinstance(data, list):
                for p in data: resultados_normalizados.append((p, market_sel))

    try:
        af_id = config_liga.get("af_id")
        if af_id and AF_API_KEY:
            af_data, error = consultar_af_con_fallback_fixtures(af_id)
            if error: diagnostics.append(f"API-Football: {error}")
            for item in af_data:
                fix, teams, bookies_af = item.get("fixture", {}), item.get("teams", {}), item.get("bookmakers", [])
                bookmakers_converted = []
                for b in bookies_af:
                    b_markets = api_football_markets(b.get("bets", []), teams.get("home", {}).get("name", "Local"), teams.get("away", {}).get("name", "Visitante"))
                    bookmakers_converted.append({"key": str(b.get("id")), "title": b.get("name"), "markets": b_markets})

                partido_norm = {
                    "id": f"af_{fix.get('id')}", "home_team": teams.get("home", {}).get("name", "Local"),
                    "away_team": teams.get("away", {}).get("name", "Visitante"),
                    "commence_time": fix.get("date", datetime.now(timezone.utc).isoformat()), "bookmakers": bookmakers_converted
                }
                for m_sel in mercados_sels: resultados_normalizados.append((partido_norm, m_sel))
    except (TypeError, ValueError, KeyError, AttributeError):
        diagnostics.append("API-Football: formato de respuesta inesperado")

    try:
        as_id = config_liga.get("allsports_id")
        if as_id and ALLSPORTS_KEY:
            res_as, error = consultar_allsports_odds(as_id)
            if error: diagnostics.append(f"AllSports: {error}")
            if isinstance(res_as, dict):
                for m_id, odds_list in res_as.items():
                    if not odds_list or not isinstance(odds_list, list): continue
                    match_info = odds_list[0]
                    bookmakers_converted, dict_bookies = [], {}
                    for entry in odds_list: dict_bookies.setdefault(entry.get("bookmaker_name", "AllSports"), []).append(entry)
                    for b_name, b_entries in dict_bookies.items():
                        outcomes_h2h = []
                        for e in b_entries:
                            m_type = e.get("type", "")
                            odd_val = valid_odds(e.get("odd"))
                            if odd_val is None: continue
                            if m_type in ["1", "Home"]: outcomes_h2h.append({"name": match_info.get("event_home_team", "Local"), "price": odd_val})
                            elif m_type in ["X", "Draw"]: outcomes_h2h.append({"name": "Draw", "price": odd_val})
                            elif m_type in ["2", "Away"]: outcomes_h2h.append({"name": match_info.get("event_away_team", "Visitante"), "price": odd_val})
                        if outcomes_h2h: bookmakers_converted.append({"key": b_name, "title": b_name, "markets": [{"key": "h2h", "outcomes": outcomes_h2h}]})
                    partido_as = {
                        "id": f"as_{m_id}", "home_team": match_info.get("event_home_team", "Local"),
                        "away_team": match_info.get("event_away_team", "Visitante"),
                        "commence_time": match_info.get("event_date", datetime.now(timezone.utc).isoformat()), "bookmakers": bookmakers_converted
                    }
                    for m_sel in mercados_sels: resultados_normalizados.append((partido_as, m_sel))
    except (TypeError, ValueError, KeyError, AttributeError):
        diagnostics.append("AllSports: formato de respuesta inesperado")

    try:
        sp_id = config_liga.get("sportapi_id")
        if sp_id and SPORTAPI_KEY:
            sp_data, error = consultar_sportapi_odds(sp_id)
            if error: diagnostics.append(f"SportAPI: {error}")
            for item in sp_data:
                bookmakers_converted = []
                for b in item.get("bookmakers", []):
                    outcomes_h2h = [{"name": o.get("label"), "price": valid_odds(o.get("value"))} for o in b.get("odds", [])]
                    if outcomes_h2h: bookmakers_converted.append({"key": b.get("name"), "title": b.get("name"), "markets": [{"key": "h2h", "outcomes": outcomes_h2h}]})
                partido_sp = {
                    "id": f"sp_{item.get('id', '0')}", "home_team": item.get("home_team", "Local"),
                    "away_team": item.get("away_team", "Visitante"),
                    "commence_time": item.get("start_time", datetime.now(timezone.utc).isoformat()), "bookmakers": bookmakers_converted
                }
                for m_sel in mercados_sels: resultados_normalizados.append((partido_sp, m_sel))
    except (TypeError, ValueError, KeyError, AttributeError):
        diagnostics.append("SportAPI: formato de respuesta inesperado")

    return (liga_nombre, resultados_normalizados, diagnostics)

def procesar_e_inyectar_mercado(datos, mercado, limite_horas, nombre_liga, diccionario_consolidador):
    ahora_utc = datetime.now(timezone.utc)
    if not datos or not isinstance(datos, list): return

    for partido in datos:
        partido_id = partido['id']
        home, away = partido['home_team'], partido['away_team']
        try:
            fecha_utc = datetime.fromisoformat(partido['commence_time'].replace('Z', '+00:00'))
        except (ValueError, KeyError, TypeError):
            continue
        if fecha_utc.tzinfo is None:
            fecha_utc = fecha_utc.replace(tzinfo=timezone.utc)
        if fecha_utc > ahora_utc + timedelta(hours=limite_horas): continue
        if fecha_utc < ahora_utc - timedelta(hours=3): continue

        es_en_vivo = partido.get('in_play', False)
        fecha_local = fecha_utc.astimezone(ZoneInfo("America/Guayaquil"))
        bookmakers = partido.get('bookmakers', [])
        if not bookmakers: continue

        casas_pref = st.session_state.get('casas_preferidas', [])
        if casas_pref:
            bookies_filtrados = [b for b in bookmakers if any(cp.lower() in b['title'].lower() for cp in casas_pref)]
            bookmakers = bookies_filtrados

        cuotas_globales = {}
        for b in bookmakers:
            dict_b_markets = {m['key']: m['outcomes'] for m in b.get('markets', [])}
            key = diccionario_mercados.get(mercado)
            for outcome in dict_b_markets.get(key, []):
                name = normalize_outcome(key, outcome, home, away)
                price = valid_odds(outcome.get('price'))
                if name and price:
                    cuotas_globales.setdefault(name, []).append((price, b['title']))

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
            value_bets[opcion] = {"ev": ev, "prob_real": prob_real * 100, "prob_poisson": probs_poisson.get(opcion, prob_real * 100), "es_value": ev > 0.02}

        expected = EXPECTED.get(mercado)
        # Double-chance selections overlap: inverse-odds sums are not arbitrage.
        info_surebet = surebet(max_cuotas, expected) if expected else surebet({})
        if expected and set(max_cuotas) != expected:
            # Keep incomplete quotes visible but do not assign false value signals.
            for item in value_bets.values():
                item.update(ev=0.0, es_value=False, prob_real=0.0)
        if mercado == "Doble Oportunidad":
            for opcion, item in value_bets.items():
                item.update(ev=0.0, es_value=False, prob_real=0.0)
        if max_cuotas:
            if partido_id not in diccionario_consolidador:
                diccionario_consolidador[partido_id] = {
                    "id": partido_id, "liga_origen": nombre_liga,
                    "fecha_str": fecha_local.strftime("%d/%m/%Y - %H:%M"), "fecha_ts": fecha_utc.timestamp(),
                    "local": home, "visitante": away, "es_en_vivo": es_en_vivo, "mercados": {}
                }
            diccionario_consolidador[partido_id]["mercados"][mercado] = {
                "max_cuotas": max_cuotas, "max_bookies": max_bookies,
                "value_bets": value_bets, "variaciones": variaciones_dict, "surebet": info_surebet
            }

# =========================================================
# 7. SIDEBAR Y PANEL DE RESPALDO EN PANTALLA PRINCIPAL
# =========================================================
with st.sidebar:
    st.markdown('<div class="brand"><span>◈</span> Analizador<div class="brand-sub">TU ESPACIO DEPORTIVO</div></div>',unsafe_allow_html=True)
    st.markdown("### Filtros de escaneo")
    st.radio("Apariencia:", options=["Oscuro", "Claro"], index=0 if st.session_state.tema_visual == "Oscuro" else 1, key="tema_visual_widget", on_change=lambda: ConfigManager.guardar('tema_visual', st.session_state.tema_visual_widget), horizontal=True)

    configured_count = sum(bool(key) for key in (API_KEY, AF_API_KEY, ALLSPORTS_KEY, SPORTAPI_KEY))
    st.markdown(f"""
        <div class="creditos-caja-pro">
            <small style="color:var(--rg-text-soft);">Fuentes de datos</small><br>
            <span style="font-weight:600; font-size:12px;">Credenciales configuradas: {configured_count}/4</span>
        </div>
    """, unsafe_allow_html=True)

    with st.expander("Seleccionar Torneos", expanded=True):
        if 'ligas_sels_widget' not in st.session_state: st.session_state.ligas_sels_widget = ["UEFA Champions League", "Premier League"]
        c1, c2 = st.columns(2)
        if c1.button("Todas", width="stretch"):
            st.session_state.ligas_sels_widget = list(CATALOGO_LIGAS_UNIFICADO.keys())
            st.rerun()
        if c2.button("Limpiar", width="stretch"):
            st.session_state.ligas_sels_widget = []
            st.rerun()
        ligas_sels = st.multiselect("Torneos:", options=list(CATALOGO_LIGAS_UNIFICADO.keys()), key="ligas_sels_widget")

    with st.expander("Filtros de Mercado", expanded=True):
        casas_preferidas = st.multiselect("Casas de apuestas:", ["Betano", "Bet365", "Ecuabet", "1xBet", "Pinnacle", "Bwin", "Unibet", "William Hill"], default=[x for x in st.session_state.casas_preferidas if x in ["Betano", "Bet365", "Ecuabet", "1xBet", "Pinnacle", "Bwin", "Unibet", "William Hill"]], key="casas_preferidas_widget", on_change=lambda: ConfigManager.guardar('casas_preferidas', st.session_state.casas_preferidas_widget))
        mercados_sels = st.multiselect("Mercados:", list(diccionario_mercados.keys()), default=["1X2 (Resultado Final)"])
        horizonte = st.selectbox("Horizonte de consulta", ["Próximas 24 horas", "Próximas 72 horas", "Próximos 7 días", "Todos los disponibles"],index=2)
        limite_h = {"Próximas 24 horas":24,"Próximas 72 horas":72,"Próximos 7 días":168,"Todos los disponibles":999999}[horizonte]

    with st.expander("Gestión de Banca"):
        bankroll_total = st.number_input("Banca Total ($):", min_value=10.0, value=float(st.session_state.bankroll_total), step=10.0, key="bankroll_widget", on_change=lambda: ConfigManager.guardar('bankroll_total', st.session_state.bankroll_widget))
        monto_inversion = st.number_input("Inversión Base ($):", min_value=1.0, value=10.0, step=1.0, key="monto_inversion_base")

    consultar = st.button("Escanear Mercado", type="primary", width="stretch")

# One navigation control and one scan pipeline, shared by all screens.
VIEWS = ["Dashboard", "Radar Multi-Mercado", "Verificador & Monte Carlo", "Análisis Estadístico H2H",
         "Matriz de Cobertura", "Oportunidades +EV", "Alineaciones & Bajas", "Generador Visual", "Bitácora Financiera", "Soporte"]
quick_navigation()
VIEW_LABELS = {"Dashboard":"Vista general", "Radar Multi-Mercado":"Radar de eventos", "Verificador & Monte Carlo":"Verificar un boleto", "Análisis Estadístico H2H":"Modelo de goles", "Matriz de Cobertura":"Cobertura de mercados", "Oportunidades +EV":"Oportunidades +EV", "Alineaciones & Bajas":"Alineaciones y bajas", "Generador Visual":"Mi boleto", "Bitácora Financiera":"Bitácora financiera", "Soporte":"Ayuda y conexiones"}
vista_seleccionada = st.selectbox("Ir a sección", VIEWS, key="navigation",format_func=lambda view:VIEW_LABELS[view])
if st.session_state.get('ultima_consulta'):
    st.caption("Último escaneo con resultados: " + st.session_state.ultima_consulta + " · Ecuador (UTC−5)")

if consultar:
    if not ligas_sels or not mercados_sels:
        st.warning("Selecciona al menos un torneo y un mercado.")
    elif not any((API_KEY, AF_API_KEY, ALLSPORTS_KEY, SPORTAPI_KEY)):
        st.warning("Configura al menos una API en Secrets de Streamlit antes de escanear.")
    else:
        consolidador, failed, diagnostics = {}, [], []
        st.session_state.ha_consultado = True
        with st.status("Consultando proveedores…", expanded=True) as status_consulta:
            progress = st.progress(0,text="Preparando consultas…")
            completed = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = {executor.submit(escanear_liga_worker, name, CATALOGO_LIGAS_UNIFICADO[name], mercados_sels, limite_h): name for name in ligas_sels}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        name, pairs, notes = future.result()
                        diagnostics.extend(f"{name} · {note}" for note in notes)
                        for data, market in pairs:
                            try:
                                procesar_e_inyectar_mercado([data], market, limite_h, name, consolidador)
                            except (TypeError, ValueError, KeyError, AttributeError):
                                diagnostics.append(f"{name} · Evento con formato inválido omitido")
                    except Exception:
                        failed.append(futures[future])
                    completed += 1
                    progress.progress(completed / len(futures),text=f"{completed} de {len(futures)} competiciones procesadas")
            status_consulta.update(label=f"Escaneo terminado · {len(consolidador)} eventos", state="complete")
        st.session_state.scan_errors = failed
        st.session_state.api_diagnostics = diagnostics
        if not consolidador and (failed or diagnostics) and st.session_state.datos_cargados:
            st.warning("La consulta no devolvió resultados válidos. Se conservan los eventos del escaneo anterior; no corresponden a una actualización nueva.")
        else:
            st.session_state.datos_cargados_previos = st.session_state.datos_cargados
            st.session_state.datos_cargados = consolidador
            st.session_state.ultima_consulta = datetime.now(ZoneInfo("America/Guayaquil")).strftime("%d/%m/%Y %H:%M")
            for selection in st.session_state.ticket_persistente.values():
                market = consolidador.get(selection.get('event_id'), {}).get('mercados', {}).get(selection['mercado'], {})
                choice = selection['seleccion']
                selection['stale'] = choice not in market.get('max_cuotas', {})
                if not selection['stale']:
                    selection.update(cuota=market['max_cuotas'][choice], casa=market['max_bookies'][choice], prob_real=market['value_bets'][choice]['prob_real'])

if st.session_state.get('api_diagnostics'):
    with st.expander("Avisos de proveedores"):
        for note in sorted(set(st.session_state.api_diagnostics)):
            st.write(note)
if st.session_state.get('scan_errors'):
    st.warning("Algunas ligas no pudieron procesarse: " + ", ".join(st.session_state.scan_errors))

if vista_seleccionada == "Dashboard":
    st.subheader("Vista general")
    dashboard(st.session_state.datos_cargados)
    if st.session_state.datos_cargados:
        st.subheader("Comparador de mercado")
        opportunity_table(st.session_state.datos_cargados)

# ---------------------------------------------------------
# VISTA 1: RADAR
# ---------------------------------------------------------
elif vista_seleccionada == "Radar Multi-Mercado":
    st.title("Radar de eventos")
    st.caption("Encuentra un partido, compara sus mercados y añade selecciones a tu boleto.")
    events = st.session_state.datos_cargados
    if not events:
        st.info("El Radar está listo. Elige tus competiciones en el menú lateral y pulsa Escanear Mercado.")
        render_ticket(BitacoraManager.guardar)
    else:
        left,right = st.columns([2.1,1],gap="large")
        with left:
            search = st.text_input("Buscar equipo",placeholder="Ej.: Arsenal, Barcelona…",key="radar_search")
            a,b = st.columns(2)
            league = a.selectbox("Competición",["Todas",*sorted({p['liga_origen'] for p in events.values()})],key="radar_league")
            order = b.selectbox("Ordenar por",["Más próximos","Mayor valor"],key="radar_order")
            only_value = st.checkbox("Mostrar solo eventos con +EV",key="radar_value")
            filtered = filter_events(events,search,league,order,only_value)
            st.caption(f"{len(filtered)} de {len(events)} eventos · Mostrando hasta 12 por página")
            if not filtered:
                st.info("No hay coincidencias. Prueba otro equipo, competición o desactiva el filtro +EV.")
            else:
                page = st.selectbox("Página",range(1,(len(filtered)+11)//12+1),key="radar_page")
                for part in filtered[(page-1)*12:page*12]:
                    with st.container(border=True):
                        st.markdown(f"<span class='liga-chip'>{escape(part['liga_origen'])}</span><span class='kickoff-chip'>{escape(part['fecha_str'])}</span>",unsafe_allow_html=True)
                        initials = lambda team: escape(''.join(word[0] for word in team.split()[:2]).upper())
                        st.markdown(f"<div class='match-header'><span class='team-mark'>{initials(part['local'])}</span>{escape(part['local'])}<span class='versus'>vs</span>{escape(part['visitante'])}<span class='team-mark'>{initials(part['visitante'])}</span></div>",unsafe_allow_html=True)
                        market_name = st.selectbox("Mercado",list(part['mercados']),key="market_"+part['id'])
                        market = part['mercados'][market_name]
                        choices = list(market['max_cuotas'])
                        if "1X2" in market_name:
                            choices = [choice for choice in ['Local','Empate','Visitante'] if choice in choices]
                        for column,choice in zip(st.columns(len(choices)),choices):
                            with column:
                                quote = market['max_cuotas'][choice]
                                value = market['value_bets'][choice]
                                key = f"ap_{part['id']}_{market_name}_{choice}"
                                st.checkbox(f"{choice} ({quote:g})",value=key in st.session_state.ticket_persistente,key=key,
                                    on_change=toggle_apuesta,args=(part,market_name,choice,quote,market['max_bookies'][choice],value['prob_real'],key))
                                st.caption(market['max_bookies'][choice])
                                if value['es_value']: st.caption(f"+EV · {value['ev']*100:.1f}% estimado")
                                st.caption(f"Consenso {value['prob_real']:.1f}%" if value['prob_real'] else "Consenso no disponible")
        with right:
            render_ticket(BitacoraManager.guardar)

# ---------------------------------------------------------
# VISTA 2: VERIFICADOR
# ---------------------------------------------------------
elif vista_seleccionada == "Verificador & Monte Carlo":
    st.title("Verificador de Boletos")
    st.caption("Análisis cuantitativo de viabilidad, Kelly & Monte Carlo")

    st.caption("Las probabilidades del verificador son una aproximación a partir de las cuotas (ajuste del 5 %). La combinada supone eventos independientes.")
    tab_foto, tab_texto = st.tabs(["📷 Foto", "📝 Texto"])
    texto_para_analizar = ""

    with tab_foto:
        uploaded = st.file_uploader("Sube captura del boleto:", type=["jpg", "jpeg", "png"])
        if uploaded and st.button("Extraer texto de la imagen"):
            try:
                from PIL import Image
                import pytesseract
                image = Image.open(uploaded)
                if image.width * image.height > 16000000:
                    st.error("La imagen supera el límite de 16 megapíxeles.")
                else:
                    available = pytesseract.get_languages(config='')
                    languages = '+'.join(lang for lang in ('spa','eng') if lang in available)
                    if not languages:
                        st.error("Instala el idioma español o inglés de Tesseract para leer el boleto.")
                    else:
                        st.session_state.ocr_text = pytesseract.image_to_string(image, lang=languages, timeout=15)
            except Exception:
                st.error("No se pudo leer la imagen. Revisa que Tesseract esté instalado o pega el texto manualmente.")
        texto_ocr = st.text_area("Texto extraído (revisa y corrige las cuotas)", value=st.session_state.get('ocr_text', ''))
        if st.session_state.get('ocr_text') and not texto_ocr:
            st.info("Vuelve a abrir esta sección para revisar el texto extraído.")
    with tab_texto:
        texto_manual = st.text_area("Pega tus selecciones (equipo y cuota por línea):", placeholder="Real Madrid 1.50", height=120)
    source = st.radio("Analizar desde", ["Texto manual", "Foto revisada"], horizontal=True)
    texto_para_analizar = texto_manual if source == "Texto manual" else texto_ocr
    if texto_para_analizar:
        partidos_ext = parse_ticket(texto_para_analizar)
        if not partidos_ext:
            st.info("No se reconocen selecciones válidas. Usa un nombre seguido de una cuota decimal mayor que 1.")
        if partidos_ext:
            cuota_acum, prob_comb = 1.0, 1.0
            for p in partidos_ext:
                cuota_acum *= p['cuota']
                prob_comb *= (p['prob_real'] / 100.0)

            ev = (cuota_acum * prob_comb) - 1.0
            sharpe = calcular_sharpe_parlay(cuota_acum, prob_comb)
            b_odds = cuota_acum - 1.0
            kelly_pct = max(0.0, ((b_odds * prob_comb) - (1.0 - prob_comb)) / b_odds) if b_odds > 0 else 0.0
            score = min(100, max(0, int((prob_comb * 60) + (max(0, ev) * 200) + (sharpe * 10))))

            c1, c2, c3 = st.columns(3)
            c1.metric("Salud", f"{score}/100")
            c2.metric("Cuota", f"@{round(cuota_acum, 2)}")
            c3.metric("EV Neto", f"{'+' if ev > 0 else ''}{round(ev * 100, 1)}%")

            st.markdown("### 🧮 Criterio de Kelly & Riesgo")
            k1, k2 = st.columns(2)
            k1.metric("Stake Sugerido", f"${round((kelly_pct * 0.25) * bankroll_total, 2)}")
            k2.metric("Sharpe Ratio", f"{round(sharpe, 2)}")

            res_mc = ejecutar_simulacion_montecarlo(partidos_ext)
            st.markdown("### 🎲 Monte Carlo (10k simulaciones)")
            m1, m2 = st.columns(2)
            m1.metric("Acierto Pleno", f"{round(res_mc['pleno_acierto'], 1)}%")
            m2.metric("Fallo por 1", f"{round(res_mc['fallo_por_uno'], 1)}%")

            st.markdown("<div class='veredicto-box'>", unsafe_allow_html=True)
            if ev > 0.02 and res_mc['pleno_acierto'] > 15.0:
                st.markdown("### 🔥 Veredicto: Alto Valor (+EV)")
                st.write("Excelente relación de riesgo/retorno matemática.")
            elif res_mc['fallo_por_uno'] > 30.0:
                st.markdown("### ⚠️ Veredicto: Riesgo 'Fallo por 1'")
                st.write(f"Alta probabilidad (**{round(res_mc['fallo_por_uno'], 1)}%**) de perder por un solo evento. Considera sistemas de cobertura.")
            else:
                st.markdown("### 📉 Veredicto: Desfavorable (-EV)")
                st.write("Margen de la casa muy alto. No recomendado.")
            st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------
# VISTA 3: H2H
# ---------------------------------------------------------
elif vista_seleccionada == "Análisis Estadístico H2H":
    st.title("Modelo de goles")
    l_loc = st.slider("Goles Local (λ):", 0.5, 3.5, 1.45, 0.05)
    l_vis = st.slider("Goles Visitante (λ):", 0.5, 3.5, 1.10, 0.05)
    res_p = calcular_modelo_poisson(l_loc, l_vis)
    c1, c2, c3 = st.columns(3)
    c1.metric("Local", f"{round(res_p['Local'], 1)}%")
    c2.metric("Empate", f"{round(res_p['Empate'], 1)}%")
    c3.metric("Visitante", f"{round(res_p['Visitante'], 1)}%")
    st.caption("Escenario Poisson con medias introducidas manualmente; no consulta enfrentamientos históricos.")
    frame = pd.DataFrame({'Resultado':['Local','Empate','Visitante'], 'Probabilidad':[res_p[k] for k in ['Local','Empate','Visitante']]})
    themed_chart(px.bar(frame, x='Resultado', y='Probabilidad', color='Resultado', title='Probabilidades del escenario',color_discrete_sequence=['#21cfa4','#f3bc62','#63a9f5']),key='poisson_chart')


elif vista_seleccionada == "Bitácora Financiera":
    render_finance(BitacoraManager.guardar)

elif vista_seleccionada == "Oportunidades +EV":
    st.title("Oportunidades +EV")
    st.caption("Diferencia frente al consenso de cuotas. No representa rentabilidad garantizada.")
    opportunity_table(st.session_state.datos_cargados, positive_only=True)

elif vista_seleccionada == "Matriz de Cobertura":
    st.title("Matriz de cobertura")
    st.caption("Mercados con cuotas disponibles por torneo en el último escaneo.")
    coverage = [{'Liga':p['liga_origen'], 'Mercado':m} for p in st.session_state.datos_cargados.values() for m in p['mercados']]
    if coverage:
        frame = pd.crosstab(pd.DataFrame(coverage).Liga, pd.DataFrame(coverage).Mercado)
        st.dataframe(frame, width="stretch")
        themed_chart(px.imshow(frame,text_auto=True,color_continuous_scale='Teal',aspect='auto',title='Eventos por mercado y competición'),key='coverage_matrix')
    else: st.info("Escanea torneos para conocer la cobertura real.")

elif vista_seleccionada == "Generador Visual":
    st.title("Mi boleto")
    render_ticket(BitacoraManager.guardar)
    ticket = list(st.session_state.ticket_persistente.values())
    if ticket:
        frame = pd.DataFrame(ticket)[['evento','mercado','seleccion','cuota','casa']]
        st.dataframe(frame, hide_index=True, width="stretch")
        st.download_button("Descargar boleto CSV", frame.to_csv(index=False), "boleto.csv", "text/csv")
        if st.button("Enviar resumen a Telegram"):
            message = "Boleto Analizador\n" + "\n".join(f"{p['evento']}: {p['seleccion']} @ {p['cuota']}" for p in ticket)
            if enviar_telegram(message): st.success("Resumen enviado.")
            else: st.error("No se pudo enviar. Comprueba TG_TOKEN y TG_CHAT_ID en Secrets.")
    else: st.info("Añade selecciones desde el Radar para crear un resumen.")

elif vista_seleccionada == "Alineaciones & Bajas":
    st.title("Alineaciones y bajas")
    render_football_details(CATALOGO_LIGAS_UNIFICADO, AF_API_KEY)

elif vista_seleccionada == "Soporte":
    st.title("Centro de ayuda")
    st.markdown("1. Abre **Filtros de escaneo** en el menú lateral (☰ en celular).\n2. Elige torneos, mercados y casas; pulsa **Escanear Mercado**.\n3. Explora el Dashboard y añade selecciones desde Radar.\n4. Exporta la Bitácora para conservar tus registros.")
    st.subheader("Estado de configuración")
    st.dataframe(pd.DataFrame([{'Proveedor':name, 'Estado':'Clave configurada' if key else 'Sin clave'} for name,key in [('The Odds API',API_KEY),('API-Football',AF_API_KEY),('AllSports',ALLSPORTS_KEY),('SportAPI',SPORTAPI_KEY)]]), hide_index=True, width="stretch")
    st.caption("Una clave configurada no confirma conectividad, suscripción ni disponibilidad de cuotas. Las fuentes pueden devolver datos vacíos. API-Football consulta hasta tres páginas por liga para limitar consumo.")

st.divider()
st.caption("Analizador · Datos para comparar, contexto para decidir. Exporta tu bitácora para conservarla fuera de esta sesión.")
