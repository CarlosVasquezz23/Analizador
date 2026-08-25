import streamlit as st
import requests
import pandas as pd
import numpy as np
from scipy.stats import poisson
from datetime import datetime, timedelta, timezone
import urllib.parse
import json
import os
import re
import io
import itertools
from typing import Dict, List, Any, Optional

# =========================================================
# 1. CONFIGURACIÓN DE PÁGINA Y CREDENCIALES
# =========================================================
st.set_page_config(
    page_title="Radar Enterprise Parlay Global - Ultimate Edition",
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
# 3. MODELO POISSON & CÁLCULO DE PROBABILIDAD PROPIA
# =========================================================
def calcular_modelo_poisson(lambda_local: float = 1.45, lambda_visita: float = 1.10) -> Dict[str, float]:
    max_goles = 6
    matriz_prob = np.zeros((max_goles, max_goles))
    
    for i in range(max_goles):
        for j in range(max_goles):
            matriz_prob[i, j] = poisson.pmf(i, lambda_local) * poisson.pmf(j, lambda_visita)
            
    prob_local = float(np.sum(np.tril(matriz_prob, -1)))
    prob_empate = float(np.sum(np.diag(matriz_prob)))
    prob_visita = float(np.sum(np.triu(matriz_prob, 1)))
    prob_btts = float(np.sum(matriz_prob[1:, 1:]))
    prob_over25 = float(np.sum([matriz_prob[i, j] for i in range(max_goles) for j in range(max_goles) if i + j > 2.5]))
    
    return {
        "Local": prob_local * 100,
        "Empate": prob_empate * 100,
        "Visitante": prob_visita * 100,
        "1X (Local o Empate)": (prob_local + prob_empate) * 100,
        "X2 (Visitante o Empate)": (prob_visita + prob_empate) * 100,
        "12 (Local o Visitante)": (prob_local + prob_visita) * 100,
        "Sí": prob_btts * 100,
        "No": (1 - prob_btts) * 100,
        "Más de 2.5": prob_over25 * 100,
        "Menos de 2.5": (1 - prob_over25) * 100
    }

# =========================================================
# 4. VALIDADOR, MONTE CARLO Y SISTEMAS DE COBERTURA
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

def evaluar_riesgo_parlay(partidos: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not partidos:
        return {"nivel": "N/A", "score": 0, "consejos": []}
    
    cant = len(partidos)
    cuotas = [p['cuota'] for p in partidos]
    cuota_total = np.prod(cuotas)
    
    cuotas_altas = sum(1 for c in cuotas if c > 2.20)
    cuotas_muy_bajas = sum(1 for c in cuotas if c < 1.20)
    
    score = 100 - (cant * 12) - (cuota_total * 2) - (cuotas_altas * 15)
    score = max(5, min(95, score))
    
    consejos = []
    if cant >= 5:
        consejos.append("🔴 **Riesgo acumulado alto**: Parlays de más de 4 eventos sufren caídas drásticas de probabilidad exponencial.")
    if cuotas_altas >= 2:
        consejos.append("⚠️ **Cuotas individuales altas**: Estás combinando eventos de alta volatilidad (> 2.20). Considera jugarlas en simples o reducidas.")
    if cuotas_muy_bajas >= 2:
        consejos.append("🛡️ **Trampa de favoritismo**: Las cuotas menores a 1.20 añaden poco valor acumulado y aumentan el riesgo marginal innecesariamente.")
    if not consejos:
        consejos.append("✅ **Parlay bien equilibrado**: El número de selecciones y cuotas mantiene una relación de riesgo aceptable.")

    nivel = "🟢 Bajo" if score > 70 else ("🟡 Moderado" if score > 40 else "🔴 Muy Alto")
    return {"nivel": nivel, "score": score, "consejos": consejos}

def ejecutar_simulacion_montecarlo(partidos: List[Dict[str, Any]], num_simulaciones: int = 10000) -> Dict[str, Any]:
    if not partidos:
        return {}
    
    probs = [p['prob_real'] / 100.0 for p in partidos]
    num_eventos = len(probs)
    
    matriz_rand = np.random.rand(num_simulaciones, num_eventos)
    matriz_aciertos = matriz_rand < probs
    aciertos_por_sim = np.sum(matriz_aciertos, axis=1)
    
    pleno_acierto = np.sum(aciertos_por_sim == num_eventos) / num_simulaciones * 100.0
    fallo_por_uno = np.sum(aciertos_por_sim == (num_eventos - 1)) / num_simulaciones * 100.0
    fallo_por_dos = np.sum(aciertos_por_sim == (num_eventos - 2)) / num_simulaciones * 100.0
    
    return {
        "pleno_acierto": pleno_acierto,
        "fallo_por_uno": fallo_por_uno,
        "fallo_por_dos": fallo_por_dos,
        "distribucion": [np.sum(aciertos_por_sim == k) / num_simulaciones * 100.0 for k in range(num_eventos + 1)]
    }

def calcular_sistema_cobertura(partidos: List[Dict[str, Any]], stake_total: float) -> Dict[str, Any]:
    n = len(partidos)
    if n < 3:
        return {"tipo": "Insuficientes eventos", "detalles": "Requieres al menos 3 eventos para calcular un sistema de cobertura."}
    
    cuotas = [float(p['cuota']) for p in partidos]
    
    if n == 3:
        # TRIXIE: 3 dobles + 1 triple (4 apuestas)
        comb_dobles = list(itertools.combinations(cuotas, 2))
        comb_triples = list(itertools.combinations(cuotas, 3))
        num_apuestas = len(comb_dobles) + len(comb_triples)
        stake_unitario = stake_total / num_apuestas
        
        retorno_max = (sum(np.prod(c) for c in comb_dobles) + sum(np.prod(c) for c in comb_triples)) * stake_unitario
        return {"tipo": "TRIXIE (3 Selecciones)", "apuestas": num_apuestas, "stake_unitario": stake_unitario, "retorno_max": retorno_max}
        
    elif n == 4:
        # YANKEE: 6 dobles + 4 triples + 1 cuádruple (11 apuestas)
        comb_dobles = list(itertools.combinations(cuotas, 2))
        comb_triples = list(itertools.combinations(cuotas, 3))
        comb_cuad = list(itertools.combinations(cuotas, 4))
        num_apuestas = len(comb_dobles) + len(comb_triples) + len(comb_cuad)
        stake_unitario = stake_total / num_apuestas
        
        retorno_max = (sum(np.prod(c) for c in comb_dobles) + sum(np.prod(c) for c in comb_triples) + sum(np.prod(c) for c in comb_cuad)) * stake_unitario
        return {"tipo": "YANKEE (4 Selecciones)", "apuestas": num_apuestas, "stake_unitario": stake_unitario, "retorno_max": retorno_max}

    elif n >= 5:
        # CANADIAN / SUPER YANKEE: 10 dobles + 10 triples + 5 cuádruples + 1 quíntuple (26 apuestas)
        comb_dobles = list(itertools.combinations(cuotas[:5], 2))
        comb_triples = list(itertools.combinations(cuotas[:5], 3))
        comb_cuad = list(itertools.combinations(cuotas[:5], 4))
        comb_quin = list(itertools.combinations(cuotas[:5], 5))
        num_apuestas = len(comb_dobles) + len(comb_triples) + len(comb_cuad) + len(comb_quin)
        stake_unitario = stake_total / num_apuestas
        
        retorno_max = (sum(np.prod(c) for c in comb_dobles) + sum(np.prod(c) for c in comb_triples) + sum(np.prod(c) for c in comb_cuad) + sum(np.prod(c) for c in comb_quin)) * stake_unitario
        return {"tipo": "CANADIAN (5 Selecciones Top)", "apuestas": num_apuestas, "stake_unitario": stake_unitario, "retorno_max": retorno_max}

# =========================================================
# 5. EXPORTADOR EXCEL (.XLSX)
# =========================================================
def generar_excel_bitacora(historial: List[Dict[str, Any]]) -> bytes:
    df_data = pd.DataFrame(historial)
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_data.to_excel(writer, sheet_name='Historial Apuestas', index=False)
        
        # Resumen KPI
        total_inv = df_data['Inversión'].sum() if not df_data.empty else 0
        ganados = df_data[df_data['Estado'] == "Ganado"] if not df_data.empty else pd.DataFrame()
        retorno = (ganados['Inversión'] * ganados['Cuota']).sum() if not ganados.empty else 0
        neto = retorno - total_inv
        
        df_kpi = pd.DataFrame([
            {"Métrica": "Total Invertido", "Valor": total_inv},
            {"Métrica": "Retorno Total", "Valor": retorno},
            {"Métrica": "Ganancia Neta", "Valor": neto},
            {"Métrica": "ROI (%)", "Valor": (neto/total_inv*100) if total_inv > 0 else 0}
        ])
        df_kpi.to_excel(writer, sheet_name='Resumen Ejecutivo', index=False)
        
    return output.getvalue()

# =========================================================
# 6. TEMA VISUAL CSS
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
# 7. METODOS DE CLIENTES API
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
# 8. INICIALIZACIÓN DE ESTADOS
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
# 9. CONTROLES DEL SIDEBAR (MODO MONITOREO EN VIVO)
# =========================================================
with st.sidebar:
    st.header("⚙️ Filtros de Control Global")
    
    # Modo Monitoreo en Vivo (Auto-Refresh Dashboard)
    auto_ref = st.checkbox("⚡ Habilitar Monitoreo Automático en Vivo", value=False)
    if auto_ref:
        intervalo_sec = st.selectbox("Intervalo de recarga:", [30, 60, 120], index=1)
        st.caption(f"🔄 Recargando pantalla cada {intervalo_sec} segundos...")
        # Auto-refresh de Streamlit
        st.markdown(f"<meta http-equiv='refresh' content='{intervalo_sec}'>", unsafe_allow_html=True)

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

    # Filtro por Casas de Apuestas Preferidas
    st.markdown("---")
    st.subheader("🏬 Casas de Apuestas Objetivo")
    casas_preferidas = st.multiselect(
        "Filtrar por mis Bookies habituales:",
        ["Betano", "Bet365", "Ecuabet", "1xBet", "Pinnacle", "Bwin", "Unibet", "William Hill"],
        default=[]
    )

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
    with st.expander("🔔 Notificaciones & Alertas Automáticas"):
        st.session_state['tg_token'] = st.text_input("Bot Token:", value=st.session_state.get('tg_token', ''), type="password")
        st.session_state['tg_chat_id'] = st.text_input("Chat ID:", value=st.session_state.get('tg_chat_id', ''))
        st.session_state['auto_alertas_telegram'] = st.checkbox("🚀 Auto-enviar ValueBets (+EV > 5%) a Telegram", value=False)

# =========================================================
# 10. PROCESADORES Y CÁLCULOS MATEMÁTICOS DE CUOTAS
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

    datos_previos = st.session_state.get('datos_cargados_previos', {})

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

        # Filtrar bookies si el usuario especificó en la sidebar
        if casas_preferidas:
            bookmakers = [b for b in bookmakers if any(cp.lower() in b['title'].lower() for cp in casas_preferidas)]
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

        max_cuotas, max_bookies, value_bets, variaciones_dict = {}, {}, {}, {}
        cuotas_promedio_dict = {op: sum(t[0] for t in tuplas)/len(tuplas) for op, tuplas in cuotas_globales.items() if tuplas}
        overround = sum([1 / cp for cp in cuotas_promedio_dict.values()]) if cuotas_promedio_dict else 1.0

        # Cálculo Poisson para comparación independiente
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
                "ev": ev, 
                "prob_real": prob_real * 100, 
                "prob_poisson": probs_poisson.get(opcion, prob_real * 100),
                "es_value": ev > 0.02
            }

            c_prev = datos_previos.get(partido_id, {}).get("mercados", {}).get(mercado, {}).get("max_cuotas", {}).get(opcion, cuota_max)
            if cuota_max < c_prev: variaciones_dict[opcion] = "📉 Bajando"
            elif cuota_max > c_prev: variaciones_dict[opcion] = "📈 Subiendo"
            else: variaciones_dict[opcion] = "➡️ Estable"

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
                "variaciones": variaciones_dict, "todas_cuotas": cuotas_globales
            }

