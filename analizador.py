import streamlit as st
import requests
import pandas as pd
import numpy as np
import math
from datetime import datetime, timedelta, timezone
import urllib.parse
import json
import os
import re
import io
import itertools
from typing import Dict, List, Any, Optional
from PIL import Image

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
# 3. MODELADO MATEMÁTICO: POISSON, DIXON-COLES & LIVE POISSON
# =========================================================
def poisson_pmf(k: int, mu: float) -> float:
    return (math.pow(mu, k) * math.exp(-mu)) / math.factorial(k)

def dixon_coles_factor(i: int, j: int, lambda_l: float, lambda_v: float, rho: float = -0.13) -> float:
    if i == 0 and j == 0:
        return 1.0 - (lambda_l * lambda_v * rho)
    elif i == 1 and j == 0:
        return 1.0 + (lambda_v * rho)
    elif i == 0 and j == 1:
        return 1.0 + (lambda_l * rho)
    elif i == 1 and j == 1:
        return 1.0 - rho
    return 1.0

def calcular_impacto_bajas(lambda_base: float, peso_bajas: float) -> float:
    factor_ajuste = max(0.3, 1.0 - (peso_bajas / 100.0))
    return lambda_base * factor_ajuste

def calcular_modelo_poisson(lambda_local: float = 1.45, lambda_visita: float = 1.10, usar_dixon_coles: bool = True) -> Dict[str, float]:
    max_goles = 6
    matriz_prob = np.zeros((max_goles, max_goles))
    
    for i in range(max_goles):
        for j in range(max_goles):
            p_base = poisson_pmf(i, lambda_local) * poisson_pmf(j, lambda_visita)
            tau = dixon_coles_factor(i, j, lambda_local, lambda_visita) if usar_dixon_coles else 1.0
            matriz_prob[i, j] = p_base * tau
            
    soma = np.sum(matriz_prob)
    if soma > 0:
        matriz_prob /= soma

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

def calcular_poisson_live(minuto: int, goles_loc: int, goles_vis: int, lambda_l_base: float = 1.45, lambda_v_base: float = 1.10) -> Dict[str, float]:
    """Recalcula las probabilidades dinámicas según el tiempo restante y el marcador actual."""
    tiempo_restante_pct = max(0.01, (90 - min(89, minuto)) / 90.0)
    lambda_l_rem = lambda_l_base * tiempo_restante_pct
    lambda_v_rem = lambda_v_base * tiempo_restante_pct

    max_goles_rem = 5
    matriz_prob = np.zeros((max_goles_rem, max_goles_rem))

    for i in range(max_goles_rem):
        for j in range(max_goles_rem):
            matriz_prob[i, j] = poisson_pmf(i, lambda_l_rem) * poisson_pmf(j, lambda_v_rem)

    soma = np.sum(matriz_prob)
    if soma > 0:
        matriz_prob /= soma

    prob_local_win, prob_empate_win, prob_visita_win = 0.0, 0.0, 0.0
    for i in range(max_goles_rem):
        for j in range(max_goles_rem):
            tot_loc = goles_loc + i
            tot_vis = goles_vis + j
            if tot_loc > tot_vis: prob_local_win += matriz_prob[i, j]
            elif tot_loc == tot_vis: prob_empate_win += matriz_prob[i, j]
            else: prob_visita_win += matriz_prob[i, j]

    return {
        "Local": float(prob_local_win * 100),
        "Empate": float(prob_empate_win * 100),
        "Visitante": float(prob_visita_win * 100),
        "1X (Local o Empate)": float((prob_local_win + prob_empate_win) * 100),
        "X2 (Visitante o Empate)": float((prob_visita_win + prob_empate_win) * 100),
        "12 (Local o Visitante)": float((prob_local_win + prob_visita_win) * 100),
        "Sí": 50.0, "No": 50.0, "Más de 2.5": 50.0, "Menos de 2.5": 50.0
    }

# =========================================================
# 4. VALIDADOR, ARBITRAJE, SHARPE RATIO & RUINA MONTE CARLO
# =========================================================
def detectar_surebet(cuotas_max_dict: Dict[str, float]) -> Dict[str, Any]:
    if not cuotas_max_dict or len(cuotas_max_dict) < 2:
        return {"es_surebet": False, "overround": 1.0, "lucro": 0.0}
    
    overround = sum(1.0 / float(c) for c in cuotas_max_dict.values())
    es_surebet = overround < 1.0
    lucro = ((1.0 / overround) - 1.0) * 100.0 if es_surebet else 0.0
    
    return {"es_surebet": es_surebet, "overround": overround, "lucro": lucro}

def calcular_sharpe_parlay(cuota_total: float, prob_combinada: float) -> float:
    ev = (cuota_total * prob_combinada) - 1.0
    varianza = (prob_combinada * ((cuota_total - 1.0) ** 2)) + ((1.0 - prob_combinada) * ((-1.0) ** 2))
    desviacion = math.sqrt(varianza) if varianza > 0 else 1.0
    return ev / desviacion

def simular_riesgo_ruina_banca(bankroll_inicial: float, stake_promedio: float, prob_exito: float, cuota_prom: float, num_apuestas: int = 200, sims: int = 2000) -> Dict[str, float]:
    bancarrota_count = 0
    final_bankrolls = []
    
    for _ in range(sims):
        b = bankroll_inicial
        for _ in range(num_apuestas):
            if b <= 0:
                bancarrota_count += 1
                b = 0
                break
            if np.random.rand() < prob_exito:
                b += stake_promedio * (cuota_prom - 1.0)
            else:
                b -= stake_promedio
        final_bankrolls.append(b)
        
    return {
        "prob_ruina": (bancarrota_count / sims) * 100.0,
        "banca_promedio_final": float(np.mean(final_bankrolls))
    }

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
        comb_dobles = list(itertools.combinations(cuotas, 2))
        comb_triples = list(itertools.combinations(cuotas, 3))
        num_apuestas = len(comb_dobles) + len(comb_triples)
        stake_unitario = stake_total / num_apuestas
        retorno_max = (sum(np.prod(c) for c in comb_dobles) + sum(np.prod(c) for c in comb_triples)) * stake_unitario
        return {"tipo": "TRIXIE (3 Selecciones)", "apuestas": num_apuestas, "stake_unitario": stake_unitario, "retorno_max": retorno_max}
        
    elif n == 4:
        comb_dobles = list(itertools.combinations(cuotas, 2))
        comb_triples = list(itertools.combinations(cuotas, 3))
        comb_cuad = list(itertools.combinations(cuotas, 4))
        num_apuestas = len(comb_dobles) + len(comb_triples) + len(comb_cuad)
        stake_unitario = stake_total / num_apuestas
        retorno_max = (sum(np.prod(c) for c in comb_dobles) + sum(np.prod(c) for c in comb_triples) + sum(np.prod(c) for c in comb_cuad)) * stake_unitario
        return {"tipo": "YANKEE (4 Selecciones)", "apuestas": num_apuestas, "stake_unitario": stake_unitario, "retorno_max": retorno_max}

    elif n >= 5:
        comb_dobles = list(itertools.combinations(cuotas[:5], 2))
        comb_triples = list(itertools.combinations(cuotas[:5], 3))
        comb_cuad = list(itertools.combinations(cuotas[:5], 4))
        comb_quin = list(itertools.combinations(cuotas[:5], 5))
        num_apuestas = len(comb_dobles) + len(comb_triples) + len(comb_cuad) + len(comb_quin)
        stake_unitario = stake_total / num_apuestas
        retorno_max = (sum(np.prod(c) for c in comb_dobles) + sum(np.prod(c) for c in comb_triples) + sum(np.prod(c) for c in comb_cuad) + sum(np.prod(c) for c in comb_quin)) * stake_unitario
        return {"tipo": "CANADIAN (5 Selecciones Top)", "apuestas": num_apuestas, "stake_unitario": stake_unitario, "retorno_max": retorno_max}

# =========================================================
# 5. EXPORTADOR CSV
# =========================================================
def generar_csv_bitacora(historial: List[Dict[str, Any]]) -> bytes:
    df_data = pd.DataFrame(historial)
    return df_data.to_csv(index=False).encode('utf-8')

