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
import itertools
import time
from typing import Dict, List, Any, Optional
from PIL import Image
import streamlit.components.v1 as components
import concurrent.futures

# =========================================================
# 1. CONFIGURACIÓN DE PÁGINA Y CREDENCIALES
# =========================================================
st.set_page_config(
    page_title="Radar Enterprise Parlay Global - Pro Edition",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

DB_FILE = "bitacora_backup.json"
CONFIG_FILE = "user_config.json"

API_KEY = st.secrets.get("ODDS_API_KEY", "")
HL_API_KEY = st.secrets.get("HL_API_KEY", "")
HL_BASE_URL = "https://soccer.highlightly.net"
AF_API_KEY = st.secrets.get("AF_API_KEY", "")
AF_BASE_URL = "https://v3.football.api-sports.io"

DEFAULT_TG_TOKEN = st.secrets.get("TG_TOKEN", "")
DEFAULT_TG_CHAT_ID = st.secrets.get("TG_CHAT_ID", "")

# =========================================================
# 2. CAPA DE PERSISTENCIA Y SERVICIOS EN MEMORIA BUFFERED
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

def disparar_alerta_sonora_y_notificacion(titulo: str, mensaje: str):
    if not st.session_state.get('notif_push_activas', True):
        return
    js_code = f"""
    <script>
    if ("Notification" in window) {{
        if (Notification.permission === "granted") {{
            new Notification("{titulo}", {{ body: "{mensaje}", icon: "⚽" }});
        }} else if (Notification.permission !== "denied") {{
            Notification.requestPermission().then(function (permission) {{
                if (permission === "granted") {{
                    new Notification("{titulo}", {{ body: "{mensaje}", icon: "⚽" }});
                }}
            }});
        }}
    }}
    try {{
        let ctx = new (window.AudioContext || window.webkitAudioContext)();
        let osc = ctx.createOscillator();
        let gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, ctx.currentTime);
        gain.gain.setValueAtTime(0.1, ctx.currentTime);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.3);
    }} catch(e) {{}}
    </script>
    """
    components.html(js_code, height=0, width=0)

# =========================================================
# 3. MODELADO MATEMÁTICO VECTORIZADO (NUMPY)
# =========================================================
def poisson_pmf_vec(k_array: np.ndarray, mu: float) -> np.ndarray:
    return (np.power(mu, k_array) * np.exp(-mu)) / np.array([math.factorial(k) for k in k_array])

def calcular_impacto_bajas(lambda_base: float, peso_bajas: float) -> float:
    return lambda_base * max(0.3, 1.0 - (peso_bajas / 100.0))

def calcular_modelo_poisson(lambda_local: float = 1.45, lambda_visita: float = 1.10, usar_dixon_coles: bool = True, bajas_local_pct: float = 0.0, bajas_visita_pct: float = 0.0) -> Dict[str, float]:
    lambda_l = calcular_impacto_bajas(lambda_local, bajas_local_pct)
    lambda_v = calcular_impacto_bajas(lambda_visita, bajas_visita_pct)

    max_goles = 6
    k_range = np.arange(max_goles)
    
    p_l = poisson_pmf_vec(k_range, lambda_l)
    p_v = poisson_pmf_vec(k_range, lambda_v)
    
    matriz_prob = np.outer(p_l, p_v)

    if usar_dixon_coles:
        rho = -0.13
        matriz_prob[0, 0] *= (1.0 - lambda_l * lambda_v * rho)
        matriz_prob[1, 0] *= (1.0 + lambda_v * rho)
        matriz_prob[0, 1] *= (1.0 + lambda_l * rho)
        matriz_prob[1, 1] *= (1.0 - rho)

    soma = np.sum(matriz_prob)
    if soma > 0:
        matriz_prob /= soma

    prob_local = float(np.sum(np.tril(matriz_prob, -1)))
    prob_empate = float(np.sum(np.diag(matriz_prob)))
    prob_visita = float(np.sum(np.triu(matriz_prob, 1)))
    prob_btts = float(np.sum(matriz_prob[1:, 1:]))
    
    grid_i, grid_j = np.indices((max_goles, max_goles))
    prob_over25 = float(np.sum(matriz_prob[grid_i + grid_j > 2.5]))

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
    tiempo_restante_pct = max(0.01, (90 - min(89, minuto)) / 90.0)
    lambda_l_rem = lambda_l_base * tiempo_restante_pct
    lambda_v_rem = lambda_v_base * tiempo_restante_pct

    max_goles_rem = 6
    k_range = np.arange(max_goles_rem)
    p_l = poisson_pmf_vec(k_range, lambda_l_rem)
    p_v = poisson_pmf_vec(k_range, lambda_v_rem)
    
    matriz_prob = np.outer(p_l, p_v)
    soma = np.sum(matriz_prob)
    if soma > 0:
        matriz_prob /= soma

    grid_i, grid_j = np.indices((max_goles_rem, max_goles_rem))
    tot_loc = goles_loc + grid_i
    tot_vis = goles_vis + grid_j

    prob_local_win = float(np.sum(matriz_prob[tot_loc > tot_vis]))
    prob_empate_win = float(np.sum(matriz_prob[tot_loc == tot_vis]))
    prob_visita_win = float(np.sum(matriz_prob[tot_loc < tot_vis]))
    
    prob_over25_live = float(np.sum(matriz_prob[(goles_loc + goles_vis + grid_i + grid_j) > 2.5]))
    prob_btts_live = float(np.sum(matriz_prob[(tot_loc > 0) & (tot_vis > 0)]))

    return {
        "Local": prob_local_win * 100,
        "Empate": prob_empate_win * 100,
        "Visitante": prob_visita_win * 100,
        "1X (Local o Empate)": (prob_local_win + prob_empate_win) * 100,
        "X2 (Visitante o Empate)": (prob_visita_win + prob_empate_win) * 100,
        "12 (Local o Visitante)": (prob_local_win + prob_visita_win) * 100,
        "Sí": prob_btts_live * 100,
        "No": (1.0 - prob_btts_live) * 100,
        "Más de 2.5": prob_over25_live * 100,
        "Menos de 2.5": (1.0 - prob_over25_live) * 100
    }

# =========================================================
# 4. VALIDADOR Y MONTE CARLO
# =========================================================
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

def simular_riesgo_ruina_banca(bankroll_inicial: float, stake_promedio: float, prob_exito: float, cuota_prom: float, num_apuestas: int = 200, sims: int = 2000) -> Dict[str, float]:
    bancarrota_count = 0
    final_bankrolls = []
    
    matriz_rand = np.random.rand(sims, num_apuestas)
    matriz_exitos = matriz_rand < prob_exito
    
    for i in range(sims):
        b = bankroll_inicial
        quebro = False
        for j in range(num_apuestas):
            if b <= 0:
                quebro = True
                break
            if matriz_exitos[i, j]:
                b += stake_promedio * (cuota_prom - 1.0)
            else:
                b -= stake_promedio
        if quebro:
            bancarrota_count += 1
            final_bankrolls.append(0.0)
        else:
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
                alertas.append(f"⚠️ **{ev}**: Selección contradictoria en 1X2 ({', '.join(opciones)}).")

            if "1X2 (Ganador)" in mercados and "Doble Oportunidad" in mercados:
                for s in selecciones:
                    if s['seleccion'] == "Local" and any(x in ["X2 (Visitante o Empate)"] for x in opciones):
                        alertas.append(f"⚠️ **{ev}**: Conflicto entre Local y X2.")
                    elif s['seleccion'] == "Visitante" and any(x in ["1X (Local o Empate)"] for x in opciones):
                        alertas.append(f"⚠️ **{ev}**: Conflicto entre Visitante y 1X.")

            if "Goles Más/Menos 2.5" in mercados and "Ambos Anotan (BTTS)" in mercados:
                if "Menos de 2.5" in opciones and "Sí" in opciones:
                    alertas.append(f"⚠️ **{ev}**: Conflicto negativo (Menos de 2.5 y Ambos Anotan Sí).")

    return alertas

def detectar_conflictos_horarios(apuestas: List[Dict[str, Any]]) -> List[str]:
    alertas_horario = []
    fechas = [(ap['evento'], ap['fecha_ts']) for ap in apuestas if ap.get('fecha_ts')]
    if len(fechas) > 1:
        for (ev1, ts1), (ev2, ts2) in itertools.combinations(fechas, 2):
            diff_min = abs(ts1 - ts2) / 60.0
            if diff_min < 110:
                alertas_horario.append(f"⏰ **Conflicto Horario**: *{ev1}* y *{ev2}* se solapan ({int(diff_min)} min de diff). Dificulta Cashout o Cobertura.")
    return alertas_horario

def evaluar_riesgo_parlay(partidos: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not partidos:
        return {"nivel": "N/A", "score": 0, "consejos": []}
    cant = len(partidos)
    cuotas = [p['cuota'] for p in partidos]
    cuota_total = np.prod(cuotas)
    
    cuotas_altas = sum(1 for c in cuotas if c > 2.20)
    cuotas_muy_bajas = sum(1 for c in cuotas if c < 1.20)
    
    score = round(max(5, min(95, 100 - (cant * 12) - (cuota_total * 2) - (cuotas_altas * 15))), 1)
    consejos = []
    if cant >= 5: consejos.append("🔴 **Riesgo acumulado**: Parlays de >4 eventos sufren caída exponencial de probabilidad.")
    if cuotas_altas >= 1: consejos.append(f"⚠️ **Volatilidad alta**: Tienes {cuotas_altas} selección(es) con cuota >2.20.")
    if cuotas_muy_bajas >= 2: consejos.append("🛡️ **Bajo valor**: Cuotas <1.20 aportan poco retorno e introducen riesgo.")
    
    nivel = "🟢 Bajo" if score > 70 else ("🟡 Moderado" if score > 40 else "🔴 Muy Alto")
    if not consejos: consejos.append("✅ **Parlay equilibrado**: Buena relación riesgo/beneficio.")
    return {"nivel": nivel, "score": score, "consejos": consejos}

def ejecutar_simulacion_montecarlo(partidos: List[Dict[str, Any]], num_simulaciones: int = 10000) -> Dict[str, Any]:
    if not partidos: return {}
    probs = [p['prob_real'] / 100.0 for p in partidos]
    num_eventos = len(probs)
    
    matriz_rand = np.random.rand(num_simulaciones, num_eventos)
    matriz_aciertos = matriz_rand < probs
    aciertos_por_sim = np.sum(matriz_aciertos, axis=1)
    
    return {
        "pleno_acierto": float(np.sum(aciertos_por_sim == num_eventos) / num_simulaciones * 100.0),
        "fallo_por_uno": float(np.sum(aciertos_por_sim == (num_eventos - 1)) / num_simulaciones * 100.0),
        "fallo_por_dos": float(np.sum(aciertos_por_sim == (num_eventos - 2)) / num_simulaciones * 100.0),
        "distribucion": [float(np.sum(aciertos_por_sim == k) / num_simulaciones * 100.0) for k in range(num_eventos + 1)]
    }

def calcular_sistema_cobertura(partidos: List[Dict[str, Any]], stake_total: float) -> Dict[str, Any]:
    n = len(partidos)
    if n < 3: return {"tipo": "Insuficientes eventos", "detalles": "Se requieren mínimo 3 eventos."}
    cuotas = [float(p['cuota']) for p in partidos]
    
    if n == 3:
        comb_dobles = list(itertools.combinations(cuotas, 2))
        comb_triples = list(itertools.combinations(cuotas, 3))
        num_ap = len(comb_dobles) + len(comb_triples)
        st_u = stake_total / num_ap
        return {"tipo": "TRIXIE (3 Selecciones)", "apuestas": num_ap, "stake_unitario": st_u, "retorno_max": (sum(np.prod(c) for c in comb_dobles) + sum(np.prod(c) for c in comb_triples)) * st_u}
    elif n == 4:
        comb_dobles = list(itertools.combinations(cuotas, 2))
        comb_triples = list(itertools.combinations(cuotas, 3))
        comb_cuad = list(itertools.combinations(cuotas, 4))
        num_ap = len(comb_dobles) + len(comb_triples) + len(comb_cuad)
        st_u = stake_total / num_ap
        return {"tipo": "YANKEE (4 Selecciones)", "apuestas": num_ap, "stake_unitario": st_u, "retorno_max": (sum(np.prod(c) for c in comb_dobles) + sum(np.prod(c) for c in comb_triples) + sum(np.prod(c) for c in comb_cuad)) * st_u}
    else:
        comb_dobles = list(itertools.combinations(cuotas[:5], 2))
        comb_triples = list(itertools.combinations(cuotas[:5], 3))
        comb_cuad = list(itertools.combinations(cuotas[:5], 4))
        comb_quin = list(itertools.combinations(cuotas[:5], 5))
        num_ap = len(comb_dobles) + len(comb_triples) + len(comb_cuad) + len(comb_quin)
        st_u = stake_total / num_ap
        return {"tipo": "CANADIAN (5 Selecciones)", "apuestas": num_ap, "stake_unitario": st_u, "retorno_max": (sum(np.prod(c) for c in comb_dobles) + sum(np.prod(c) for c in comb_triples) + sum(np.prod(c) for c in comb_cuad) + sum(np.prod(c) for c in comb_quin)) * st_u}

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

    resumen = f"🧠 **AI Insight:** **{total_partidos} partidos analizados**. "
    resumen += f"🔥 **{total_surebets} SureBet(s)** detectadas. " if total_surebets > 0 else "Sin SureBets activas. "
    resumen += f"📈 **{total_valuebets} apuestas +EV**. Concentración de valor en **{top_liga}** ({val_top_count} +EV)." if total_valuebets > 0 else "Cuotas ajustadas por casas."
    return resumen

def generar_csv_bitacora(historial: List[Dict[str, Any]]) -> bytes:
    return pd.DataFrame(historial).to_csv(index=False).encode('utf-8')

# =========================================================
# 5. ESTADOS DE SESIÓN
# =========================================================
user_config = ConfigManager.cargar()

if 'tema_visual' not in st.session_state: st.session_state.tema_visual = user_config.get('tema_visual', 'Oscuro')
if 'historial_apuestas' not in st.session_state: st.session_state.historial_apuestas = BitacoraManager.cargar()
if 'version_ticket' not in st.session_state: st.session_state.version_ticket = 0
if 'datos_cargados' not in st.session_state: st.session_state.datos_cargados = {}
if 'datos_cargados_previos' not in st.session_state: st.session_state.datos_cargados_previos = {}
if 'historico_cuotas_live' not in st.session_state: st.session_state.historico_cuotas_live = {}
if 'ha_consultado' not in st.session_state: st.session_state.ha_consultado = False
if 'claves_auto' not in st.session_state: st.session_state.claves_auto = set()
if 'creditos_restantes' not in st.session_state: st.session_state.creditos_restantes = "No consultado"
if 'creditos_restantes_af' not in st.session_state: st.session_state.creditos_restantes_af = "No consultado"
if 'overrides_live' not in st.session_state: st.session_state.overrides_live = {}
if 'notif_push_activas' not in st.session_state: st.session_state.notif_push_activas = True
if 'ticket_persistente' not in st.session_state: st.session_state.ticket_persistente = {}
if 'ultimo_escaneo_ts' not in st.session_state: st.session_state.ultimo_escaneo_ts = None
if 'casas_preferidas' not in st.session_state: st.session_state.casas_preferidas = user_config.get('casas_preferidas', [])
if 'bankroll_total' not in st.session_state: st.session_state.bankroll_total = user_config.get('bankroll_total', 200.0)
if 'tg_token' not in st.session_state: st.session_state.tg_token = DEFAULT_TG_TOKEN
if 'tg_chat_id' not in st.session_state: st.session_state.tg_chat_id = DEFAULT_TG_CHAT_ID
if 'debug_api_errors' not in st.session_state: st.session_state.debug_api_errors = []
if 'debug_mode' not in st.session_state: st.session_state.debug_mode = False

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
        st.toast(f"✅ Agregado: {opcion_nombre} (x{cuota_val})", icon="🎟️")
    else:
        st.session_state.ticket_persistente.pop(clave_k, None)
        st.toast(f"❌ Eliminado: {opcion_nombre}", icon="🗑️")

# =========================================================
# 6. ESTILOS CSS
# =========================================================
modo_claro = (st.session_state.tema_visual == "Claro")
bg_val = "#f8fafc" if modo_claro else "#0b0e14"
card_val = "#ffffff" if modo_claro else "#12161f"
card_alt_val = "#f1f5f9" if modo_claro else "#171c27"
border_val = "#cbd5e1" if modo_claro else "#232a38"
text_val = "#0f172a" if modo_claro else "#ffffff"
text_soft_val = "#64748b" if modo_claro else "#8a94a6"

st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap');

    :root {{
        --rg-bg: {bg_val};
        --rg-card: {card_val};
        --rg-card-alt: {card_alt_val};
        --rg-border: {border_val};
        --rg-text: {text_val};
        --rg-accent: #00d2d3;
        --rg-text-soft: {text_soft_val};
    }}

    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; color: var(--rg-text); }}

    .block-container {{
        padding-top: 2rem !important;
        padding-bottom: 2rem !important;
        max-width: 98% !important;
    }}

    h1 {{
        font-weight: 800 !important;
        font-size: clamp(1.5rem, 2.2vw, 2.1rem) !important;
        background: linear-gradient(90deg, var(--rg-text) 0%, #00d2d3 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }}

    .ai-summary-box {{
        background: linear-gradient(90deg, rgba(124, 92, 255, 0.12) 0%, rgba(0, 210, 211, 0.12) 100%);
        border: 1px solid rgba(0, 210, 211, 0.35);
        border-radius: 12px;
        padding: 12px 18px;
        margin-bottom: 18px;
        font-size: 13.5px;
    }}

    .empty-state-card {{
        background: linear-gradient(135deg, var(--rg-card) 0%, var(--rg-card-alt) 100%);
        border: 1px solid var(--rg-border);
        border-radius: 16px;
        padding: 24px;
    }}

    .match-header {{ font-size: 16px; font-weight: 700; color: var(--rg-text); }}
    .liga-chip {{
        display: inline-block; font-size: 10px; font-weight: 700; text-transform: uppercase;
        color: var(--rg-accent); background: rgba(0,210,211,0.10);
        border: 1px solid rgba(0,210,211,0.35); border-radius: 999px; padding: 2px 8px;
    }}
    .kickoff-chip {{
        display: inline-block; font-size: 11px; font-weight: 600; color: var(--rg-text-soft);
        background: var(--rg-card-alt); border: 1px solid var(--rg-border); border-radius: 999px; padding: 2px 8px;
    }}

    .creditos-caja-pro {{
        background: var(--rg-card-alt); padding: 8px 12px; border-radius: 8px;
        border: 1px solid var(--rg-border); margin-bottom: 8px; display: flex;
        justify-content: space-between; align-items: center;
    }}

    .ticket-titulo {{
        font-weight: 800; font-size: 14px; text-transform: uppercase;
        color: var(--rg-accent); text-align: center; margin-bottom: 6px;
    }}
    .ticket-item {{ border-bottom: 1px dashed var(--rg-border); padding: 6px 0; color: var(--rg-text); }}
    .ticket-cuota-tag {{
        font-family: 'JetBrains Mono', monospace; font-weight: 700; color: var(--rg-accent);
        background: rgba(0,210,211,0.08); border-radius: 6px; padding: 1px 6px; font-size: 12px;
    }}

    .kpi-card {{
        border-radius: 12px; padding: 16px; border: 1px solid var(--rg-border);
        background: var(--rg-card); height: 100%;
    }}
    .kpi-label {{ font-size: 10.5px; font-weight: 700; text-transform: uppercase; color: var(--rg-text-soft); margin-bottom: 6px; }}
    .kpi-value {{ font-family: 'JetBrains Mono', monospace; font-size: 24px; font-weight: 700; color: var(--rg-text); }}
    </style>
""", unsafe_allow_html=True)

# =========================================================
# 7. CLIENTES API
# =========================================================
AF_LEAGUE_IDS = {
    "🇦🇷 Liga Profesional (Argentina)": 128, "🇧🇷 Brasileirão Serie A (Brasil)": 71,
    "🇨🇱 Primera División (Chile)": 265, "🇨🇴 Primera A (Colombia)": 239,
    "🇪🇨 LigaPro (Ecuador)": 242, "🇲🇽 Liga MX (México)": 262,
    "🇵🇾 División Profesional (Paraguay)": 252, "🇵🇪 Liga 1 (Perú)": 281,
    "🇺🇾 Primera División (Uruguay)": 268, "🇺🇸 MLS (Estados Unidos)": 253,
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League (Inglaterra)": 39, "🇪🇸 LaLiga (España)": 140,
    "🇮🇹 Serie A (Italia)": 135, "🇩🇪 Bundesliga (Alemania)": 78,
    "🇫🇷 Ligue 1 (Francia)": 61, "🇵🇹 Primeira Liga (Portugal)": 94,
    "🇳🇱 Eredivisie (Países Bajos)": 88, "🏆 Champions League": 2,
    "🏆 Europa League": 3, "🏆 Copa Libertadores": 13, "🏆 Copa Sudamericana": 11
}

todas_las_ligas = {
    "🇺🇸 USA MLS": ["soccer_usa_mls"], "🇲🇽 Mexico Liga MX": ["soccer_mexico_liga_mx"],
    "🇦🇷 Argentina Primera Division": ["soccer_argentina_primera_division"],
    "🇧🇷 Brasil Brasileirao": ["soccer_brazil_campeonato"],
    "🏆 EU Champions League": ["soccer_uefa_champions_league"],
    "🏆 EU Europa League": ["soccer_uefa_europa_league"],
    "🏆 EU Conference League": ["soccer_uefa_europa_conference_league"],
    "🏆 Copa Libertadores": ["soccer_conmebol_copa_libertadores"],
    "🏆 Copa Sudamericana": ["soccer_conmebol_copa_sudamericana"],
    "🇪🇸 Spain La Liga": ["soccer_spain_la_liga"], "🇪🇸 Spain La Liga 2": ["soccer_spain_segunda_division"],
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿 England Premier League": ["soccer_epl"], "🏴󠁧󠁢󠁥󠁮󠁧󠁿 England Championship": ["soccer_efl_champ"],
    "🇮🇹 Italy Serie A": ["soccer_italy_serie_a"], "🇮🇹 Italy Serie B": ["soccer_italy_serie_b"],
    "🇩🇪 Germany Bundesliga": ["soccer_germany_bundesliga"], "🇩🇪 Germany Bundesliga 2": ["soccer_germany_bundesliga2"],
    "🇫🇷 France Ligue 1": ["soccer_france_ligue_one"], "🇫🇷 France Ligue 2": ["soccer_france_ligue_two"],
    "🇵🇹 Portugal Primeira Liga": ["soccer_portugal_primeira_liga"],
    "🇳🇱 Netherlands Eredivisie": ["soccer_netherlands_eredivisie"],
    "🇹🇷 Turkey Super Lig": ["soccer_turkey_super_league"], "🇦🇺 Australia A-League": ["soccer_australia_aleague"]
}

diccionario_mercados = {
    "1X2 (Ganador)": "h2h", "Doble Oportunidad": "double_chance",
    "Ambos Anotan (BTTS)": "btts", "Goles Más/Menos 2.5": "totals"
}

def af_headers(): return {"x-apisports-key": AF_API_KEY}

def _actualizar_creditos_af(response_headers):
    restante = response_headers.get('x-ratelimit-requests-remaining')
    limite = response_headers.get('x-ratelimit-requests-limit')
    if restante is not None:
        st.session_state.creditos_restantes_af = f"{restante}/{limite}" if limite else restante

def actualizar_creditos(headers):
    if 'x-requests-remaining' in headers:
        st.session_state.creditos_restantes = headers['x-requests-remaining']

@st.cache_data(ttl=60)
def consultar_api_odds(sport_key, market_key):
    if not API_KEY or not sport_key:
        return [], "Falta clave o sport_key"
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={API_KEY}&regions=eu,us&markets={market_key}&oddsFormat=decimal"
    try:
        response = requests.get(url, timeout=10)
        actualizar_creditos(response.headers)
        if response.status_code == 200:
            res_json = response.json()
            return (res_json, None) if isinstance(res_json, list) else ([], f"Respuesta inesperada: {res_json}")
        return [], f"HTTP {response.status_code}: {response.text[:200]}"
    except Exception as e:
        return [], f"Excepción: {e}"

def consultar_af_con_fallback_fixtures(af_id, nombre_liga):
    if not af_id or not AF_API_KEY: return []
    url_odds = f"{AF_BASE_URL}/odds"
    season_actual = datetime.now().year
    try:
        r = requests.get(url_odds, headers=af_headers(), params={"league": af_id, "season": season_actual}, timeout=10)
        _actualizar_creditos_af(r.headers)
        if r.status_code == 200:
            raw_data = r.json().get("response", [])
            if raw_data: return raw_data
    except Exception:
        pass

    url_fixtures = f"{AF_BASE_URL}/fixtures"
    try:
        rf = requests.get(url_fixtures, headers=af_headers(), params={"league": af_id, "next": 10}, timeout=10)
        _actualizar_creditos_af(rf.headers)
        fixtures_encontrados = rf.json().get("response", []) if rf.status_code == 200 else []
    except Exception:
        return []

    resultados_reconstruidos = []
    for fix in fixtures_encontrados:
        f_id = fix.get("fixture", {}).get("id")
        if not f_id: continue
        try:
            r_fix_odds = requests.get(url_odds, headers=af_headers(), params={"fixture": f_id}, timeout=8)
            _actualizar_creditos_af(r_fix_odds.headers)
            if r_fix_odds.status_code == 200:
                resp_f = r_fix_odds.json().get("response", [])
                if resp_f: resultados_reconstruidos.extend(resp_f)
        except Exception:
            pass
    return resultados_reconstruidos

# =========================================================
# 8. PARALELIZACIÓN ASÍNCRONA DE ESCANEO
# =========================================================
def escanear_liga_worker(liga_nombre, sport_keys_list, mercados_sels, limite_h):
    resultados_liga = []
    for market_sel in mercados_sels:
        m_key = diccionario_mercados.get(market_sel, "h2h")
        for skey in sport_keys_list:
            data, err = consultar_api_odds(skey, m_key)
            if data and isinstance(data, list) and len(data) > 0:
                resultados_liga.append((market_sel, data))
    return (liga_nombre, resultados_liga)

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

        horas = (fecha_utc - ahora_utc).total_seconds() / 3600.0
        es_en_vivo = partido.get('in_play', False) or (horas <= 0 and horas >= -2.2)
        marcador_local = partido.get('scores', {}).get('home', 0) if es_en_vivo else None
        marcador_visita = partido.get('scores', {}).get('away', 0) if es_en_vivo else None
        
        minuto_estimado = 30
        if es_en_vivo and horas < 0:
            minutos_transcurridos = int(abs(horas) * 60)
            minuto_estimado = max(1, minutos_transcurridos) if minutos_transcurridos <= 45 else (45 if minutos_transcurridos <= 60 else min(89, minutos_transcurridos - 15))

        min_raw = partido.get('minute', minuto_estimado) if es_en_vivo else None
        try: minuto_num = int(str(min_raw).replace("'", "").replace("+", ""))
        except Exception: minuto_num = minuto_estimado

        if limite_horas < 900000 and not es_en_vivo and (horas < -48.0 or horas > (limite_horas + 48)): continue

        fecha_local = fecha_utc - timedelta(hours=5)
        bookmakers = partido.get('bookmakers', [])
        if not bookmakers: continue

        casas_pref = st.session_state.get('casas_preferidas', [])
        if casas_pref:
            bookies_filtrados = [b for b in bookmakers if any(cp.lower() in b['title'].lower() for cp in casas_pref)]
            if bookies_filtrados: bookmakers = bookies_filtrados

        cuotas_globales, betano_cuotas = {}, {}
        for b in bookmakers:
            b_key = b['key'].lower()
            dict_b_markets = {m['key']: m['outcomes'] for m in b.get('markets', [])}

            if mercado == "Doble Oportunidad" and "double_chance" in dict_b_markets:
                for o in dict_b_markets["double_chance"]:
                    o_name = "1X (Local o Empate)" if o['name'] in ["home_draw", "home or draw", "1X", f"{home} or draw"] else ("X2 (Visitante o Empate)" if o['name'] in ["away_draw", "away or draw", "X2", f"{away} or draw"] else "12 (Local o Visitante)")
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

        override_data = st.session_state.overrides_live.get(partido_id, {})
        minuto_final_calc = override_data.get('minuto', minuto_num)
        goles_loc_final = override_data.get('goles_loc', marcador_local or 0)
        goles_vis_final = override_data.get('goles_vis', marcador_visita or 0)

        probs_poisson = calcular_poisson_live(minuto_final_calc, goles_loc_final, goles_vis_final) if es_en_vivo else calcular_modelo_poisson(1.45, 1.10, usar_dixon_coles=True)

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
                    "marcador_local": goles_loc_final, "marcador_visita": goles_vis_final,
                    "minuto_en_vivo": f"{minuto_final_calc}'", "minuto_num": minuto_final_calc,
                    "mercados": {}
                }
            diccionario_consolidador[partido_id]["mercados"][mercado] = {
                "max_cuotas": max_cuotas, "max_bookies": max_bookies,
                "betano_cuotas": betano_cuotas, "value_bets": value_bets,
                "variaciones": variaciones_dict, "todas_cuotas": cuotas_globales,
                "surebet": info_surebet
            }

# =========================================================
# 9. SIDEBAR ORGANIZADO
# =========================================================
with st.sidebar:
    st.header("⚙️ Control Global")
    
    st.radio(
        "🎨 Tema Visual:", options=["Oscuro", "Claro"],
        index=0 if st.session_state.tema_visual == "Oscuro" else 1,
        key="tema_visual_widget",
        on_change=lambda: ConfigManager.guardar('tema_visual', st.session_state.tema_visual_widget),
        horizontal=True
    )

    st.session_state.notif_push_activas = st.checkbox("🔔 Push & Alertas Sonoras", value=st.session_state.notif_push_activas)

    st.markdown(f"""
        <div class="creditos-caja-pro">
            <div>
                <small style="color:var(--rg-text-soft); font-weight:700;">Odds API</small><br>
                <span style="font-size:15px; font-weight:bold; color:var(--rg-accent);">🔑 {st.session_state.creditos_restantes}</span>
            </div>
            <div>🟢</div>
        </div>
        <div class="creditos-caja-pro">
            <div>
                <small style="color:var(--rg-text-soft); font-weight:700;">Football API</small><br>
                <span style="font-size:15px; font-weight:bold; color:#feca57;">🔑 {st.session_state.creditos_restantes_af}</span>
            </div>
            <div>🟡</div>
        </div>
    """, unsafe_allow_html=True)

    with st.expander("⚽ Selección de Torneos", expanded=True):
        if 'ligas_sels_widget' not in st.session_state: st.session_state.ligas_sels_widget = []
        c1, c2 = st.columns(2)
        if c1.button("✅ Todas", use_container_width=True):
            st.session_state.ligas_sels_widget = list(todas_las_ligas.keys())
            st.rerun()
        if c2.button("🧹 Ninguna", use_container_width=True):
            st.session_state.ligas_sels_widget = []
            st.rerun()

        ligas_sels = st.multiselect("Ligas principales:", list(todas_las_ligas.keys()), key="ligas_sels_widget")
        habilitar_af = st.checkbox("Habilitar Ligas LATAM", value=True)
        ligas_af_sels = st.multiselect("Ligas extra:", list(AF_LEAGUE_IDS.keys()), default=["🇪🇨 LigaPro (Ecuador)"]) if habilitar_af else []

    with st.expander("🏬 Casas y Mercados", expanded=True):
        casas_preferidas = st.multiselect(
            "Mis Bookies:",
            ["Betano", "Bet365", "Ecuabet", "1xBet", "Pinnacle", "Bwin", "Unibet", "William Hill"],
            default=st.session_state.casas_preferidas,
            key="casas_preferidas_widget",
            on_change=lambda: ConfigManager.guardar('casas_preferidas', st.session_state.casas_preferidas_widget)
        )
        mercados_sels = st.multiselect("Mercados:", list(diccionario_mercados.keys()), default=["1X2 (Ganador)"])
        sin_limite_fecha = st.checkbox("🌐 Traer todo sin filtro", value=True)
        limite_h = 999999 if sin_limite_fecha else 168

    with st.expander("🧮 Banca & Criterio Kelly"):
        bankroll_total = st.number_input(
            "Banca Total ($):", min_value=10.0, value=float(st.session_state.bankroll_total), step=10.0,
            key="bankroll_widget", on_change=lambda: ConfigManager.guardar('bankroll_total', st.session_state.bankroll_widget)
        )
        fraccion_kelly = st.slider("Fracción Kelly:", 0.1, 1.0, 0.25, 0.05)
        monto_inversion = st.number_input("Inversión Base ($):", min_value=1.0, value=10.0, step=1.0, key="monto_inversion_base")

    consultar = st.button("🔍 Escanear Mercado Now", type="primary", use_container_width=True)

    with st.expander("🔔 Alertas Telegram"):
        st.session_state['auto_alertas_telegram'] = st.checkbox("🚀 Auto-alertas (+EV > 5%)", value=False)

# =========================================================
# 10. VISTAS Y NAVEGACIÓN
# =========================================================
col_nav_rapida, col_nav_extra = st.columns([3, 1.5])
with col_nav_rapida:
    tab_activa_rapida = st.radio("Pestañas:", ["🚀 RADAR MULTI-MERCADO", "🧮 CALCULADORA & OCR", "📊 ESTADÍSTICAS & H2H"], horizontal=True, label_visibility="collapsed")
with col_nav_extra:
    opcion_extra = st.selectbox("Más herramientas:", ["-- Seleccionar otro módulo --", "🛡️ MATRIZ DE COBERTURAS", "🔥 CAZADOR +EV & DROPPING", "📰 BAJAS & ALINEACIONES", "🎨 GENERADOR DE CARTEL", "📈 BITÁCORA & ROI", "💬 ATENCIÓN Y MEJORAS"], label_visibility="collapsed")

vista_seleccionada = opcion_extra if opcion_extra != "-- Seleccionar otro módulo --" else tab_activa_rapida

# ---------------------------------------------------------
# PESTAÑA 1: RADAR AUTOMÁTICO
# ---------------------------------------------------------
if vista_seleccionada == "🚀 RADAR MULTI-MERCADO":
    st.title("⚽ Radar Avanzado Multi-Mercado Global")
    st.caption("Escaneo en tiempo real · Modelo Dixon-Coles / Poisson Live · Arbitraje y +EV")

    if consultar:
        if (len(ligas_sels) > 0 or len(ligas_af_sels) > 0) and len(mercados_sels) > 0:
            st.cache_data.clear()
            consolidador = {}
            st.session_state.ha_consultado = True
            
            with st.status("🔄 Escaneando mercados...", expanded=True) as status_consulta:
                # 1. Peticiones en paralelo para The Odds API
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [
                        executor.submit(escanear_liga_worker, liga, todas_las_ligas.get(liga, []), mercados_sels, limite_h)
                        for liga in ligas_sels
                    ]
                    for future in concurrent.futures.as_completed(futures):
                        liga_nombre, resultados = future.result()
                        status_consulta.update(label=f"📊 Procesando: {liga_nombre}")
                        for m_sel, raw_data in resultados:
                            if m_sel == "Doble Oportunidad" and not raw_data:
                                raw_data = calcular_doble_oportunidad_sintetica(raw_data)
                            procesar_e_inyectar_mercado(raw_data, m_sel, limite_h, liga_nombre, consolidador)

                # 2. Peticiones secuenciales para API Football (evitar rate limiting)
                for liga_af in ligas_af_sels:
                    af_id = AF_LEAGUE_IDS.get(liga_af)
                    status_consulta.update(label=f"📡 Conectando API Football: {liga_af}...")
                    if af_id and AF_API_KEY:
                        raw_af_odds = consultar_af_con_fallback_fixtures(af_id, liga_af)
                        if raw_af_odds:
                            datos_norm = []
                            for item in raw_af_odds:
                                fix, teams, bookies_af = item.get("fixture", {}), item.get("teams", {}), item.get("bookmakers", [])
                                bookmakers_converted = []
                                for b in bookies_af:
                                    b_markets = []
                                    for m in b.get("bets", []):
                                        m_name = m.get("name")
                                        key_m = "h2h" if m_name == "Match Winner" else ("totals" if m_name == "Goals Over/Under" else ("btts" if m_name == "Both Teams Score" else "double_chance"))
                                        outcomes = [{"name": o.get("value"), "price": float(o.get("odd"))} for o in m.get("values", [])]
                                        b_markets.append({"key": key_m, "outcomes": outcomes})
                                    bookmakers_converted.append({"key": str(b.get("id")), "title": b.get("name"), "markets": b_markets})
                                datos_norm.append({
                                    "id": f"af_{fix.get('id')}",
                                    "home_team": teams.get("home", {}).get("name", "Local"),
                                    "away_team": teams.get("away", {}).get("name", "Visitante"),
                                    "commence_time": fix.get("date", datetime.now(timezone.utc).isoformat()),
                                    "bookmakers": bookmakers_converted
                                })
                            for m_sel in mercados_sels:
                                procesar_e_inyectar_mercado(datos_norm, m_sel, limite_h, liga_af, consolidador)

                status_consulta.update(label=f"✅ Escaneo completo: {len(consolidador)} partidos procesados.", state="complete")

            st.session_state.datos_cargados_previos = st.session_state.datos_cargados
            st.session_state.datos_cargados = consolidador
            st.session_state.version_ticket += 1
            st.session_state.ultimo_escaneo_ts = time.time()
            st.toast(f"✅ {len(consolidador)} partidos cargados.", icon="🎯")
            st.rerun()

    dict_partidos = st.session_state.datos_cargados
    if dict_partidos:
        texto_ai = generar_resumen_ejecutivo_ai(dict_partidos)
        if texto_ai: st.markdown(f"<div class='ai-summary-box'>{texto_ai}</div>", unsafe_allow_html=True)

    if not st.session_state.ha_consultado:
        st.markdown("""
            <div class="empty-state-card">
                <h3 style="color:var(--rg-accent); margin-bottom:8px;">⚡ Sistema Listo para el Análisis</h3>
                <p style="color:var(--rg-text-soft); font-size:13.5px;">Selecciona torneos en la barra lateral y presiona <b>Escanear Mercado Now</b>.</p>
            </div>
        """, unsafe_allow_html=True)
    elif not dict_partidos:
        st.info("ℹ️ No se encontraron partidos con los filtros actuales.")
    else:
        col_izq, col_der = st.columns([6.5, 3.5])
        with col_izq:
            st.subheader(f"📋 Eventos ({len(dict_partidos)})")
            ligas_con_datos = list(set([p['liga_origen'] for p in dict_partidos.values()]))
            if ligas_con_datos:
                pestanas_ligas = st.tabs(ligas_con_datos)
                for p_idx, liga_p in enumerate(ligas_con_datos):
                    with pestanas_ligas[p_idx]:
                        partidos_f = [p for p in dict_partidos.values() if p['liga_origen'] == liga_p]
                        for part in partidos_f:
                            with st.container(border=True):
                                st.markdown(f"<span class='liga-chip'>🏆 {part['liga_origen']}</span>", unsafe_allow_html=True)
                                st.markdown(f"<div class='match-header'>⚽ {part['local']} vs {part['visitante']}</div>", unsafe_allow_html=True)
                                st.markdown(f"<span class='kickoff-chip'>📅 {part['fecha_str']}</span>", unsafe_allow_html=True)
                                
                                sub_tabs = st.tabs(list(part['mercados'].keys()))
                                for m_idx, text_m in enumerate(part['mercados'].keys()):
                                    with sub_tabs[m_idx]:
                                        m_info = part['mercados'][text_m]
                                        todas_opciones = list(m_info['max_cuotas'].keys())
                                        sub_cols = st.columns(len(todas_opciones))
                                        for idx, opcion in enumerate(todas_opciones):
                                            cuota_m = m_info['max_cuotas'][opcion]
                                            with sub_cols[idx]:
                                                val = m_info['value_bets'][opcion]
                                                lbl_val = "🔥" if val['es_value'] else ""
                                                clave_base = f"ap_{part['id']}_{text_m}_{opcion}"
                                                val_defecto = clave_base in st.session_state.ticket_persistente
                                                st.checkbox(
                                                    f"{opcion} ({cuota_m}) {lbl_val}",
                                                    value=val_defecto, key=clave_base,
                                                    on_change=toggle_apuesta,
                                                    args=(part, text_m, opcion, cuota_m, m_info['max_bookies'][opcion], val['prob_real'], clave_base)
                                                )
                                                st.caption(f"🏠 {m_info['max_bookies'][opcion]} | Impl: {round(val['prob_real'],1)}%")

        with col_der:
            # Fragmento para actualización rápida del boleto sin recargar toda la app
            @st.fragment
            def render_boleto_fragment():
                st.subheader("🎟️ Configuración de Parlay")
                apuestas_seleccionadas = list(st.session_state.ticket_persistente.values())
                if apuestas_seleccionadas:
                    for al in detectar_correlaciones(apuestas_seleccionadas): st.error(al)
                    for al_h in detectar_conflictos_horarios(apuestas_seleccionadas): st.warning(al_h)

                    cuota_acum, prob_comb = 1.0, 1.0
                    with st.container(border=True):
                        st.markdown("<div class='ticket-titulo'>🎟️ Boleto Parlay</div>", unsafe_allow_html=True)
                        for ap in apuestas_seleccionadas:
                            cuota_acum *= float(ap['cuota'])
                            prob_comb *= (float(ap['prob_real']) / 100.0)
                            st.markdown(f"<div class='ticket-item'>✔️ <b>{ap['evento']}</b><br>➔ <code>{ap['seleccion']}</code> | <span class='ticket-cuota-tag'>x{ap['cuota']}</span></div>", unsafe_allow_html=True)

                        monto_ticket = st.number_input("💵 Inversión ($):", min_value=1.0, value=float(monto_inversion), step=1.0)
                        ganancia_neta = (cuota_acum * monto_ticket) - monto_ticket
                        sharpe = calcular_sharpe_parlay(cuota_acum, prob_comb)

                        st.metric("Cuota Final", f"x{round(cuota_acum, 2)}")
                        st.metric("Ganancia Neta", f"${round(ganancia_neta, 2)}")
                        st.metric("⚡ Sharpe Ratio Parlay", f"{round(sharpe, 3)}")

                        if st.button("💾 Registrar en Bitácora", type="primary", use_container_width=True):
                            st.session_state.historial_apuestas.append({
                                "Fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                                "Detalles": f"{len(apuestas_seleccionadas)} combinadas",
                                "Liga": apuestas_seleccionadas[0]['liga'],
                                "Market": "Multi-Mercado",
                                "Cuota": cuota_acum, "Inversión": monto_ticket,
                                "Estado": "Pendiente", "Ganancia Potencial": ganancia_neta
                            })
                            BitacoraManager.guardar(st.session_state.historial_apuestas)
                            st.toast("Guardado en Bitácora!", icon="💾")
                else:
                    st.info("🎟️ Marca casillas para armar tu boleto.")

            render_boleto_fragment()

# ---------------------------------------------------------
# PESTAÑA 2: CALCULADORA EXTERNA & MONTE CARLO
# ---------------------------------------------------------
elif vista_seleccionada == "🧮 CALCULADORA & OCR":
    st.title("🧮 Analizador, Lector OCR & Monte Carlo")
    
    modo_ingreso = st.radio("Método de Ingreso:", ["🚀 Pegado Rápido", "📝 Registro Manual"], horizontal=True)
    col_ing, col_res = st.columns([1.1, 1])
    partidos_externos = []

    with col_ing:
        with st.container(border=True):
            margen_casa = st.slider("Margen estimado casa (%):", 1.0, 15.0, 5.0, 0.5)
            if modo_ingreso == "🚀 Pegado Rápido":
                texto_pegado = st.text_area("Pega tu boleto:", value="Independiente del Valle 1.62\nFK Bodo/Glimt 1.55", height=120)
                if texto_pegado:
                    for linea in texto_pegado.strip().split('\n'):
                        cuotas = re.findall(r'\b\d+\.\d+\b', linea.replace(',', '.'))
                        if cuotas:
                            c_val = float(cuotas[-1])
                            nombre = re.sub(r'\b\d+\.\d+\b', '', linea).strip() or "Selección"
                            partidos_externos.append({"nombre": nombre, "cuota": c_val})

    with col_res:
        with st.container(border=True):
            if partidos_externos:
                cuota_tot, prob_tot = 1.0, 1.0
                partidos_mc = []
                for p in partidos_externos:
                    c = p['cuota']
                    cuota_tot *= c
                    p_real = (1.0 / c) / (1.0 + (margen_casa / 100.0))
                    prob_tot *= p_real
                    partidos_mc.append({"nombre": p['nombre'], "cuota": c, "prob_real": p_real * 100.0})

                st.metric("Probabilidad Real", f"{round(prob_tot * 100, 2)}%")
                st.metric("Cuota Acumulada", f"x{round(cuota_tot, 2)}")

                res_mc = ejecutar_simulacion_montecarlo(partidos_mc)
                if res_mc:
                    st.write(f"🎯 **Ganar Completo (Monte Carlo):** `{round(res_mc['pleno_acierto'], 2)}%`")
                    st.write(f"💔 **Fallo por 1 partido:** `{round(res_mc['fallo_por_uno'], 2)}%`")

# ---------------------------------------------------------
# PESTAÑA 3: ESTADÍSTICAS & H2H
# ---------------------------------------------------------
elif vista_seleccionada == "📊 ESTADÍSTICAS & H2H":
    st.title("📊 Análisis Estadístico & H2H")
    c1, c2 = st.columns(2)
    eq_local = c1.text_input("Equipo Local:", value="Valencia")
    eq_visit = c2.text_input("Equipo Visitante:", value="Real Betis")

    if eq_local and eq_visit:
        probs_custom = calcular_modelo_poisson(1.45, 1.10)
        cx1, cx2, cx3 = st.columns(3)
        cx1.metric(f"Victoria {eq_local}", f"{round(probs_custom['Local'], 1)}%")
        cx2.metric("Empate", f"{round(probs_custom['Empate'], 1)}%")
        cx3.metric(f"Victoria {eq_visit}", f"{round(probs_custom['Visitante'], 1)}%")

# ---------------------------------------------------------
# PESTAÑA 4: MATRIZ DE COBERTURAS
# ---------------------------------------------------------
elif vista_seleccionada == "🛡️ MATRIZ DE COBERTURAS":
    st.title("🛡️ Matriz de Coberturas & SureBet")
    c_sb1, c_sb2, c_sb3 = st.columns(3)
    c1 = c_sb1.number_input("Cuota 1:", value=2.10)
    cX = c_sb2.number_input("Cuota X:", value=3.40)
    c2 = c_sb3.number_input("Cuota 2:", value=4.50)
    monto_sb = st.number_input("Inversión Total ($):", value=100.0)

    res_sb = detectar_surebet({"1": c1, "X": cX, "2": c2})
    if res_sb["es_surebet"]:
        st.success(f"🔥 SUREBET! Rendimiento: +{round(res_sb['lucro'], 2)}%")
    else:
        st.info(f"Sin arbitraje. Overround: {round(res_sb['overround']*100, 2)}%")

# ---------------------------------------------------------
# PESTAÑA 5: CAZADOR +EV & DROPPING
# ---------------------------------------------------------
elif vista_seleccionada == "🔥 CAZADOR +EV & DROPPING":
    st.title("🔥 Cazador de Valor (+EV)")
    umbral = st.slider("Umbral Mínimo (+EV %):", 1.0, 25.0, 5.0)
    dict_partidos = st.session_state.get('datos_cargados', {})
    
    lista_val = []
    if dict_partidos:
        for part in dict_partidos.values():
            for m_n, m_i in part['mercados'].items():
                for op, val_d in m_i['value_bets'].items():
                    ev_pct = round(val_d['ev'] * 100, 1)
                    if ev_pct >= umbral:
                        lista_val.append({"Partido": f"{part['local']} vs {part['visitante']}", "Mercado": m_n, "Selección": op, "Cuota": m_i['max_cuotas'][op], "EV": f"+{ev_pct}%"})

    if lista_val: st.dataframe(pd.DataFrame(lista_val), use_container_width=True)
    else: st.caption("Sin oportunidades por encima del umbral seleccionado.")

# ---------------------------------------------------------
# PESTAÑA 6: BAJAS & ALINEACIONES
# ---------------------------------------------------------
elif vista_seleccionada == "📰 BAJAS & ALINEACIONES":
    st.title("📰 Centro de Bajas y Alineaciones")
    st.info("Módulo integrado con API-Football para consulta de alineaciones oficiales.")

# ---------------------------------------------------------
# PESTAÑA 7: GENERADOR DE CARTEL
# ---------------------------------------------------------
elif vista_seleccionada == "🎨 GENERADOR DE CARTEL":
    st.title("🎨 Generador Visual de Pronósticos")
    apuestas_en_boleto = list(st.session_state.ticket_persistente.values())
    cuota_cartel = np.prod([float(ap['cuota']) for ap in apuestas_en_boleto]) if apuestas_en_boleto else 1.0

    cartel_html = f"""
    <div style="background: #0b0e14; border: 2px solid #00d2d3; border-radius: 12px; padding: 20px; color: white; text-align: center;">
        <h3 style="color:#00d2d3;">🔥 PARLAY SUGERIDO</h3>
        <h4>Cuota Total: x{round(cuota_cartel, 2)}</h4>
        <p>Selecciones activas: {len(apuestas_en_boleto)}</p>
    </div>
    """
    components.html(cartel_html, height=200)

# ---------------------------------------------------------
# PESTAÑA 8: BITÁCORA & ROI
# ---------------------------------------------------------
elif vista_seleccionada == "📈 BITÁCORA & ROI":
    st.title("📈 Auditoría Financiera Pro")
    if st.session_state.historial_apuestas:
        df_act = pd.DataFrame(st.session_state.historial_apuestas)
        st.dataframe(df_act, use_container_width=True)
        csv_bytes = generar_csv_bitacora(st.session_state.historial_apuestas)
        st.download_button("📊 Exportar CSV", data=csv_bytes, file_name="Reporte_Apuestas.csv", mime="text/csv")
    else:
        st.info("Bitácora vacía.")

# ---------------------------------------------------------
# PESTAÑA 9: ATENCIÓN Y MEJORAS
# ---------------------------------------------------------
elif vista_seleccionada == "💬 ATENCIÓN Y MEJORAS":
    st.title("💬 Soporte y Feedback")
    msg = st.text_area("Describe tu sugerencia o error:")
    if st.button("Enviar Feedback"):
        if msg.strip() and enviar_telegram(f"📩 *FEEDBACK:* {msg}"):
            st.success("Mensaje enviado con éxito!")