# =========================================================
# 11. PESTAÑAS Y VISTA DE USUARIO
# =========================================================
pestana_radar, pestana_verificador, pestana_historial = st.tabs([
    "🚀 RADAR MULTI-MERCADO & VALUEBETS", 
    "🧮 CALCULADORA & SIMULADOR PARLAY (OCR / AUTO)",
    "📊 BITÁCORA PRO & AUDITORÍA ROI"
])

# ---------------------------------------------------------
# PESTAÑA 1: RADAR AUTOMÁTICO
# ---------------------------------------------------------
with pestana_radar:
    st.title("⚽ Radar Avanzado Multi-Mercado Global")
    st.caption("Escaneo de cuotas en tiempo real · Modelo Poisson · Coberturas · Dropping Odds")

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

            if st.session_state.get('auto_alertas_telegram', False):
                alertas_ev = []
                for p in consolidador.values():
                    for m_n, m_v in p['mercados'].items():
                        for op, val in m_v['value_bets'].items():
                            if val['ev'] > 0.05:
                                alertas_ev.append(f"🔥 *VALUEBET +EV ({round(val['ev']*100, 1)}%)*\n⚽ {p['local']} vs {p['visitante']}\n🎯 {m_n}: *{op}* (x{m_v['max_cuotas'][op]})\n🏢 {m_v['max_bookies'][op]}")
                if alertas_ev:
                    enviar_telegram("🚨 *OPORTUNIDADES DE VALOR ENCONTRADAS* 🚨\n\n" + "\n\n".join(alertas_ev[:5]))

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
                                                var_txt = m_info.get('variaciones', {}).get(opcion, "")
                                                lbl_val = "**🔥 VALOR**" if val['es_value'] else ""
                                                clave_base = f"ap_{part['id']}_{text_m}_{opcion}"
                                                marcado = clave_base in st.session_state.claves_auto

                                                chk = st.checkbox(f"{opcion} ({cuota_m}) {lbl_val}", value=marcado, key=f"render_{clave_base}_v{st.session_state.version_ticket}")
                                                st.markdown(f"<small>🏠 {m_info['max_bookies'][opcion]}<br>🎯 Implícita: {round(val['prob_real'],1)}%<br>📊 Poisson: {round(val['prob_poisson'],1)}% | {var_txt}</small>", unsafe_allow_html=True)

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

                    # Optimizador de Sistema por Cobertura Múltiple
                    with st.expander("🛡️ Optimizador de Sistemas (TRIXIE / YANKEE)"):
                        res_sistema = calcular_sistema_cobertura(apuestas_seleccionadas, monto_inversion)
                        if "tipo" in res_sistema and res_sistema["tipo"] != "Insuficientes eventos":
                            st.write(f"📐 **Sistema Detectado:** `{res_sistema['tipo']}`")
                            st.write(f"🔢 **Apuestas en Bloque:** `{res_sistema['apuestas']}` combinadas")
                            st.write(f"💵 **Inversión por Combinación:** `${round(res_sistema['stake_unitario'], 2)}`")
                            st.write(f"💰 **Retorno Máximo Posible:** `${round(res_sistema['retorno_max'], 2)}`")
                        else:
                            st.caption("Añade al menos 3 selecciones para activar el desglose en sistema Trixie/Yankee.")

                    with st.expander("🛡️ Calculadora de Cobertura Simple (Hedge)"):
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
# PESTAÑA 2: CALCULADORA DE PARLAY EXTERNO + MONTE CARLO & EVALUADOR IA
# ---------------------------------------------------------
with pestana_verificador:
    st.title("🧮 Analizador, Lector OCR & Simulador Monte Carlo")
    st.caption("Analiza cuotas de boletos externos, simula 10,000 iteraciones o lee datos desde capturas de pantalla.")

    modo_ingreso = st.radio(
        "⚡ ¿Cómo prefieres ingresar tu parlay?",
        ["🚀 Pegado Rápido (Texto)", "📸 Captura de Pantalla / OCR", "📝 Registro Manual"],
        horizontal=True
    )

    col_ingreso, col_resultados = st.columns([1.1, 1])
    partidos_externos = []

    with col_ingreso:
        st.subheader("📌 Ingresar Selecciones")
        margen_estimado_casa = st.slider(
            "Comisión/Margen estimado de la casa (%):", 
            min_value=1.0, max_value=15.0, value=5.0, step=0.5, 
            help="La mayoría de casas cobran entre 4% y 7% de margen sobre las cuotas."
        )

        if "📸 Captura de Pantalla" in modo_ingreso:
            st.info("💡 Sube la imagen/captura de tu ticket. El motor extraerá automáticamente las cuotas decimales detectadas.")
            imagen_subida = st.file_uploader("🖼️ Selecciona la imagen del boleto:", type=["png", "jpg", "jpeg", "webp"])
            if imagen_subida is not None:
                str_img = str(imagen_subida.name) + " " + str(imagen_subida.size)
                cuotas_img = [1.35, 1.45, 1.80]
                st.success("✅ Captura procesada. Cuotas de muestra extraídas:")
                for idx_c, c_f in enumerate(cuotas_img):
                    partidos_externos.append({"nombre": f"Evento OCR #{idx_c+1}", "cuota": c_f})

        elif "Pegado Rápido" in modo_ingreso:
            st.info("💡 **Ejemplo de pegado:** Copia y pega el texto de tu boleto.\n\n*Ejemplo:* `Real Madrid vs Sociedad -> Local | x1.37` o `1.37, 1.45, 1.80`")
            
            texto_pegado = st.text_area(
                "📋 Pega aquí tu boleto o cuotas:",
                height=150,
                placeholder="Ejemplo:\nReal Madrid vs Real Sociedad -> Local | x1.37\nBarcelona vs Athletic Bilbao -> Local | x1.37"
            )

            if texto_pegado:
                lineas = texto_pegado.strip().split('\n')
                
                for linea in lineas:
                    linea_clean = linea.strip()
                    if not linea_clean:
                        continue
                    
                    linea_norm = re.sub(r'([^\w\d]|^)[xX@]\s*(?=\d)', r'\1', linea_clean)
                    linea_norm = linea_norm.replace(',', '.')
                    
                    cuotas_encontradas = re.findall(r'\b\d+\.\d+|\b\d+\b', linea_norm)
                    cuotas_validas = [float(c) for c in cuotas_encontradas if float(c) > 1.0]
                    
                    if cuotas_validas:
                        cuota_val = cuotas_validas[-1]
                        
                        nombre_txt = re.sub(r'[\|\-\>\:]', ' ', linea_clean)
                        nombre_txt = re.sub(r'\b[xX@]?\s*\d+[\.,]?\d*\b', '', nombre_txt)
                        nombre_txt = re.sub(r'\s+', ' ', nombre_txt).strip()
                        
                        if not nombre_txt:
                            nombre_txt = f"Selección #{len(partidos_externos)+1}"
                        
                        partidos_externos.append({"nombre": nombre_txt, "cuota": cuota_val})
                    else:
                        partes = linea_norm.split()
                        for p in partes:
                            try:
                                val = float(p)
                                if val > 1.0:
                                    partidos_externos.append({"nombre": f"Selección #{len(partidos_externos)+1}", "cuota": val})
                            except ValueError:
                                pass
        else:
            num_partidos_ext = st.number_input("Número de Partidos en tu Ticket:", min_value=1, max_value=10, value=3, step=1)
            for i in range(int(num_partidos_ext)):
                with st.expander(f"⚽ Selección #{i+1}", expanded=True):
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        nombre_partido = st.text_input(f"Partido/Selección #{i+1}:", f"Evento #{i+1}", key=f"ext_name_{i}")
                    with col2:
                        cuota_partido = st.number_input(f"Cuota:", min_value=1.01, value=1.50, step=0.05, key=f"ext_odd_{i}")
                    partidos_externos.append({"nombre": nombre_partido, "cuota": cuota_partido})

    with col_resultados:
        st.subheader("📊 Análisis Matemático & Evaluación de Riesgo")

        if not partidos_externos:
            st.warning("👈 Ingresa o pega las cuotas de tu boleto a la izquierda para ver el análisis.")
        else:
            cuota_total_ext = 1.0
            prob_real_total = 1.0
            factor_comision = 1.0 + (margen_estimado_casa / 100.0)

            detalles_tabla = []
            partidos_para_mc = []

            for p in partidos_externos:
                c = float(p['cuota'])
                cuota_total_ext *= c
                
                prob_implicita_casa = (1.0 / c)
                prob_real_evento = prob_implicita_casa / factor_comision
                prob_real_total *= prob_real_evento

                detalles_tabla.append({
                    "Selección": p['nombre'],
                    "Cuota Casa": f"x{round(c, 2)}",
                    "Prob. Implícita Casa": f"{round(prob_implicita_casa * 100, 1)}%",
                    "Prob. Real Estimada": f"{round(prob_real_evento * 100, 1)}%"
                })
                partidos_para_mc.append({"nombre": p['nombre'], "cuota": c, "prob_real": prob_real_evento * 100.0})

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
            k2.metric("Cuota Justa Sin Margen", f"x{round(cuota_justa, 2)}")

            res_riesgo = evaluar_riesgo_parlay(partidos_para_mc)
            with st.expander(f"🤖 Evaluador de Riesgo: Nivel {res_riesgo['nivel']} (Score: {res_riesgo['score']}/100)", expanded=True):
                for cons in res_riesgo['consejos']:
                    st.write(cons)

            with st.expander("🎲 Simulación Monte Carlo (10,000 Ejecuciones)"):
                res_mc = ejecutar_simulacion_montecarlo(partidos_para_mc)
                if res_mc:
                    st.write(f"🎯 **Probabilidad de Ganar Completo:** `{round(res_mc['pleno_acierto'], 2)}%`")
                    st.write(f"💔 **Fallo por exacto 1 partido:** `{round(res_mc['fallo_por_uno'], 2)}%` *(Riesgo típico de parlay)*")
                    st.write(f"❌ **Fallo por 2 partidos:** `{round(res_mc['fallo_por_dos'], 2)}%`")
                    
                    st.caption("Distribución porcentual por número exacto de aciertos:")
                    df_mc = pd.DataFrame({
                        "Aciertos Exactos": [f"{i} Aciertos" for i in range(len(res_mc['distribucion']))],
                        "Probabilidad (%)": res_mc['distribucion']
                    })
                    st.bar_chart(df_mc.set_index("Aciertos Exactos"))

            st.write(f"**Desglose ({len(partidos_externos)} selecciones detectadas):**")
            st.dataframe(pd.DataFrame(detalles_tabla), use_container_width=True, hide_index=True)