# =========================================================
# 6. TEMA VISUAL CSS AVANZADO
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

    .block-container {
        padding-top: 3.2rem !important;
        padding-bottom: 2rem !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
        max-width: 98% !important;
    }

    h1 {
        font-family: 'Inter', sans-serif;
        font-weight: 800 !important;
        letter-spacing: -0.02em;
        font-size: clamp(1.5rem, 2.2vw, 2.1rem) !important;
        background: linear-gradient(90deg, #ffffff 0%, #00d2d3 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem !important;
    }

    div[data-testid="stRadio"] > div {
        flex-direction: row !important;
        gap: 6px !important;
    }

    div[data-testid="stRadio"] label {
        background-color: var(--rg-card) !important;
        border: 1px solid var(--rg-border) !important;
        border-radius: 8px !important;
        padding: 6px 12px !important;
        font-weight: 700 !important;
        font-size: 12px !important;
        color: var(--rg-text-soft) !important;
        cursor: pointer;
        transition: all 0.2s ease;
    }

    div[data-testid="stRadio"] label:hover {
        border-color: var(--rg-accent) !important;
        color: #ffffff !important;
    }

    div[data-testid="stRadio"] label[data-checked="true"] {
        background-color: var(--rg-card-alt) !important;
        border-color: var(--rg-accent) !important;
        color: #ffffff !important;
    }

    div[data-baseweb="tab-list"] {
        gap: 4px !important;
        margin-bottom: 18px !important;
        padding-bottom: 4px !important;
        border-bottom: 1px solid var(--rg-border) !important;
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
        white-space: nowrap !important;
    }

    button[data-baseweb="tab"] {
        font-weight: 700 !important;
        font-size: 11.5px !important;
        padding: 7px 12px !important;
        border-radius: 8px !important;
        color: #8a94a6 !important;
        background-color: transparent !important;
        transition: all 0.2s ease;
        border: 1px solid transparent !important;
        flex-shrink: 0 !important;
    }

    button[data-baseweb="tab"][aria-selected="true"] {
        color: #ffffff !important;
        background-color: var(--rg-card-alt) !important;
        border: 1px solid var(--rg-border) !important;
    }

    button[data-baseweb="tab"]:hover {
        color: var(--rg-accent) !important;
    }

    div[data-baseweb="tab-highlight"] { 
        background-color: var(--rg-accent) !important; 
        height: 2px !important;
        box-shadow: 0 0 10px var(--rg-accent);
    }

    .empty-state-card {
        background: linear-gradient(135deg, #121722 0%, #0d1017 100%);
        border: 1px solid var(--rg-border);
        border-radius: 16px;
        padding: 30px;
        box-shadow: 0 12px 30px rgba(0,0,0,0.35);
    }
    
    .empty-state-step {
        background: var(--rg-card-alt);
        border: 1px solid var(--rg-border-soft);
        border-radius: 12px;
        padding: 18px 14px;
        text-align: center;
        transition: all 0.25s ease-in-out;
    }

    .empty-state-step:hover {
        border-color: rgba(0, 210, 211, 0.4);
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(0, 210, 211, 0.08);
    }

    .step-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        border-radius: 50%;
        background: rgba(0, 210, 211, 0.12);
        color: var(--rg-accent);
        border: 1px solid rgba(0, 210, 211, 0.4);
        font-weight: 800;
        font-size: 14px;
        margin-bottom: 12px;
    }

    div[data-testid="stNotification"] {
        border-radius: 10px !important;
        border: 1px solid var(--rg-border) !important;
        background-color: var(--rg-card-alt) !important;
    }

    div[data-baseweb="input"] {
        border-radius: 8px !important;
        border: 1px solid var(--rg-border) !important;
        background-color: var(--rg-card) !important;
    }

    .hint-box {
        background: rgba(0, 210, 211, 0.05);
        border: 1px solid rgba(0, 210, 211, 0.25);
        border-radius: 10px;
        padding: 10px 14px;
        margin-bottom: 12px;
        font-size: 12.5px;
        color: #cfd8e3;
    }

    .match-header { font-size: 16px; font-weight: 700; margin-bottom: 2px; letter-spacing: -0.01em; }
    .liga-chip {
        display: inline-block; font-size: 10px; font-weight: 700; text-transform: uppercase;
        letter-spacing: 0.04em; color: var(--rg-accent); background: rgba(0,210,211,0.10);
        border: 1px solid rgba(0,210,211,0.35); border-radius: 999px; padding: 2px 8px; margin-bottom: 4px;
    }
    .kickoff-chip {
        display: inline-block; font-size: 11px; font-weight: 600; color: #cfd8e3;
        background: var(--rg-card-alt); border: 1px solid var(--rg-border); border-radius: 999px;
        padding: 2px 8px; margin-top: 2px;
    }

    .creditos-caja-pro {
        background: linear-gradient(135deg, #151b26 0%, #0f131a 100%);
        padding: 8px 12px;
        border-radius: 8px;
        border: 1px solid var(--rg-border);
        margin-bottom: 8px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    div[class*="st-key-match_"] {
        background: linear-gradient(180deg, var(--rg-card) 0%, #0f131b 100%) !important;
        border: 1px solid var(--rg-border) !important;
        border-radius: 14px !important;
        box-shadow: 0 2px 10px rgba(0,0,0,0.25);
    }

    div[class*="st-key-ticket_card"] {
        background: linear-gradient(180deg, #12161f 0%, #0d1017 100%) !important;
        border: 2px dashed rgba(0,210,211,0.45) !important;
        border-radius: 16px !important;
        box-shadow: 0 0 15px rgba(0,210,211,0.08);
    }
    .ticket-titulo {
        font-family: 'Inter', sans-serif; font-weight: 800; font-size: 14px; letter-spacing: 0.04em;
        text-transform: uppercase; color: var(--rg-accent); text-align: center; margin-bottom: 6px;
    }
    .ticket-item { border-bottom: 1px dashed var(--rg-border); padding: 6px 0 8px 0; }
    .ticket-item:last-of-type { border-bottom: none; }
    .ticket-cuota-tag {
        font-family: 'JetBrains Mono', monospace; font-weight: 700; color: var(--rg-accent);
        background: rgba(0,210,211,0.08); border-radius: 6px; padding: 1px 6px; font-size: 12px;
    }

    div[data-testid="stSegmentedControl"] button {
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 12px !important;
    }

    div.stButton > button {
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 13px !important;
        padding: 6px 12px !important;
        transition: transform .08s ease;
        border: 1px solid var(--rg-border) !important;
    }
    div.stButton > button:hover { transform: translateY(-1px); }
    div.stButton > button[kind="primary"] {
        background: linear-gradient(90deg, #00b3b4, #00d2d3) !important;
        color: #000000 !important;
        font-weight: 700 !important;
        border: none !important;
        box-shadow: 0 4px 16px rgba(0,210,211,0.25);
    }

    div[data-testid="stMetric"] {
        background: var(--rg-card-alt); border: 1px solid var(--rg-border);
        border-radius: 10px; padding: 8px 12px;
    }

    .kpi-card {
        border-radius: 12px;
        padding: 16px;
        border: 1px solid var(--rg-border);
        background: linear-gradient(145deg, var(--rg-card) 0%, #0d1017 100%);
        box-shadow: 0 8px 20px rgba(0,0,0,0.3);
        height: 100%;
    }
    .kpi-label { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--rg-text-soft); margin-bottom: 6px; }
    .kpi-value { font-family: 'JetBrains Mono', monospace; font-size: clamp(20px, 2.5vw, 28px); font-weight: 700; line-height: 1.1; }
    .kpi-sub { font-size: 11.5px; margin-top: 6px; font-weight: 500; }
    </style>
""", unsafe_allow_html=True)

# =========================================================
# 7. CLIENTES API Y MAPEO EXTENDIDO DE LIGAS
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

todas_las_ligas = {
    "EU Champions League": ["soccer_uefa_champions_league", "soccer_uefa_champions_league_qualification"],
    "EU Europa League": ["soccer_uefa_europa_league", "soccer_uefa_europa_league_qualification"],
    "Copa Libertadores": ["soccer_conmebol_copa_libertadores"],
    "Copa Sudamericana": ["soccer_conmebol_copa_sudamericana"],
    "Argentina Primera Division": ["soccer_argentina_primera_division"],
    "Chile Primera Division": ["soccer_chile_campeonato"],
    "Brasil Brasileirao": ["soccer_brazil_campeonato"],
    "Mexico Liga MX": ["soccer_mexico_liga_mx"],
    "Spain La Liga": ["soccer_spain_la_liga"],
    "England Premier League": ["soccer_epl"],
    "Italy Serie A": ["soccer_italy_serie_a"],
    "Germany Bundesliga": ["soccer_germany_bundesliga"],
    "France Ligue 1": ["soccer_france_ligue_one"]
}

diccionario_mercados = {
    "1X2 (Ganador)": "h2h",
    "Doble Oportunidad": "double_chance",
    "Ambos Anotan (BTTS)": "btts",
    "Goles Más/Menos 2.5": "totals"
}

# =========================================================
# 8. ESTADOS DE SESIÓN
# =========================================================
if 'historial_apuestas' not in st.session_state:
    st.session_state.historial_apuestas = BitacoraManager.cargar()
if 'version_ticket' not in st.session_state:
    st.session_state.version_ticket = 0
if 'datos_cargados' not in st.session_state:
    st.session_state.datos_cargados = {}
if 'datos_cargados_previos' not in st.session_state:
    st.session_state.datos_cargados_previos = {}
if 'historico_cuotas_live' not in st.session_state:
    st.session_state.historico_cuotas_live = {}
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
# 9. SIDEBAR ORGANIZADO EN ACCORDEONES
# =========================================================
with st.sidebar:
    st.header("⚙️ Control Global")
    
    auto_ref = st.checkbox("⚡ Monitoreo en Vivo", value=False)
    if auto_ref:
        intervalo_sec = st.selectbox("Recarga cada:", [30, 60, 120], index=1)
        st.markdown(f"<meta http-equiv='refresh' content='{intervalo_sec}'>", unsafe_allow_html=True)

    st.markdown(f"""
        <div class="creditos-caja-pro">
            <div>
                <small style="color:#8a94a6; text-transform:uppercase; font-weight:700;">Odds API</small><br>
                <span style="font-size:16px; font-weight:bold; color:#00d2d3;">🔑 {st.session_state.creditos_restantes}</span>
            </div>
            <div>🟢</div>
        </div>
        <div class="creditos-caja-pro">
            <div>
                <small style="color:#8a94a6; text-transform:uppercase; font-weight:700;">Football API</small><br>
                <span style="font-size:16px; font-weight:bold; color:#feca57;">🔑 {st.session_state.creditos_restantes_af}</span>
            </div>
            <div>🟡</div>
        </div>
    """, unsafe_allow_html=True)

    with st.expander("⚽ Selección de Torneos", expanded=True):
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

        ligas_sels = st.multiselect("Ligas principales:", list(todas_las_ligas.keys()), key="ligas_sels_widget")
        
        habilitar_af = st.checkbox("Habilitar Ligas LATAM (API-Football)", value=True)
        ligas_af_sels = st.multiselect("Ligas extra:", list(AF_LEAGUE_IDS.keys()), default=[]) if habilitar_af else []

    with st.expander("🏬 Casas y Mercados", expanded=True):
        casas_preferidas = st.multiselect(
            "Mis Bookies:",
            ["Betano", "Bet365", "Ecuabet", "1xBet", "Pinnacle", "Bwin", "Unibet", "William Hill"],
            default=[]
        )
        mercados_sels = st.multiselect("Mercados:", list(diccionario_mercados.keys()), default=["1X2 (Ganador)"])
        sin_limite_fecha = st.checkbox("🌐 Traer todo sin filtro de días", value=False)
        if not sin_limite_fecha:
            tiempo_sel = st.selectbox(
                "Ventana de tiempo:", 
                ["24 Horas", "48 Horas", "7 Días", "14 Días", "21 Días", "30 Días"], 
                index=2
            )
            if "Horas" in tiempo_sel:
                limite_h = int(tiempo_sel.split()[0])
            else:
                limite_h = int(tiempo_sel.split()[0]) * 24
        else:
            limite_h = 999999

    with st.expander("🧮 Banca & Criterio Kelly"):
        bankroll_total = st.number_input("Banca Total ($):", min_value=10.0, value=200.0, step=10.0)
        fraccion_kelly = st.slider("Fracción de Kelly:", min_value=0.1, max_value=1.0, value=0.25, step=0.05)
        monto_inversion = st.number_input("Inversión Base ($):", min_value=1.0, value=10.0, step=1.0, key="monto_inversion_base")

    consultar = st.button("🔍 Escanear Mercado Now", type="primary", use_container_width=True)

    with st.expander("🎯 Generator Auto-Parlay"):
        perfil_estrategia = st.selectbox("Estrategia:", ["📈 Mayor Probabilidad", "🛡️ Conservador", "🔥 Cazador de Valor (+EV)", "⚖️ Doble Oportunidad"])
        num_eventos_auto = st.slider("Selecciones:", min_value=2, max_value=6, value=3)
        rango_cuota_auto = st.slider("Rango de Cuota:", min_value=1.10, max_value=3.50, value=(1.25, 2.20), step=0.05)
        prob_min_auto = st.slider("Probabilidad Mínima (%):", min_value=40, max_value=90, value=55, step=5)
        generar_auto = st.button("🎲 Pre-seleccionar", use_container_width=True)

    with st.expander("🔔 Alertas Telegram"):
        st.session_state['tg_token'] = st.text_input("Bot Token:", value=st.session_state.get('tg_token', ''), type="password")
        st.session_state['tg_chat_id'] = st.text_input("Chat ID:", value=st.session_state.get('tg_chat_id', ''))
        st.session_state['auto_alertas_telegram'] = st.checkbox("🚀 Auto-alertas (+EV > 5%)", value=False)

# =========================================================
# 10. PROCESAMIENTO DE CUOTAS
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

def consultar_api_odds_con_fallback(sport_keys_list, market_key):
    for key in sport_keys_list:
        data = consultar_api_odds(key, market_key)
        if data and isinstance(data, list) and len(data) > 0:
            return data
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

def calcular_doble_oportunidad_sintetica(raw_h2h_data):
    if not raw_h2h_data or not isinstance(raw_h2h_data, list): return []
    datos_sinteticos = []
    for partido in raw_h2h_data:
        p_copy = json.loads(json.dumps(partido))
        bookies_sinteticos = []
        for b in p_copy.get('bookmakers', []):
            h2h_market = next((m for m in b.get('markets', []) if m['key'] == 'h2h'), None)
            if not h2h_market: continue
            
            c_local = next((o['price'] for o in h2h_market['outcomes'] if o['name'] == p_copy['home_team']), None)
            c_visita = next((o['price'] for o in h2h_market['outcomes'] if o['name'] == p_copy['away_team']), None)
            c_empate = next((o['price'] for o in h2h_market['outcomes'] if o['name'] == 'Draw'), None)

            if c_local and c_visita and c_empate:
                dc_1x = round(1.0 / ((1.0 / c_local) + (1.0 / c_empate)), 2)
                dc_x2 = round(1.0 / ((1.0 / c_visita) + (1.0 / c_empate)), 2)
                dc_12 = round(1.0 / ((1.0 / c_local) + (1.0 / c_visita)), 2)

                b['markets'] = [{
                    'key': 'double_chance',
                    'outcomes': [
                        {'name': 'home_draw', 'price': dc_1x},
                        {'name': 'away_draw', 'price': dc_x2},
                        {'name': 'home_away', 'price': dc_12}
                    ]
                }]
                bookies_sinteticos.append(b)
        p_copy['bookmakers'] = bookies_sinteticos
        datos_sinteticos.append(p_copy)
    return datos_sinteticos

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
        
        es_en_vivo = partido.get('in_play', False) or (horas <= 0 and horas >= -2.5)
        marcador_local = partido.get('scores', {}).get('home', 0) if es_en_vivo else None
        marcador_visita = partido.get('scores', {}).get('away', 0) if es_en_vivo else None
        
        min_raw = partido.get('minute', '30') if es_en_vivo else None
        try:
            minuto_num = int(str(min_raw).replace("'", "").replace("+", ""))
        except Exception:
            minuto_num = 30
        minuto_en_vivo = f"{minuto_num}'" if es_en_vivo else None

        if limite_horas < 900000 and not es_en_vivo and (horas < -48.0 or horas > (limite_horas + 48)): 
            continue

        fecha_local = fecha_utc - timedelta(hours=5)
        bookmakers = partido.get('bookmakers', [])
        if not bookmakers: continue

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

        if es_en_vivo:
            probs_poisson = calcular_poisson_live(minuto_num, marcador_local or 0, marcador_visita or 0)
        else:
            probs_poisson = calcular_modelo_poisson(1.45, 1.10, usar_dixon_coles=True)

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

            # Registro de histórico de cuotas para Sparkline Chart
            clave_hist = f"{partido_id}_{mercado}_{opcion}"
            if clave_hist not in st.session_state.historico_cuotas_live:
                st.session_state.historico_cuotas_live[clave_hist] = []
            st.session_state.historico_cuotas_live[clave_hist].append(cuota_max)
            if len(st.session_state.historico_cuotas_live[clave_hist]) > 10:
                st.session_state.historico_cuotas_live[clave_hist].pop(0)

        info_surebet = detectar_surebet(max_cuotas)

        if max_cuotas:
            if partido_id not in diccionario_consolidador:
                diccionario_consolidador[partido_id] = {
                    "id": partido_id, "liga_origen": nombre_liga,
                    "fecha_str": fecha_local.strftime("%d/%m/%Y - %H:%M"),
                    "fecha_ts": fecha_utc.timestamp(),
                    "local": home, "visitante": away,
                    "es_en_vivo": es_en_vivo,
                    "marcador_local": marcador_local,
                    "marcador_visita": marcador_visita,
                    "minuto_en_vivo": minuto_en_vivo,
                    "mercados": {}
                }
            diccionario_consolidador[partido_id]["mercados"][mercado] = {
                "max_cuotas": max_cuotas, "max_bookies": max_bookies,
                "betano_cuotas": betano_cuotas, "value_bets": value_bets,
                "variaciones": variaciones_dict, "todas_cuotas": cuotas_globales,
                "surebet": info_surebet
            }

# =========================================================
# 11. VISTAS Y NAVEGACIÓN
# =========================================================
col_nav_rapida, col_nav_extra = st.columns([3, 1.5])

with col_nav_rapida:
    tab_activa_rapida = st.radio(
        "Pestañas principales:",
        options=["🚀 RADAR MULTI-MERCADO", "🧮 CALCULADORA & OCR", "📊 ESTADÍSTICAS & H2H"],
        horizontal=True,
        label_visibility="collapsed"
    )

with col_nav_extra:
    opcion_extra = st.selectbox(
        "Más herramientas:",
        options=[
            "-- Seleccionar otro módulo --",
            "🛡️ MATRIZ DE COBERTURAS",
            "🔥 CAZADOR +EV & DROPPING",
            "📰 BAJAS & ALINEACIONES",
            "🎨 GENERADOR DE CARTEL",
            "📈 BITÁCORA & ROI"
        ],
        label_visibility="collapsed"
    )

if opcion_extra != "-- Seleccionar otro módulo --":
    vista_seleccionada = opcion_extra
else:
    vista_seleccionada = tab_activa_rapida

# ---------------------------------------------------------
# PESTAÑA 1: RADAR AUTOMÁTICO
# ---------------------------------------------------------
if vista_seleccionada == "🚀 RADAR MULTI-MERCADO":
    st.title("⚽ Radar Avanzado Multi-Mercado Global")
    st.caption("Escaneo de cuotas en tiempo real · Modelo Dixon-Coles / Poisson Live · SureBets · Coberturas · Dropping Odds")

    if consultar:
        if (len(ligas_sels) > 0 or len(ligas_af_sels) > 0) and len(mercados_sels) > 0:
            st.cache_data.clear()
            consolidador = {}
            st.session_state.ha_consultado = True

            mercados_featured = [m for m in mercados_sels if diccionario_mercados[m] in ("h2h", "totals")]
            total_ligas = len(ligas_sels) + len(ligas_af_sels)

            with st.status(f"🔄 Consultando {total_ligas} liga(s)...", expanded=True) as status_consulta:
                for idx_liga, liga in enumerate(ligas_sels, start=1):
                    sport_keys_list = todas_las_ligas.get(liga, [])
                    status_consulta.update(label=f"🔄 Consultando ({idx_liga}/{total_ligas}): {liga}")
                    
                    if sport_keys_list:
                        raw_h2h = consultar_api_odds_con_fallback(sport_keys_list, market_key="h2h")
                        
                        if "1X2 (Ganador)" in mercados_sels:
                            procesar_e_inyectar_mercado(raw_h2h, "1X2 (Ganador)", limite_h, liga, consolidador)

                        if "Doble Oportunidad" in mercados_sels:
                            raw_dc = consultar_api_odds_con_fallback(sport_keys_list, market_key="double_chance")
                            if not raw_dc or len(raw_dc) == 0:
                                raw_dc = calcular_doble_oportunidad_sintetica(raw_h2h)
                            procesar_e_inyectar_mercado(raw_dc, "Doble Oportunidad", limite_h, liga, consolidador)

                        if "Goles Más/Menos 2.5" in mercados_sels:
                            raw_totals = consultar_api_odds_con_fallback(sport_keys_list, market_key="totals")
                            procesar_e_inyectar_mercado(raw_totals, "Goles Más/Menos 2.5", limite_h, liga, consolidador)

                        if "Ambos Anotan (BTTS)" in mercados_sels:
                            for p_base in raw_h2h:
                                datos_evento = consultar_api_odds_evento(sport_keys_list[0], p_base['id'], "btts")
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
            <div class="empty-state-card">
                <h3 style="color:#00d2d3; margin-bottom:8px; font-weight:800;">⚡ Sistema Listo para el Análisis</h3>
                <p style="color:#8a94a6; margin-bottom:22px; font-size:13.5px;">Sigue estos pasos en el panel lateral para iniciar la búsqueda de cuotas y ValueBets:</p>
                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap:16px;">
                    <div class="empty-state-step">
                        <div class="step-badge">1</div><br>
                        <strong style="color:#ffffff; font-size:14px;">Selecciona Torneos</strong>
                        <p style="font-size:12px; color:#8a94a6; margin-top:6px; margin-bottom:0;">Añade Champions, Europa League o Ligas Domésticas.</p>
                    </div>
                    <div class="empty-state-step">
                        <div class="step-badge">2</div><br>
                        <strong style="color:#ffffff; font-size:14px;">Casas y Mercados</strong>
                        <p style="font-size:12px; color:#8a94a6; margin-top:6px; margin-bottom:0;">Selecciona la ventana de tiempo o activa 'Traer todo'.</p>
                    </div>
                    <div class="empty-state-step">
                        <div class="step-badge">3</div><br>
                        <strong style="color:#ffffff; font-size:14px;">Escanea y Ejecuta</strong>
                        <p style="font-size:12px; color:#8a94a6; margin-top:6px; margin-bottom:0;">Haz clic en 'Escanear Mercado Now' para analizar cuotas.</p>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    elif not dict_partidos:
        st.info("ℹ️ **No se encontraron partidos en el rango seleccionado.** Si estás buscando torneos europeos fuera de jornada inmediata, activa la casilla **'🌐 Traer todo sin filtro de días'** o amplia la ventana de tiempo.")
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
                                col_enc_1, col_enc_2 = st.columns([4, 1.5])
                                with col_enc_1:
                                    st.markdown(f"<span class='liga-chip'>🏆 {part['liga_origen']}</span>", unsafe_allow_html=True)
                                    if part.get('es_en_vivo'):
                                        m_loc = part.get('marcador_local', 0)
                                        m_vis = part.get('marcador_visita', 0)
                                        st.markdown(f"<div class='match-header'>⚽ {part['local']} <span style='color:#00d2d3; font-weight:800;'>{m_loc} - {m_vis}</span> {part['visitante']}</div>", unsafe_allow_html=True)
                                        st.markdown(f"<span style='background:#e74c3c; color:white; padding:2px 8px; border-radius:12px; font-weight:bold; font-size:11px;'>🔴 EN VIVO</span> <span class='kickoff-chip'>⏱️ {part.get('minuto_en_vivo', 'LIVE')}</span>", unsafe_allow_html=True)
                                    else:
                                        st.markdown(f"<div class='match-header'>⚽ {part['local']} vs {part['visitante']}</div>", unsafe_allow_html=True)
                                        st.markdown(f"<span class='kickoff-chip'>📅 {part['fecha_str']}</span>", unsafe_allow_html=True)

                                # MEJORA 1: CALCULADORA Y ALERTA DE COBERTURA LIVE (HEDGING AUTO-TRIGGER)
                                if part.get('es_en_vivo'):
                                    with st.expander("⚡ Cobertura en Vivo / Hedging Automático (Live Trigger)", expanded=False):
                                        c_h1, c_h2 = st.columns(2)
                                        monto_prev = c_h1.number_input("Inversión previa ($):", min_value=1.0, value=10.0, key=f"h_inv_{part['id']}")
                                        cuota_prev = c_h2.number_input("Cuota previa lograda:", min_value=1.01, value=2.50, key=f"h_cuota_{part['id']}")
                                        
                                        ret_esperado = monto_prev * cuota_prev
                                        st.caption(f"Retorno esperado si gana tu apuesta inicial: **${round(ret_esperado, 2)}**")
                                        
                                        m_1x2 = part['mercados'].get("1X2 (Ganador)", {})
                                        if m_1x2:
                                            cuota_contra_live = min(m_1x2.get('max_cuotas', {}).values()) if m_1x2.get('max_cuotas') else 2.0
                                            stake_hedge_live = ret_esperado / cuota_contra_live
                                            ganancia_asegurada_live = ret_esperado - monto_prev - stake_hedge_live
                                            
                                            if ganancia_asegurada_live > 0:
                                                st.success(f"🔥 **¡Oportunidad de Cobertura!** Apuesta **${round(stake_hedge_live, 2)}** a la contra-opción (x{cuota_contra_live}) para **asegurar ${round(ganancia_asegurada_live, 2)} libres de riesgo**.")
                                            else:
                                                st.info(f"💡 Apostar **${round(stake_hedge_live, 2)}** a la contraopción equilibra pérdidas si el marcador cambia.")

                                sub_tabs = st.tabs(list(part['mercados'].keys()))
                                for m_idx, text_m in enumerate(part['mercados'].keys()):
                                    with sub_tabs[m_idx]:
                                        m_info = part['mercados'][text_m]
                                        
                                        if m_info.get("surebet", {}).get("es_surebet", False):
                                            c_sb_left, c_sb_right = st.columns([4, 1])
                                            c_sb_left.success(f"💰 **SUREBET / ARBITRAJE DETECTADO!** Rendimiento asegurable: +{round(m_info['surebet']['lucro'], 2)}%")
                                            
                                            # MEJORA 5: BOTÓN DE DISPARO DIRECTO A BITÁCORA
                                            if c_sb_right.button("⚡ Disparo a Bitácora", key=f"disparo_sb_{part['id']}_{text_m}"):
                                                c_max_par = max(m_info['max_cuotas'].values())
                                                st.session_state.historial_apuestas.append({
                                                    "Fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                                                    "Detalles": f"SureBet Live (+{round(m_info['surebet']['lucro'], 2)}%) - {part['local']} vs {part['visitante']}",
                                                    "Liga": part['liga_origen'],
                                                    "Market": text_m,
                                                    "Cuota": c_max_par,
                                                    "Inversión": monto_inversion,
                                                    "Estado": "Pendiente",
                                                    "Ganancia Potencial": (c_max_par * monto_inversion) - monto_inversion
                                                })
                                                BitacoraManager.guardar(st.session_state.historial_apuestas)
                                                st.toast("⚡ ¡SureBet registrada directamente en Bitácora!", icon="🚀")

                                        # MEJORA 2: ALERTA DE MOMENTUM / DROPPING ODDS LIVE
                                        for op_k, var_k in m_info.get('variaciones', {}).items():
                                            if "Bajando" in var_k and part.get('es_en_vivo'):
                                                st.warning(f"⚡ **MOMENTUM LIVE:** Caída drástica de cuota en **{op_k}** ({m_info['max_cuotas'][op_k]}). ¡El mercado está entrando fuerte!")

                                        todas_opciones = list(m_info['max_cuotas'].keys())
                                        if text_m == "1X2 (Ganador)":
                                            orden_deseado = ["Local", "Empate", "Visitante"]
                                            todas_opciones = [o for o in orden_deseado if o in todas_opciones] + [o for o in todas_opciones if o not in orden_deseado]

                                        sub_cols = st.columns(len(todas_opciones))
                                        for idx, opcion in enumerate(todas_opciones):
                                            cuota_m = m_info['max_cuotas'][opcion]
                                            with sub_cols[idx]:
                                                val = m_info['value_bets'][opcion]
                                                var_txt = m_info.get('variaciones', {}).get(opcion, "")
                                                lbl_val = "**🔥 VALOR**" if val['es_value'] else ""
                                                clave_base = f"ap_{part['id']}_{text_m}_{opcion}"
                                                marcado = clave_base in st.session_state.claves_auto

                                                chk = st.checkbox(f"{opcion} ({cuota_m}) {lbl_val}", value=marcado, key=f"render_{clave_base}_v{st.session_state.version_ticket}")
                                                
                                                # MEJORA 3: INDICADOR DE MODELO ADAPTATIVO LIVE VS PRE-MATCH
                                                lbl_modelo = "⏱️ Poisson Live" if part.get('es_en_vivo') else "📊 Dixon-Coles"
                                                st.markdown(f"<small>🏠 {m_info['max_bookies'][opcion]}<br>🎯 Implícita: {round(val['prob_real'],1)}%<br>{lbl_modelo}: {round(val['prob_poisson'],1)}% | {var_txt}</small>", unsafe_allow_html=True)

                                                with st.expander("🏬 Comparar Casas"):
                                                    todas_casas = m_info.get('todas_cuotas', {}).get(opcion, [])
                                                    if todas_casas:
                                                        df_casas = pd.DataFrame(todas_casas, columns=["Cuota", "Casa de Apuestas"]).sort_values("Cuota", ascending=False)
                                                        st.dataframe(df_casas, use_container_width=True, hide_index=True)
                                                    
                                                    # MEJORA 4: HISTÓRICO DE CUOTAS LIVE (SPARKLINE CHART)
                                                    clave_hist = f"{part['id']}_{text_m}_{opcion}"
                                                    hist_pts = st.session_state.historico_cuotas_live.get(clave_hist, [])
                                                    if len(hist_pts) > 1:
                                                        st.caption("📈 **Tendencia de Cuota Live:**")
                                                        st.line_chart(hist_pts, height=100)

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

                    monto_ticket = st.number_input("💵 Inversión / Importe a Apostar ($):", min_value=1.0, value=float(monto_inversion), step=1.0, key="monto_ticket_directo")

                    b = cuota_acumulada - 1.0
                    p = prob_combinada
                    q = 1.0 - p
                    f_kelly = ((b * p) - q) / b if b > 0 else 0
                    stake_kelly = max(0.0, f_kelly * fraccion_kelly * bankroll_total)

                    ganancia_neta = (cuota_acumulada * monto_ticket) - monto_ticket
                    sharpe_parlay = calcular_sharpe_parlay(cuota_acumulada, prob_combinada)

                    st.metric("Cuota Final", f"x{round(cuota_acumulada, 2)}")
                    st.metric("Ganancia Neta Base", f"${round(ganancia_neta, 2)}")
                    
                    c_k1, c_k2 = st.columns(2)
                    c_k1.metric("💡 Stake Kelly Sugerido", f"${round(stake_kelly, 2)}", help=f"Recomendación para tu Bankroll de ${bankroll_total}")
                    c_k2.metric("⚡ Sharpe Ratio Parlay", f"{round(sharpe_parlay, 3)}", help="Relación de Rentabilidad Esperanza vs Volatilidad (> 0.05 es aceptable)")

                    with st.expander("🛡️ Optimizador de Sistemas (TRIXIE / YANKEE)"):
                        res_sistema = calcular_sistema_cobertura(apuestas_seleccionadas, monto_ticket)
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
                        retorno_potencial = monto_ticket * cuota_acumulada
                        stake_hedge = retorno_potencial / cuota_contra
                        ganancia_asegurada = retorno_potencial - monto_ticket - stake_hedge
                        st.info(f"👉 Apostar **${round(stake_hedge, 2)}** a la contraopción para **garantizar ${round(ganancia_asegurada, 2)} libres de riesgo**.")

                    if st.button("💾 Registrar en Bitácora", type="primary", use_container_width=True):
                        st.session_state.historial_apuestas.append({
                            "Fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                            "Detalles": f"{len(apuestas_seleccionadas)} combinadas",
                            "Liga": apuestas_seleccionadas[0]['liga'] if apuestas_seleccionadas else "Varias",
                            "Market": apuestas_seleccionadas[0]['mercado'] if len(apuestas_seleccionadas)==1 else "Multi-Mercado",
                            "Cuota": cuota_acumulada,
                            "Inversión": monto_ticket,
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
                    html_ticket += f"<hr><h4>Cuota Total: x{round(cuota_acumulada,2)} | Inversión: ${monto_ticket}</h4></div>"
                    
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
# PESTAÑA 2: CALCULADORA EXTERNA & MONTE CARLO
# ---------------------------------------------------------
elif vista_seleccionada == "🧮 CALCULADORA & OCR":
    st.title("🧮 Analizador, Lector OCR & Simulador Monte Carlo")
    st.caption("Analiza cuotas de boletos externos, simula 10,000 iteraciones o lee datos desde capturas de pantalla.")

    modo_ingreso = st.segmented_control(
        "⚡ Método de Ingreso:",
        options=["🚀 Pegado Rápido (Texto)", "📸 Captura de Pantalla / OCR", "📝 Registro Manual"],
        default="🚀 Pegado Rápido (Texto)"
    )

    col_ingreso, col_resultados = st.columns([1.1, 1], gap="medium")
    partidos_externos = []

    with col_ingreso:
        with st.container(border=True):
            st.subheader("📌 Ingresar Selecciones")
            margen_estimado_casa = st.slider(
                "Comisión/Margen estimado de la casa (%):", 
                min_value=1.0, max_value=15.0, value=5.0, step=0.5, 
                help="La mayoría de casas cobran entre 4% y 7% de margen sobre las cuotas."
            )

            if modo_ingreso == "📸 Captura de Pantalla / OCR":
                st.markdown("""
                    <div class="hint-box">
                        💡 <b>Lector OCR Inteligente:</b> Sube la captura de tu boleto. El motor procesará la imagen y extraerá los eventos y cuotas.
                    </div>
                """, unsafe_allow_html=True)
                imagen_subida = st.file_uploader("🖼️ Selecciona la imagen del boleto:", type=["png", "jpg", "jpeg", "webp"])
                
                if imagen_subida is not None:
                    try:
                        image = Image.open(imagen_subida)
                        texto_ocr = ""
                        
                        try:
                            import pytesseract
                            texto_ocr = pytesseract.image_to_string(image)
                        except Exception:
                            try:
                                import easyocr
                                reader = easyocr.Reader(['es', 'en'], gpu=False)
                                image_np = np.array(image)
                                result_ocr = reader.readtext(image_np, detail=0)
                                texto_ocr = "\n".join(result_ocr)
                            except Exception:
                                texto_ocr = ""

                        if texto_ocr.strip():
                            lineas = texto_ocr.strip().split('\n')
                            for linea in lineas:
                                linea_clean = linea.strip()
                                if not linea_clean:
                                    continue
                                
                                linea_norm = re.sub(r'([^\w\d]|^)[xX@]\s*(?=\d)', r'\1', linea_clean)
                                linea_norm = linea_norm.replace(',', '.')
                                
                                cuotas_encontradas = re.findall(r'\b\d+\.\d+\b', linea_norm)
                                cuotas_validas = [float(c) for c in cuotas_encontradas if float(c) > 1.01 and float(c) < 100.0]
                                
                                if cuotas_validas:
                                    cuota_val = cuotas_validas[-1]
                                    nombre_txt = re.sub(r'[\|\-\>\:\@]', ' ', linea_clean)
                                    nombre_txt = re.sub(r'\b[xX@]?\s*\d+[\.,]?\d*\b', '', nombre_txt)
                                    nombre_txt = re.sub(r'\s+', ' ', nombre_txt).strip()
                                    
                                    if not nombre_txt or len(nombre_txt) < 2:
                                        nombre_txt = f"Selección #{len(partidos_externos)+1}"
                                    
                                    partidos_externos.append({"nombre": nombre_txt, "cuota": cuota_val})

                            if partidos_externos:
                                st.success(f"✅ Se detectaron {len(partidos_externos)} selecciones en la captura de pantalla.")
                            else:
                                st.warning("⚠️ No se pudieron asociar cuotas válidas en el texto extraído.")
                        else:
                            st.warning("⚠️ No se pudo procesar la imagen mediante el OCR nativo. Usa la pestaña '🚀 Pegado Rápido (Texto)' para ingresar los datos.")
                    except Exception as e:
                        st.error(f"Error procesando la imagen: {e}")

            elif modo_ingreso == "🚀 Pegado Rápido (Texto)":
                st.markdown("""
                    <div class="hint-box">
                        💡 <b>Ejemplo de pegado directo:</b> Copia el texto o cuotas de tu boleto.<br>
                        <code>Independiente del Valle 1.62</code> | <code>FK Bodo/Glimt 1.55</code>
                    </div>
                """, unsafe_allow_html=True)
                
                texto_pegado = st.text_area(
                    "📋 Pega aquí tu boleto o cuotas:",
                    height=130,
                    value="Independiente del Valle 1.62\nFK Bodo/Glimt 1.55",
                    placeholder="Ejemplo:\nIndependiente del Valle 1.62\nFK Bodo/Glimt 1.55"
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
                num_partidos_ext = st.number_input("Número de Partidos en tu Ticket:", min_value=1, max_value=10, value=2, step=1)
                for i in range(int(num_partidos_ext)):
                    with st.expander(f"⚽ Selección #{i+1}", expanded=True):
                        col1, col2 = st.columns([2, 1])
                        with col1:
                            nombre_partido = st.text_input(f"Partido/Selección #{i+1}:", f"Evento #{i+1}", key=f"ext_name_{i}")
                        with col2:
                            cuota_partido = st.number_input(f"Cuota:", min_value=1.01, value=1.50, step=0.05, key=f"ext_odd_{i}")
                        partidos_externos.append({"nombre": nombre_partido, "cuota": cuota_partido})

    with col_resultados:
        with st.container(border=True):
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
                        <div class="kpi-value" style="color: #00d2d3;">{round(prob_porcentaje, 2)}%</div>
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
# PESTAÑA 3: ANÁLISIS ESTADÍSTICO & H2H
# ---------------------------------------------------------
elif vista_seleccionada == "📊 ESTADÍSTICAS & H2H":
    st.title("📊 Análisis Estadístico, Simulador xG y H2H")
    st.caption("Historial reciente de enfrentamientos directos, simulación de goles esperados (xG) e impacto de bajas.")

    @st.cache_data(ttl=3600)
    def obtener_id_equipo_af(team_name):
        if not AF_API_KEY or not team_name:
            return None
        try:
            r = requests.get(f"{AF_BASE_URL}/teams", headers=af_headers(), params={"search": team_name}, timeout=10)
            _actualizar_creditos_af(r.headers)
            data = r.json().get("response", [])
            return data[0]["team"]["id"] if data else None
        except Exception:
            return None

    @st.cache_data(ttl=3600)
    def consultar_h2h_af(team1_id, team2_id):
        if not AF_API_KEY or not team1_id or not team2_id:
            return []
        try:
            r = requests.get(f"{AF_BASE_URL}/fixtures/headtohead", headers=af_headers(), params={"h2h": f"{team1_id}-{team2_id}", "last": 10}, timeout=10)
            _actualizar_creditos_af(r.headers)
            return r.json().get("response", [])
        except Exception:
            return []

    @st.cache_data(ttl=3600)
    def consultar_ultimos_partidos_af(team_id):
        if not AF_API_KEY or not team_id:
            return []
        try:
            r = requests.get(f"{AF_BASE_URL}/fixtures", headers=af_headers(), params={"team": team_id, "last": 5}, timeout=10)
            _actualizar_creditos_af(r.headers)
            return r.json().get("response", [])
        except Exception:
            return []

    c_h1, c_h2 = st.columns(2)
    with c_h1:
        eq_local = st.text_input("⚽ Equipo Local:", value="Independiente del Valle")
    with c_h2:
        eq_visit = st.text_input("⚽ Equipo Visitante:", value="Deportes Tolima")

    if eq_local and eq_visit:
        st.subheader(f"⚔️ Comparativa Directa: {eq_local} vs {eq_visit}")

        with st.spinner("Consultando estadísticas en API-Football..."):
            id_local = obtener_id_equipo_af(eq_local)
            id_visit = obtener_id_equipo_af(eq_visit)

        if id_local and id_visit:
            ult_local = consultar_ultimos_partidos_af(id_local)
            ult_visit = consultar_ultimos_partidos_af(id_visit)

            def procesar_forma_y_goles(partidos, team_id):
                forma, goles = [], []
                for p in partidos:
                    goals = p.get("goals", {})
                    teams = p.get("teams", {})
                    es_home = teams.get("home", {}).get("id") == team_id
                    g_favor = goals.get("home") if es_home else goals.get("away")
                    g_contra = goals.get("away") if es_home else goals.get("home")
                    
                    if g_favor is not None and g_contra is not None:
                        goles.append(g_favor)
                        if g_favor > g_contra: forma.append("W")
                        elif g_favor == g_contra: forma.append("D")
                        else: forma.append("L")
                
                cadena_forma = "-".join(forma) if forma else "N/A"
                prom_goles = round(sum(goles) / len(goles), 2) if goles else 0.0
                rend = round((forma.count("W") * 3 + forma.count("D")) / (len(forma) * 3) * 100) if forma else 0
                return cadena_forma, f"{rend}% Rend.", prom_goles

            f_loc, r_loc, g_loc = procesar_forma_y_goles(ult_local, id_local)
            f_vis, r_vis, g_vis = procesar_forma_y_goles(ult_visit, id_visit)

            col_m1, col_m2, col_m3, col_m4 = st.columns(4)
            col_m1.metric("Forma Local (Últimos 5)", f_loc, r_loc)
            col_m2.metric("Forma Visitante (Últimos 5)", f_vis, r_vis)
            col_m3.metric("Promedio Goles Local", f"{g_loc} / partido")
            col_m4.metric("Promedio Goles Visitante", f"{g_vis} / partido")

            st.markdown("<br>", unsafe_allow_html=True)
            
            with st.expander("🎯 Simulador de Goles Esperados (xG) & Calculador de Impacto por Bajas", expanded=True):
                col_xg1, col_xg2 = st.columns(2)
                with col_xg1:
                    xg_local_input = st.number_input(f"xG Promedio {eq_local}:", min_value=0.1, max_value=5.0, value=max(0.5, g_loc if g_loc > 0 else 1.45), step=0.05)
                    bajas_local_pct = st.slider(f"Penalización por bajas {eq_local} (%):", 0.0, 50.0, 0.0, step=5.0, help="Descuento ofensivo por falta de titulares clave.")
                with col_xg2:
                    xg_visit_input = st.number_input(f"xG Promedio {eq_visit}:", min_value=0.1, max_value=5.0, value=max(0.5, g_vis if g_vis > 0 else 1.10), step=0.05)
                    bajas_visit_pct = st.slider(f"Penalización por bajas {eq_visit} (%):", 0.0, 50.0, 0.0, step=5.0, help="Descuento ofensivo por falta de titulares clave.")
                
                lambda_l_adj = calcular_impacto_bajas(xg_local_input, bajas_local_pct)
                lambda_v_adj = calcular_impacto_bajas(xg_visit_input, bajas_visit_pct)

                probs_custom_xg = calcular_modelo_poisson(lambda_l_adj, lambda_v_adj, usar_dixon_coles=True)
                
                st.markdown(f"**Lambdas Ajustados:** `{eq_local}`: **{round(lambda_l_adj, 2)}** goles esperados | `{eq_visit}`: **{round(lambda_v_adj, 2)}** goles esperados")
                
                cx1, cx2, cx3, cx4 = st.columns(4)
                cx1.metric(f"Victoria {eq_local}", f"{round(probs_custom_xg['Local'], 1)}%")
                cx2.metric("Empate", f"{round(probs_custom_xg['Empate'], 1)}%")
                cx3.metric(f"Victoria {eq_visit}", f"{round(probs_custom_xg['Visitante'], 1)}%")
                cx4.metric("Más de 2.5 Goles", f"{round(probs_custom_xg['Más de 2.5'], 1)}%")

            st.markdown("<br>", unsafe_allow_html=True)
            st.subheader("📜 Histórico de Enfrentamientos Directos (H2H)")

            partidos_h2h = consultar_h2h_af(id_local, id_visit)

            if partidos_h2h:
                filas_h2h = []
                for item in partidos_h2h:
                    fixture = item.get("fixture", {})
                    league = item.get("league", {})
                    teams = item.get("teams", {})
                    goals = item.get("goals", {})

                    fecha = fixture.get("date", "")[:10]
                    torneo = league.get("name", "N/A")
                    res = f"{teams.get('home', {}).get('name')} {goals.get('home')} - {goals.get('away')} {teams.get('away', {}).get('name')}"
                    
                    if goals.get('home') > goals.get('away'):
                        ganador = teams.get('home', {}).get('name')
                    elif goals.get('away') > goals.get('home'):
                        ganador = teams.get('away', {}).get('name')
                    else:
                        ganador = "Empate"

                    filas_h2h.append({"Fecha": fecha, "Torneo": torneo, "Resultado": res, "Ganador": ganador})

                st.dataframe(pd.DataFrame(filas_h2h), use_container_width=True, hide_index=True)
            else:
                st.info(f"ℹ️ No se registraron partidos previos entre **{eq_local}** y **{eq_visit}** en la base de datos de API-Football.")
        else:
            st.warning("⚠️ No se encontraron los IDs oficiales de los equipos ingresados. Asegúrate de escribir el nombre correctamente.")

# ---------------------------------------------------------
# PESTAÑA 4: MATRIZ DE COBERTURAS Y CASHOUT
# ---------------------------------------------------------
elif vista_seleccionada == "🛡️ MATRIZ DE COBERTURAS":
    st.title("🛡️ Matriz de Coberturas & Calculadora SureBet")
    st.caption("Asegura ganancias en partidos en vivo o calcula arbitrajes libres de riesgo.")

    sub_tab1, sub_tab2 = st.tabs(["💰 Calculadora Arbitraje / SureBet", "🔄 Cashout vs. Cobertura (Hedge)"])

    with sub_tab1:
        st.subheader("🧮 Arbitraje Libre de Riesgo (2 u Opción 3-Way)")
        c_sb1, c_sb2, c_sb3 = st.columns(3)
        cuota_1 = c_sb1.number_input("Cuota Opción 1 (ej: Local):", min_value=1.01, value=2.10, step=0.05)
        cuota_X = c_sb2.number_input("Cuota Opción X (ej: Empate):", min_value=1.01, value=3.40, step=0.05)
        cuota_2 = c_sb3.number_input("Cuota Opción 2 (ej: Visitante):", min_value=1.01, value=4.50, step=0.05)

        monto_total_sb = st.number_input("Monto Total a Invertir ($):", min_value=10.0, value=100.0, step=10.0)

        inv_p = (1/cuota_1) + (1/cuota_X) + (1/cuota_2)
        lucro_sb = ((1 / inv_p) - 1) * 100

        if inv_p < 1.0:
            st.success(f"🔥 **SUREBET DETECTADA! Rendimiento Garantizado: +{round(lucro_sb, 2)}%**")
            ap_1 = (monto_total_sb / (cuota_1 * inv_p))
            ap_X = (monto_total_sb / (cuota_X * inv_p))
            ap_2 = (monto_total_sb / (cuota_2 * inv_p))

            res_df = pd.DataFrame([
                {"Opción": "Selección 1", "Cuota": cuota_1, "Apostar ($)": round(ap_1, 2), "Retorno Total ($)": round(ap_1 * cuota_1, 2)},
                {"Opción": "Selección X", "Cuota": cuota_X, "Apostar ($)": round(ap_X, 2), "Retorno Total ($)": round(ap_X * cuota_X, 2)},
                {"Opción": "Selección 2", "Cuota": cuota_2, "Apostar ($)": round(ap_2, 2), "Retorno Total ($)": round(ap_2 * cuota_2, 2)},
            ])
            st.dataframe(res_df, use_container_width=True, hide_index=True)
        else:
            st.warning(f"⚠️ No hay SureBet con estas cuotas (Overround/Margen de la casa: {round(inv_p*100, 2)}%).")

    with sub_tab2:
        st.subheader("🔄 Evaluar Cashout vs. Cobertura Directa")
        c_ch1, c_ch2 = st.columns(2)
        monto_original = c_ch1.number_input("Inversión Inicial Parlay ($):", value=20.0)
        retorno_potencial = c_ch2.number_input("Retorno Potencial Parlay ($):", value=250.0)

        cashout_ofrecido = c_ch1.number_input("Cashout ofrecido por la Casa ($):", value=140.0)
        cuota_contraopcion = c_ch2.number_input("Cuota Contra-Opción en vivo:", value=2.20)

        hedge_stake = retorno_potencial / cuota_contraopcion
        ganancia_hedge = retorno_potencial - hedge_stake - monto_original

        col_r1, col_r2 = st.columns(2)
        col_r1.info(f"💵 **Aceptar Cashout Casa:** Ganas **${round(cashout_ofrecido - monto_original, 2)}** netos")
        col_r2.success(f"🛡️ **Apostar ${round(hedge_stake, 2)} en Cobertura:** Ganas **${round(ganancia_hedge, 2)}** netos en cualquier resultado")

# ---------------------------------------------------------
# PESTAÑA 5: CAZADOR AUTOMÁTICO DE VALUEBETS (+EV) & FILTRO VALUE STREAM
# ---------------------------------------------------------
elif vista_seleccionada == "🔥 CAZADOR +EV & DROPPING":
    st.title("🔥 Cazador de Valor (+EV), Value Stream y Dropping Odds")
    st.caption("Detección de movimientos bruscos del mercado y filtrado dinámico de brechas de valor positivo (+EV).")

    st.subheader("🎯 Value Stream: Filtrado Dinámico de Oportunidades +EV")

    umbral_ev_min = st.slider("Filtrar por Umbral Mínimo de Valor (+EV %):", min_value=1.0, max_value=25.0, value=5.0, step=0.5)

    dict_partidos = st.session_state.get('datos_cargados', {})
    
    lista_valuebets_reales = []
    
    if dict_partidos:
        for p_id, part in dict_partidos.items():
            for nombre_m, m_info in part['mercados'].items():
                for opcion, val_data in m_info['value_bets'].items():
                    ev_porcentaje = round(val_data['ev'] * 100, 1)
                    if val_data.get('es_value', False) and ev_porcentaje >= umbral_ev_min:
                        cuota_bookie = m_info['max_cuotas'][opcion]
                        prob_r = val_data['prob_real'] / 100.0
                        cuota_justa = round(1.0 / prob_r, 2) if prob_r > 0 else 0
                        
                        lista_valuebets_reales.append({
                            "Partido": f"{part['local']} vs {part['visitante']}",
                            "Mercado": nombre_m,
                            "Selección": opcion,
                            "Cuota Bookie": cuota_bookie,
                            "Cuota Justa": cuota_justa,
                            "Valor (+EV)": f"+{ev_porcentaje}%",
                            "Casa Top": m_info['max_bookies'][opcion]
                        })

    if lista_valuebets_reales:
        df_value_real = pd.DataFrame(lista_valuebets_reales).sort_values("Valor (+EV)", ascending=False)
        st.dataframe(df_value_real, use_container_width=True, hide_index=True)
    else:
        st.info(f"ℹ️ No se detectaron apuestas con valor superior a **+{umbral_ev_min}%**. Realiza un nuevo escaneo o ajusta el slider del umbral.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("📉 Alertas de Dropping Odds (Cuotas en Caída Libre)")
    st.caption("Señala eventos donde el dinero del mercado masivo está empujando las líneas a la baja.")
    
    lista_dropping = []
    if dict_partidos:
        for p_id, part in dict_partidos.items():
            for nombre_m, m_info in part['mercados'].items():
                for opcion, var_estado in m_info.get('variaciones', {}).items():
                    if "Bajando" in var_estado:
                        lista_dropping.append({
                            "Partido": f"{part['local']} vs {part['visitante']}",
                            "Mercado": nombre_m,
                            "Selección": opcion,
                            "Cuota Actual": m_info['max_cuotas'][opcion],
                            "Tendencia": var_estado,
                            "Casa Top": m_info['max_bookies'][opcion]
                        })

    if lista_dropping:
        st.dataframe(pd.DataFrame(lista_dropping), use_container_width=True, hide_index=True)
    else:
        st.caption("No se han detectado caídas drásticas de cuotas en las consultas consecutivas recientes.")

# ---------------------------------------------------------
# PESTAÑA 6: NOTICIAS & BAJAS IMPORTANTES
# ---------------------------------------------------------
elif vista_seleccionada == "📰 BAJAS & ALINEACIONES":
    st.title("📰 Centro de Bajas, Lesiones y Alineaciones")
    st.caption("Información contextual en tiempo real mediante API-Football para validar tus selecciones.")

    dict_partidos = st.session_state.get('datos_cargados', {})

    col_info_1, col_info_2 = st.columns(2)

    with col_info_1:
        st.subheader("🩹 Lesionados & Sancionados Reales")
        
        @st.cache_data(ttl=3600)
        def consultar_lesiones_af(team_name):
            if not AF_API_KEY or not team_name:
                return []
            try:
                r_team = requests.get(f"{AF_BASE_URL}/teams", headers=af_headers(), params={"search": team_name}, timeout=10)
                _actualizar_creditos_af(r_team.headers)
                data_team = r_team.json().get("response", [])
                if not data_team:
                    return []
                team_id = data_team[0]["team"]["id"]

                r_injuries = requests.get(f"{AF_BASE_URL}/injuries", headers=af_headers(), params={"team": team_id}, timeout=10)
                _actualizar_creditos_af(r_injuries.headers)
                return r_injuries.json().get("response", [])
            except Exception:
                return []

        if dict_partidos:
            equipos_escaneados = set()
            for part in dict_partidos.values():
                equipos_escaneados.add(part['local'])
                equipos_escaneados.add(part['visitante'])

            equipo_sel = st.selectbox("Selecciona un equipo de la lista escaneada:", list(equipos_escaneados))
            
            if equipo_sel:
                with st.spinner(f"Consultando bajas de {equipo_sel}..."):
                    lesiones = consultar_lesiones_af(equipo_sel)
                    
                if lesiones:
                    for item in lesiones[:10]:
                        player = item.get("player", {})
                        reason = player.get("reason", "No especificado")
                        type_injury = player.get("type", "Baja")
                        st.markdown(f"🔴 **{player.get('name', 'Jugador')}:** {type_injury} ({reason})")
                else:
                    st.success(f"✅ No se reportan bajas oficiales registradas recientemente para **{equipo_sel}**.")
        else:
            st.info("ℹ️ Haz clic en **'🔍 Escanear Mercado Now'** para cargar los equipos disponibles y consultar sus bajas.")

    with col_info_2:
        st.subheader("📋 Estado del Encuentro y Alineaciones")
        
        if dict_partidos:
            partido_lista = [f"{p['local']} vs {p['visitante']}" for p in dict_partidos.values()]
            partido_sel = st.selectbox("Selecciona un partido para revisar alineación:", partido_lista)
            
            st.info("🌦️ **Condiciones Ambientales:** Consulta previa a partido disponible en actualización de cuotas.")
            st.warning("⚠️ **Alineaciones Confirmadas:** Las alineaciones oficiales se publican automáticamente 1 hora antes de que inicie el partido.")
        else:
            st.caption("Escanea partidos desde el panel lateral para habilitar este módulo.")

# ---------------------------------------------------------
# PESTAÑA 7: GENERADOR DE CARTEL
# ---------------------------------------------------------
elif vista_seleccionada == "🎨 GENERADOR DE CARTEL":
    st.title("🎨 Generador Visual de Pronósticos para Redes")
    st.caption("Crea carteles elegantes y profesionales con tus jugadas para compartir en Telegram, WhatsApp o Redes.")

    st.subheader("🖼️ Diseñador de Tarjeta de Apuesta")
    c_t1, c_t2 = st.columns([1, 1])

    with c_t1:
        titulo_cartel = st.text_input("Título del Cartel:", "🔥 PARLAY DEL DÍA DE ALTA PROBABILIDAD")
        analista_nombre = st.text_input("Nombre de Tipster / Canal:", "@MiCanalApuestasPro")
        monto_sugerido = st.number_input("Stake Sugerido (1-10):", min_value=1, max_value=10, value=3)

    with c_t2:
        st.markdown(f"""
            <div style="background: linear-gradient(135deg, #12161f 0%, #0a0d13 100%); border: 2px solid #00d2d3; padding: 20px; border-radius: 15px; text-align: center;">
                <h3 style="color: #00d2d3; margin-bottom: 5px;">{titulo_cartel}</h3>
                <p style="color: #8a94a6; font-size: 12px;">Analista: <b>{analista_nombre}</b> | Stake: <b>{monto_sugerido}/10</b></p>
                <hr style="border-color: #232a38;">
                <div style="text-align: left; font-size: 14px; margin: 10px 0;">
                    ⚽ <b>Independiente del Valle vs Dep. Tolima</b><br>
                    🎯 Selección: <span style="color:#00d2d3; font-weight:bold;">Local (1.59)</span><br><br>
                    ⚽ <b>Bodo/Glimt vs Linfield</b><br>
                    🎯 Selección: <span style="color:#00d2d3; font-weight:bold;">Más de 2.5 Goles (1.55)</span>
                </div>
                <hr style="border-color: #232a38;">
                <h2 style="color: #ffffff; font-family: 'JetBrains Mono'; margin: 5px 0;">CUOTA TOTAL: x2.46</h2>
            </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.button("📸 Descargar Imagen del Cartel (PNG)", use_container_width=True)

# ---------------------------------------------------------
# PESTAÑA 8: AUDITORÍA Y BITÁCORA PRO
# ---------------------------------------------------------
elif vista_seleccionada == "📈 BITÁCORA & ROI":
    st.title("📊 Módulo de Auditoría Financiera Avanzada Pro")

    col_exp_j, col_imp_j, col_exp_csv = st.columns(3)
    with col_exp_j:
        json_data = json.dumps(st.session_state.historial_apuestas, ensure_ascii=False, indent=4)
        st.download_button("📥 Respaldo JSON", data=json_data, file_name="bitacora_backup.json", mime="application/json", use_container_width=True)
    with col_imp_j:
        uploaded_json = st.file_uploader("📤 Restaurar JSON", type=["json"], label_visibility="collapsed")
        if uploaded_json is not None:
            try:
                data_restaurada = json.load(uploaded_json)
                st.session_state.historial_apuestas = data_restaurada
                BitacoraManager.guardar(data_restaurada)
                st.success("✅ Bitácora restaurada!")
                st.rerun()
            except Exception as e:
                st.error(f"Error JSON: {e}")
    with col_exp_csv:
        if st.session_state.historial_apuestas:
            csv_bytes = generar_csv_bitacora(st.session_state.historial_apuestas)
            st.download_button("📊 Exportar CSV Bitácora", data=csv_bytes, file_name="Reporte_Apuestas_Pro.csv", mime="text/csv", use_container_width=True)
        else:
            st.button("📊 Exportar CSV (Sin datos)", disabled=True, use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)

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
        
        with st.expander("📊 Stress Test de Banca (Probabilidad de Ruina a 200 Apuestas)"):
            if len(terminados) > 0:
                prob_acierto_hist = (len(ganados) / len(terminados))
                cuota_prom_hist = terminados['Cuota'].mean() if len(terminados) > 0 else 1.50
                inv_prom_hist = terminados['Inversión'].mean() if len(terminados) > 0 else 10.0
                
                res_ruina = simular_riesgo_ruina_banca(bankroll_total, inv_prom_hist, prob_acierto_hist, cuota_prom_hist)
                
                c_r1, c_r2 = st.columns(2)
                c_r1.metric("📉 Riesgo Estimado de Ruina", f"{round(res_ruina['prob_ruina'], 2)}%", help="Porcentaje de simulaciones donde la banca cayó a $0")
                c_r2.metric("💵 Banca Promedio Proyectada", f"${round(res_ruina['banca_promedio_final'], 2)}", help="Banca esperada tras 200 apuestas manteniendo tu rendimiento actual")
            else:
                st.caption("Registra al menos 1 apuesta finalizada (Ganada/Perdida) para ejecutar la simulación de ruina de banca.")

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
        st.markdown("""
            <div class="empty-state-card" style="text-align: center; padding: 40px 20px;">
                <div style="font-size: 42px; margin-bottom: 10px;">📋</div>
                <h3 style="color:#00d2d3; margin-bottom: 8px;">Bitácora de Apuestas Vacía</h3>
                <p style="color:#8a94a6; max-width: 500px; margin: 0 auto 20px auto; font-size: 14px;">
                    Aún no registras apuestas en tu historial. Puedes guardar un parlay directamente desde la pestaña <b>Radar</b> o restaurar un respaldo JSON existente arriba.
                </p>
            </div>
        """, unsafe_allow_html=True)