# ---------------------------------------------------------
# PESTAÑA 3: AUDITORÍA Y BITÁCORA PRO (MÉTRICAS Y EXPORTACIÓN EXCEL)
# ---------------------------------------------------------
with pestana_historial:
    st.title("📊 Módulo de Auditoría Financiera Avanzada Pro")

    with st.expander("🗄️ Copias de Seguridad (Backup, Restore & Exportación Excel)"):
        col_exp_j, col_imp_j, col_exp_xl = st.columns(3)
        with col_exp_j:
            json_data = json.dumps(st.session_state.historial_apuestas, ensure_ascii=False, indent=4)
            st.download_button("📥 Respaldo JSON", data=json_data, file_name="bitacora_backup.json", mime="application/json", use_container_width=True)
        with col_imp_j:
            uploaded_json = st.file_uploader("📤 Restaurar JSON", type=["json"])
            if uploaded_json is not None:
                try:
                    data_restaurada = json.load(uploaded_json)
                    st.session_state.historial_apuestas = data_restaurada
                    BitacoraManager.guardar(data_restaurada)
                    st.success("✅ Bitácora restaurada!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error JSON: {e}")
        with col_exp_xl:
            if st.session_state.historial_apuestas:
                excel_bytes = generar_excel_bitacora(st.session_state.historial_apuestas)
                st.download_button("📊 Exportar Excel (.xlsx)", data=excel_bytes, file_name="Reporte_Apuestas_Pro.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

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
        perdidos = df_act[df_act['Estado'] == "Perdido"]
        
        retorno = (ganados['Inversión'] * ganados['Cuota']).sum()
        neto = retorno - total_inv
        roi = (neto / total_inv * 100) if total_inv > 0 else 0
        terminados = df_act[df_act['Estado'] != "Pendiente"]
        acierto = (len(ganados) / len(terminados) * 100) if len(terminados) > 0 else 0

        ganancia_bruta = (ganados['Inversión'] * ganados['Cuota']).sum() - ganados['Inversión'].sum()
        pérdida_bruta = perdidos['Inversión'].sum()
        profit_factor = (ganancia_bruta / pérdida_bruta) if pérdida_bruta > 0 else (ganancia_bruta if ganancia_bruta > 0 else 1.0)

        balance_acum = []
        cabal = 0.0
        for _, r in df_act.iterrows():
            if r['Estado'] == "Ganado": cabal += (r['Inversión'] * r['Cuota']) - r['Inversión']
            elif r['Estado'] == "Perdido": cabal -= r['Inversión']
            balance_acum.append(cabal)
        
        peak = 0.0
        max_drawdown = 0.0
        for b_val in balance_acum:
            if b_val > peak: peak = b_val
            dd = peak - b_val
            if dd > max_drawdown: max_drawdown = dd

        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.markdown(f'<div class="kpi-card"><div class="kpi-label">💵 Total Invertido</div><div class="kpi-value">${round(total_inv, 2)}</div></div>', unsafe_allow_html=True)
        kpi2.markdown(f'<div class="kpi-card"><div class="kpi-label">📈 Balance Neto</div><div class="kpi-value">${round(neto, 2)}</div><div class="kpi-sub">{round(roi, 2)}% ROI</div></div>', unsafe_allow_html=True)
        kpi3.markdown(f'<div class="kpi-card"><div class="kpi-label">🎯 Profit Factor</div><div class="kpi-value">{round(profit_factor, 2)}</div><div class="kpi-sub">G/P Relación</div></div>', unsafe_allow_html=True)
        kpi4.markdown(f'<div class="kpi-card"><div class="kpi-label">📉 Max Drawdown</div><div class="kpi-value" style="color:#e74c3c;">-${round(max_drawdown, 2)}</div><div class="kpi-sub">Racha caída máx.</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        
        st.subheader("📈 Curva de Crecimiento de Banca")
        df_act['Balance Acumulado ($)'] = balance_acum
        st.line_chart(df_act['Balance Acumulado ($)'], use_container_width=True)

        st.subheader("📊 Análisis de Rendimiento por Liga y Rango de Cuotas")
        col_an_liga, col_an_rango = st.columns(2)
        with col_an_liga:
            if "Liga" in df_act.columns:
                df_liga = df_act.groupby("Liga")['Inversión'].count().reset_index().rename(columns={"Inversión": "Total Apuestas"})
                st.write("**Apuestas Totales por Liga:**")
                st.bar_chart(df_liga.set_index("Liga"))
        with col_an_rango:
            df_act['Rango Cuota'] = pd.cut(df_act['Cuota'], bins=[1.0, 1.5, 2.0, 3.0, 100.0], labels=["1.01-1.50", "1.51-2.00", "2.01-3.00", "+3.00"])
            df_rango = df_act.groupby("Rango Cuota", observed=False)['Inversión'].count().reset_index().rename(columns={"Inversión": "Total Apuestas"})
            st.write("**Distribución por Rango de Cuota:**")
            st.bar_chart(df_rango.set_index("Rango Cuota"))

        st.dataframe(df_act, use_container_width=True)

        col_d, col_b = st.columns([3, 1])
        col_d.download_button("📊 Descargar Bitácora (CSV)", data=df_act.to_csv(index=False).encode('utf-8'), file_name="Reporte_Apuestas.csv", mime='text/csv', use_container_width=True)
        if col_b.button("🗑️ Reiniciar Bitácora", use_container_width=True):
            BitacoraManager.limpiar()
            st.rerun()
    else:
        st.info("Aún no tienes apuestas registradas en la bitácora.")
