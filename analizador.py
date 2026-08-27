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
import time
from typing import Dict, List, Any, Optional
from PIL import Image
import streamlit.components.v1 as components

# =========================================================
# 1. CONFIGURACIÓN DE PÁGINA Y CREDENCIALES SEGURAS
# =========================================================
st.set_page_config(
    page_title="Radar Enterprise Parlay Global - UX Edition",
    page_icon="⚽",
    layout="wide"
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
        config = ConfigManager.cargar()
        config[key] = val
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            st.sidebar.error(f"Error al guardar configuración: {e}")

def enviar_telegram(mensaje: str) -> bool:
    token = st.session_state.get('tg_token', DEFAULT_TG_TOKEN)
    chat_id = st.session_state.get('tg_chat_id', DEFAULT_TG_CHAT_ID)
    if not token or not chat_id:
        st.warning("⚠️ El bot de Telegram no está configurado correctamente en secrets.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        st.error(f"💥 Error de conexión con Telegram: {e}")
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
    }} catch(e) {{ console.log(e); }}
    </script>
    """
    components.html(js_code, height=0, width=0)

# =========================================================
# 3. MODELADO MATEMÁTICO: POISSON, DIXON-COLES & LIVE POISSON DINÁMICO
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

def calcular_modelo_poisson(lambda_local: float = 1.45, lambda_visita: float = 1.10, usar_dixon_coles: bool = True, bajas_local_pct: float = 0.0, bajas_visita_pct: float = 0.0) -> Dict[str, float]:
    lambda_l = calcular_impacto_bajas(lambda_local, bajas_local_pct)
    lambda_v = calcular_impacto_bajas(lambda_visita, bajas_visita_pct)

    max_goles = 6
    matriz_prob = np.zeros((max_goles, max_goles))
    
    for i in range(max_goles):
        for j in range(max_goles):
            p_base = poisson_pmf(i, lambda_l) * poisson_pmf(j, lambda_v)
            tau = dixon_coles_factor(i, j, lambda_l, lambda_v) if usar_dixon_coles else 1.0
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
    tiempo_restante_pct = max(0.01, (90 - min(89, minuto)) / 90.0)
    lambda_l_rem = lambda_l_base * tiempo_restante_pct
    lambda_v_rem = lambda_v_base * tiempo_restante_pct

    max_goles_rem = 6
    matriz_prob = np.zeros((max_goles_rem, max_goles_rem))

    for i in range(max_goles_rem):
        for j in range(max_goles_rem):
            matriz_prob[i, j] = poisson_pmf(i, lambda_l_rem) * poisson_pmf(j, lambda_v_rem)

    soma = np.sum(matriz_prob)
    if soma > 0:
        matriz_prob /= soma

    prob_local_win, prob_empate_win, prob_visita_win = 0.0, 0.0, 0.0
    prob_over25_live, prob_btts_live = 0.0, 0.0
    goles_actuales = goles_loc + goles_vis

    for i in range(max_goles_rem):
        for j in range(max_goles_rem):
            tot_loc = goles_loc + i
            tot_vis = goles_vis + j
            p = matriz_prob[i, j]

            if tot_loc > tot_vis: prob_local_win += p
            elif tot_loc == tot_vis: prob_empate_win += p
            else: prob_visita_win += p

            if (goles_actuales + i + j) > 2.5:
                prob_over25_live += p
            if tot_loc > 0 and tot_vis > 0:
                prob_btts_live += p

    return {
        "Local": float(prob_local_win * 100),
        "Empate": float(prob_empate_win * 100),
        "Visitante": float(prob_visita_win * 100),
        "1X (Local o Empate)": float((prob_local_win + prob_empate_win) * 100),
        "X2 (Visitante o Empate)": float((prob_visita_win + prob_empate_win) * 100),
        "12 (Local o Visitante)": float((prob_local_win + prob_visita_win) * 100),
        "Sí": float(prob_btts_live * 100),
        "No": float((1.0 - prob_btts_live) * 100),
        "Más de 2.5": float(prob_over25_live * 100),
        "Menos de 2.5": float((1.0 - prob_over25_live) * 100)
    }

# =========================================================
# 4. VALIDADOR, ARBITRAJE, SHARPE RATIO & RUINA MONTE CARLO
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

def detectar_conflictos_horarios(apuestas: List[Dict[str, Any]]) -> List[str]:
    alertas_horario = []
    fechas = []
    for ap in apuestas:
        ts = ap.get('fecha_ts', None)
        if ts:
            fechas.append((ap['evento'], ts))
    
    if len(fechas) > 1:
        for (ev1, ts1), (ev2, ts2) in itertools.combinations(fechas, 2):
            diff_min = abs(ts1 - ts2) / 60.0
            if diff_min < 110:
                alertas_horario.append(f"⏰ **Conflicto de Horario**: *{ev1}* y *{ev2}* se juegan simultáneamente (diferencia de {int(diff_min)} min). Esto impedirá hacer Cashout o Cobertura manual (*Hedge*) entre ambos partidos.")
    return alertas_horario

def evaluar_riesgo_parlay(partidos: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not partidos:
        return {"nivel": "N/A", "score": 0, "consejos": []}
    
    cant = len(partidos)
    cuotas = [p['cuota'] for p in partidos]
    cuota_total = np.prod(cuotas)
    
    cuotas_altas = sum(1 for c in cuotas if c > 2.20)
    cuotas_muy_bajas = sum(1 for c in cuotas if c < 1.20)
    
    score = 100 - (cant * 12) - (cuota_total * 2) - (cuotas_altas * 15)
    score = round(max(5, min(95, score)), 1)
    
    consejos = []
    if cant >= 5:
        consejos.append("🔴 **Riesgo acumulado alto**: Parlays de más de 4 eventos sufren caídas drásticas de probabilidad exponencial.")
    if cuotas_altas >= 1:
        consejos.append(f"⚠️ **Cuota individual alta (>2.20)**: Tienes {cuotas_altas} selección(es) con mayor volatilidad.")
    if cuotas_muy_bajas >= 2:
        consejos.append("🛡️ **Trampa de favoritismo**: Las cuotas menores a 1.20 aportan poco valor y acumulan riesgo.")
    
    if score > 70:
        nivel = "🟢 Bajo"
        if not consejos:
            consejos.append("✅ **Parlay bien equilibrado**: El número de selecciones y cuotas mantiene una relación de riesgo aceptable.")
    elif score > 40:
        nivel = "🟡 Moderado"
        if not consejos:
            consejos.append("🟡 **Riesgo Moderado**: Combina varias selecciones; monitorea la inversión.")
    else:
        nivel = "🔴 Muy Alto"
        if not consejos:
            consejos.append("🔴 **Riesgo Elevado**: La cuota total o la volatilidad individual hacen este parlay altamente especulativo.")

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

def generar_resumen_ejecutivo_ai(dict_partidos: Dict[str, Any]) -> str:
    if not dict_partidos:
        return ""
    
    total_partidos = len(dict_partidos)
    total_surebets = 0
    total_valuebets = 0
    liga_con_mas_valor = {}

    for p in dict_partidos.values():
        liga = p['liga_origen']
        for m_nombre, m_info in p['mercados'].items():
            if m_info.get('surebet', {}).get('es_surebet', False):
                total_surebets += 1
            for val_item in m_info.get('value_bets', {}).values():
                if val_item.get('ev', 0) > 0.03:
                    total_valuebets += 1
                    liga_con_mas_valor[liga] = liga_con_mas_valor.get(liga, 0) + 1

    top_liga = max(liga_con_mas_valor, key=liga_con_mas_valor.get) if liga_con_mas_valor else "General"
    val_top_count = liga_con_mas_valor.get(top_liga, 0)

    resumen = f"🧠 **AI Insight Summary:** Actualmente hay **{total_partidos} partidos escaneados** en el radar. "
    if total_surebets > 0:
        resumen += f"🔥 Se han detectado **{total_surebets} SureBet(s) de arbitraje sin riesgo**. "
    else:
        resumen += "Sin SureBets activas por el momento. "

    if total_valuebets > 0:
        resumen += f"📈 Se hallaron **{total_valuebets} apuestas de valor positivo (+EV)**. La liga con mayor concentración de valor es **{top_liga}** ({val_top_count} apuestas +EV)."
    else:
        resumen += "El mercado muestra líneas ajustadas por las casas."

    return resumen

# =========================================================
# 5. EXPORTADOR CSV
# =========================================================
def generar_csv_bitacora(historial: List[Dict[str, Any]]) -> bytes:
    df_data = pd.DataFrame(historial)
    return df_data.to_csv(index=False).encode('utf-8')

# =========================================================
# 6. ESTADOS DE SESIÓN Y PERSISTENCIA
# =========================================================
user_config = ConfigManager.cargar()

if 'tema_visual' not in st.session_state:
    st.session_state.tema_visual = user_config.get('tema_visual', 'Oscuro')

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
if 'overrides_live' not in st.session_state:
    st.session_state.overrides_live = {}
if 'notif_push_activas' not in st.session_state:
    st.session_state.notif_push_activas = True

if 'ticket_persistente' not in st.session_state:
    st.session_state.ticket_persistente = {}

if 'ultimo_escaneo_ts' not in st.session_state:
    st.session_state.ultimo_escaneo_ts = None

if 'casas_preferidas' not in st.session_state:
    st.session_state.casas_preferidas = user_config.get('casas_preferidas', [])
if 'bankroll_total' not in st.session_state:
    st.session_state.bankroll_total = user_config.get('bankroll_total', 200.0)
if 'tg_token' not in st.session_state:
    st.session_state.tg_token = DEFAULT_TG_TOKEN
if 'tg_chat_id' not in st.session_state:
    st.session_state.tg_chat_id = DEFAULT_TG_CHAT_ID
if 'debug_api_errors' not in st.session_state:
    st.session_state.debug_api_errors = []
if 'debug_mode' not in st.session_state:
    st.session_state.debug_mode = False

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
        st.toast(f"✅ Agregado al boleto: {opcion_nombre} (x{cuota_val})", icon="🎟️")
    else:
        st.session_state.ticket_persistente.pop(clave_k, None)
        st.toast(f"❌ Eliminado del boleto: {opcion_nombre}", icon="🗑️")

# =========================================================
# 7. TEMA VISUAL CSS DINÁMICO
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
        --rg-accent-2: #7c5cff;
        --rg-success: #2ecc71;
        --rg-warn: #f1c40f;
        --rg-danger: #e74c3c;
        --rg-text-soft: {text_soft_val};
    }}

    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; color: var(--rg-text); }}

    .block-container {{
        padding-top: 3.2rem !important;
        padding-bottom: 2rem !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
        max-width: 98% !important;
    }}

    h1, h2, h3 {{ color: var(--rg-text) !important; }}

    h1 {{
        font-family: 'Inter', sans-serif;
        font-weight: 800 !important;
        letter-spacing: -0.02em;
        font-size: clamp(1.5rem, 2.2vw, 2.1rem) !important;
        background: linear-gradient(90deg, var(--rg-text) 0%, #00d2d3 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem !important;
    }}

    .ai-summary-box {{
        background: linear-gradient(90deg, rgba(124, 92, 255, 0.12) 0%, rgba(0, 210, 211, 0.12) 100%);
        border: 1px solid rgba(0, 210, 211, 0.35);
        border-radius: 12px;
        padding: 12px 18px;
        margin-bottom: 18px;
        font-size: 13.5px;
        color: var(--rg-text);
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }}

    .empty-state-card {{
        background: linear-gradient(135deg, var(--rg-card) 0%, var(--rg-card-alt) 100%);
        border: 1px solid var(--rg-border);
        border-radius: 16px;
        padding: 30px;
        box-shadow: 0 12px 30px rgba(0,0,0,0.15);
    }}
    
    .empty-state-step {{
        background: var(--rg-card-alt);
        border: 1px solid var(--rg-border);
        border-radius: 12px;
        padding: 18px 14px;
        text-align: center;
        transition: all 0.25s ease-in-out;
    }}

    .step-badge {{
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
    }}

    .match-header {{ font-size: 16px; font-weight: 700; margin-bottom: 2px; color: var(--rg-text); }}
    .liga-chip {{
        display: inline-block; font-size: 10px; font-weight: 700; text-transform: uppercase;
        letter-spacing: 0.04em; color: var(--rg-accent); background: rgba(0,210,211,0.10);
        border: 1px solid rgba(0,210,211,0.35); border-radius: 999px; padding: 2px 8px; margin-bottom: 4px;
    }}
    .kickoff-chip {{
        display: inline-block; font-size: 11px; font-weight: 600; color: var(--rg-text-soft);
        background: var(--rg-card-alt); border: 1px solid var(--rg-border); border-radius: 999px;
        padding: 2px 8px; margin-top: 2px;
    }}

    .creditos-caja-pro {{
        background: var(--rg-card-alt);
        padding: 8px 12px;
        border-radius: 8px;
        border: 1px solid var(--rg-border);
        margin-bottom: 8px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}

    .ticket-titulo {{
        font-family: 'Inter', sans-serif; font-weight: 800; font-size: 14px; letter-spacing: 0.04em;
        text-transform: uppercase; color: var(--rg-accent); text-align: center; margin-bottom: 6px;
    }}
    .ticket-item {{ border-bottom: 1px dashed var(--rg-border); padding: 6px 0 8px 0; color: var(--rg-text); }}
    .ticket-item:last-of-type {{ border-bottom: none; }}
    .ticket-cuota-tag {{
        font-family: 'JetBrains Mono', monospace; font-weight: 700; color: var(--rg-accent);
        background: rgba(0,210,211,0.08); border-radius: 6px; padding: 1px 6px; font-size: 12px;
    }}

    .kpi-card {{
        border-radius: 12px;
        padding: 16px;
        border: 1px solid var(--rg-border);
        background: var(--rg-card);
        box-shadow: 0 8px 20px rgba(0,0,0,0.1);
        height: 100%;
    }}
    .kpi-label {{ font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--rg-text-soft); margin-bottom: 6px; }}
    .kpi-value {{ font-family: 'JetBrains Mono', monospace; font-size: clamp(20px, 2.5vw, 28px); font-weight: 700; line-height: 1.1; color: var(--rg-text); }}
    .kpi-sub {{ font-size: 11.5px; margin-top: 6px; font-weight: 500; color: var(--rg-text-soft); }}
    </style>
""", unsafe_allow_html=True)

# =========================================================
# 8. CLIENTES API Y LIGAS DISPONIBLES Y VALIDADAS
# =========================================================
def hl_headers(): return {"x-rapidapi-key": HL_API_KEY}

@st.cache_data(ttl=86400)
def hl_buscar_ligas(country_name):
    if not country_name or not HL_API_KEY: return []
    url = f"{HL_BASE_URL}/leagues"
    try:
        r = requests.get(url, headers=hl_headers(), params={"countryName": country_name, "limit": 100}, timeout=10)
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception:
        return []

HL_LEAGUE_IDS = {
    "🇨🇴 Primera A (Colombia)": 204173,
    "🇪🇨 LigaPro (Ecuador)": 206726,
    "🇺🇾 Primera División Uruguay": 228852,
    "🇵🇪 Liga 1 (Perú)": 239915,
    "🇺🇸 MLS (EE.UU.)": 253,
}

def af_headers(): return {"x-apisports-key": AF_API_KEY}

def _actualizar_creditos_af(response_headers):
    restante = response_headers.get('x-ratelimit-requests-remaining')
    limite = response_headers.get('x-ratelimit-requests-limit')
    if restante is not None:
        st.session_state.creditos_restantes_af = f"{restante}/{limite}" if limite else restante

@st.cache_data(ttl=86400)
def af_buscar_ligas(country_name):
    if not country_name or not AF_API_KEY: return []
    url = f"{AF_BASE_URL}/leagues"
    try:
        r = requests.get(url, headers=af_headers(), params={"country": country_name}, timeout=10)
        _actualizar_creditos_af(r.headers)
        return r.json().get("response", []) if r.status_code == 200 else []
    except Exception:
        return []

AF_LEAGUE_IDS = {
    "🇦🇷 Liga Profesional (Argentina)": 128,
    "🇧🇷 Brasileirão Serie A (Brasil)": 71,
    "🇨🇱 Primera División (Chile)": 265,
    "🇨🇴 Primera A (Colombia)": 239,
    "🇪🇨 LigaPro (Ecuador)": 242,
    "🇲🇽 Liga MX (México)": 262,
    "🇵🇾 División Profesional (Paraguay)": 252,
    "🇵🇪 Liga 1 (Perú)": 281,
    "🇺🇾 Primera División (Uruguay)": 268,
    "🇺🇸 MLS (Estados Unidos)": 253,
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League (Inglaterra)": 39,
    "🇪🇸 LaLiga (España)": 140,
    "🇮🇹 Serie A (Italia)": 135,
    "🇩🇪 Bundesliga (Alemania)": 78,
    "🇫🇷 Ligue 1 (Francia)": 61,
    "🇵🇹 Primeira Liga (Portugal)": 94,
    "🇳🇱 Eredivisie (Países Bajos)": 88,
    "🏆 Champions League": 2,
    "🏆 Europa League": 3,
    "🏆 Copa Libertadores": 13,
    "🏆 Copa Sudamericana": 11
}

# Ligas comprobadas y activas soportadas en el plan de The Odds API
todas_las_ligas = {
    "🇺🇸 USA MLS": ["soccer_usa_mls"],
    "🇲🇽 Mexico Liga MX": ["soccer_mexico_liga_mx"],
    "🇦🇷 Argentina Primera Division": ["soccer_argentina_primera_division"],
    "🇧🇷 Brasil Brasileirao": ["soccer_brazil_campeonato"],
    "🏆 EU Champions League": ["soccer_uefa_champions_league"],
    "🏆 EU Europa League": ["soccer_uefa_europa_league"],
    "🏆 EU Conference League": ["soccer_uefa_europa_conference_league"],
    "🏆 Copa Libertadores": ["soccer_conmebol_copa_libertadores"],
    "🏆 Copa Sudamericana": ["soccer_conmebol_copa_sudamericana"],
    "🇪🇸 Spain La Liga": ["soccer_spain_la_liga"],
    "🇪🇸 Spain La Liga 2": ["soccer_spain_segunda_division"],
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿 England Premier League": ["soccer_epl"],
    "🏴󠁧󠁢󠁥󠁮󠁧󠁿 England Championship": ["soccer_efl_champ"],
    "🇮🇹 Italy Serie A": ["soccer_italy_serie_a"],
    "🇮🇹 Italy Serie B": ["soccer_italy_serie_b"],
    "🇩🇪 Germany Bundesliga": ["soccer_germany_bundesliga"],
    "🇩🇪 Germany Bundesliga 2": ["soccer_germany_bundesliga2"],
    "🇫🇷 France Ligue 1": ["soccer_france_ligue_one"],
    "🇫🇷 France Ligue 2": ["soccer_france_ligue_two"],
    "🇵🇹 Portugal Primeira Liga": ["soccer_portugal_primeira_liga"],
    "🇳🇱 Netherlands Eredivisie": ["soccer_netherlands_eredivisie"],
    "🇹🇷 Turkey Super Lig": ["soccer_turkey_super_league"],
    "🇦🇺 Australia A-League": ["soccer_australia_aleague"]
}

diccionario_mercados = {
    "1X2 (Ganador)": "h2h",
    "Doble Oportunidad": "double_chance",
    "Ambos Anotan (BTTS)": "btts",
    "Goles Más/Menos 2.5": "totals"
}

# =========================================================
# 9. SIDEBAR ORGANIZADO EN ACCORDEONES
# =========================================================
with st.sidebar:
    st.header("⚙️ Control Global")
    
    def _cambiar_tema():
        ConfigManager.guardar('tema_visual', st.session_state.tema_visual_widget)
        st.session_state.tema_visual = st.session_state.tema_visual_widget

    st.radio(
        "🎨 Tema Visual:",
        options=["Oscuro", "Claro"],
        index=0 if st.session_state.tema_visual == "Oscuro" else 1,
        key="tema_visual_widget",
        on_change=_cambiar_tema,
        horizontal=True
    )

    auto_ref = st.checkbox("⚡ Monitoreo en Vivo", value=False)
    if auto_ref:
        intervalo_sec = st.selectbox("Recarga cada:", [30, 60, 120], index=1)
        st.markdown(f"<meta http-equiv='refresh' content='{intervalo_sec}'>", unsafe_allow_html=True)

    st.session_state.notif_push_activas = st.checkbox("🔔 Push Web & Alertas Sonoras", value=st.session_state.notif_push_activas, help="Emite alertas emergentes en pantalla e indicadores sonoros al hallar SureBets/ValueBets.")

    st.markdown(f"""
        <div class="creditos-caja-pro">
            <div>
                <small style="color:var(--rg-text-soft); text-transform:uppercase; font-weight:700;">Odds API</small><br>
                <span style="font-size:16px; font-weight:bold; color:var(--rg-accent);">🔑 {st.session_state.creditos_restantes}</span>
            </div>
            <div>🟢</div>
        </div>
        <div class="creditos-caja-pro">
            <div>
                <small style="color:var(--rg-text-soft); text-transform:uppercase; font-weight:700;">Football API</small><br>
                <span style="font-size:16px; font-weight:bold; color:#feca57;">🔑 {st.session_state.creditos_restantes_af}</span>
            </div>
            <div>🟡</div>
        </div>
    """, unsafe_allow_html=True)

    with st.expander("🐞 Diagnóstico de APIs", expanded=False):
        st.session_state.debug_mode = st.checkbox("Modo diagnóstico detallado", value=st.session_state.debug_mode, help="Muestra en pantalla todos los errores HTTP crudos de cada consulta a las APIs.")

        if not API_KEY:
            st.error("❌ ODDS_API_KEY no está configurada en st.secrets.")
        else:
            st.success(f"✅ ODDS_API_KEY cargada (termina en ...{API_KEY[-4:]})")

        if not AF_API_KEY:
            st.warning("⚠️ AF_API_KEY (API-Football) no está configurada.")
        else:
            st.success(f"✅ AF_API_KEY cargada (termina en ...{AF_API_KEY[-4:]})")

        if st.button("🔍 Ver ligas de soccer soportadas por Odds API", use_container_width=True):
            if not API_KEY:
                st.error("No se puede consultar: falta ODDS_API_KEY.")
            else:
                try:
                    r = requests.get(f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}", timeout=10)
                    if r.status_code == 200:
                        todos_sports = r.json()
                        soccer_keys = sorted([s['key'] for s in todos_sports if s.get('group') == 'Soccer'])
                        st.write(f"**{len(soccer_keys)} ligas de soccer disponibles con tu clave:**")
                        st.code("\n".join(soccer_keys))

                        claves_usadas = set()
                        for v in todas_las_ligas.values():
                            claves_usadas.update(v)
                        no_soportadas = sorted([k for k in claves_usadas if k not in soccer_keys])
                        if no_soportadas:
                            st.error("❌ Estas ligas de tu selector NO existen en tu plan/API y por eso siempre devuelven 0 partidos:")
                            st.code("\n".join(no_soportadas))
                        else:
                            st.success("✅ Todas las claves de tu diccionario 'todas_las_ligas' existen en la API.")
                    else:
                        st.error(f"HTTP {r.status_code}: {r.text[:300]}")
                except Exception as e:
                    st.error(f"Excepción al consultar: {e}")

    with st.expander("⚽ Selección de Torneos", expanded=True):
        if 'ligas_sels_widget' not in st.session_state:
            st.session_state.ligas_sels_widget = []

        col_sel_todas, col_sel_limpiar = st.columns(2)
        with col_sel_todas:
            if st.button("✅ Todas", use_container_width=True):
                st.session_state.ligas_sels_widget = list(todas_las_ligas.keys())
                st.toast("Todas las ligas seleccionadas", icon="✅")
                st.rerun()
        with col_sel_limpiar:
            if st.button("🧹 Ninguna", use_container_width=True):
                st.session_state.ligas_sels_widget = []
                st.toast("Selección de ligas limpia", icon="🧹")
                st.rerun()

        ligas_sels = st.multiselect("Ligas principales:", list(todas_las_ligas.keys()), key="ligas_sels_widget")
        
        habilitar_af = st.checkbox("Habilitar Ligas LATAM (API-Football)", value=True)
        ligas_af_sels = st.multiselect("Ligas extra:", list(AF_LEAGUE_IDS.keys()), default=[]) if habilitar_af else []

    with st.expander("🏬 Casas y Mercados", expanded=True):
        def _on_change_bookies():
            ConfigManager.guardar('casas_preferidas', st.session_state.casas_preferidas_widget)
            st.toast("Preferencias de casas actualizadas", icon="🏬")

        casas_preferidas = st.multiselect(
            "Mis Bookies:",
            ["Betano", "Bet365", "Ecuabet", "1xBet", "Pinnacle", "Bwin", "Unibet", "William Hill"],
            default=st.session_state.casas_preferidas,
            key="casas_preferidas_widget",
            on_change=_on_change_bookies
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
        def _on_change_bankroll():
            ConfigManager.guardar('bankroll_total', st.session_state.bankroll_widget)
            st.toast("Bankroll guardado", icon="💰")

        bankroll_total = st.number_input(
            "Banca Total ($):", 
            min_value=10.0, 
            value=float(st.session_state.bankroll_total), 
            step=10.0,
            key="bankroll_widget",
            on_change=_on_change_bankroll
        )
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
        st.success("🤖 Bot de Notificaciones Activo (Modo Global Protegido)")
        st.session_state['auto_alertas_telegram'] = st.checkbox("🚀 Auto-alertas (+EV > 5%)", value=False)

# =========================================================
# 10. PROCESAMIENTO DE CUOTAS
# =========================================================
def actualizar_creditos(headers):
    if 'x-requests-remaining' in headers:
        st.session_state.creditos_restantes = headers['x-requests-remaining']

@st.cache_data(ttl=60)
def consultar_api_odds(sport_key, market_key):
    if not API_KEY:
        return [], "Falta ODDS_API_KEY en st.secrets"
    if not sport_key:
        return [], "sport_key vacío"

    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={API_KEY}&regions=eu,us&markets={market_key}&oddsFormat=decimal"
    try:
        response = requests.get(url, timeout=10)
        actualizar_creditos(response.headers)
        if response.status_code == 200:
            res_json = response.json()
            if isinstance(res_json, list):
                return res_json, None
            else:
                return [], f"[{sport_key}/{market_key}] Respuesta inesperada: {res_json}"
        else:
            return [], f"[{sport_key}/{market_key}] HTTP {response.status_code}: {response.text[:250]}"
    except Exception as e:
        return [], f"[{sport_key}/{market_key}] EXCEPCIÓN: {e}"

def consultar_api_odds_con_fallback(sport_keys_list, market_key):
    errores_locales = []
    for key in sport_keys_list:
        data, err = consultar_api_odds(key, market_key)
        if data and isinstance(data, list) and len(data) > 0:
            return data
        if err:
            errores_locales.append(err)
    if errores_locales:
        st.session_state.debug_api_errors.extend(errores_locales)
    return []

@st.cache_data(ttl=60)
def consultar_api_odds_evento(sport_key, event_id, market_key):
    if not sport_key or not event_id or not API_KEY: return None
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

        horas = (fecha_utc - ahora_utc).total_seconds() / 3600.0
        
        es_en_vivo = partido.get('in_play', False) or (horas <= 0 and horas >= -2.2)
        marcador_local = partido.get('scores', {}).get('home', 0) if es_en_vivo else None
        marcador_visita = partido.get('scores', {}).get('away', 0) if es_en_vivo else None
        
        minuto_estimado = 30
        if es_en_vivo and horas < 0:
            minutos_transcurridos = int(abs(horas) * 60)
            if minutos_transcurridos <= 45:
                minuto_estimado = max(1, minutos_transcurridos)
            elif minutos_transcurridos <= 60:
                minuto_estimado = 45 
            else:
                minuto_estimado = min(89, minutos_transcurridos - 15)
        
        min_raw = partido.get('minute', minuto_estimado) if es_en_vivo else None
        try:
            minuto_num = int(str(min_raw).replace("'", "").replace("+", ""))
        except Exception:
            minuto_num = minuto_estimado

        if limite_horas < 900000 and not es_en_vivo and (horas < -48.0 or horas > (limite_horas + 48)): 
            continue

        fecha_local = fecha_utc - timedelta(hours=5)
        bookmakers = partido.get('bookmakers', [])
        if not bookmakers: continue

        if casas_preferidas:
            bookies_filtrados = [b for b in bookmakers if any(cp.lower() in b['title'].lower() for cp in casas_preferidas)]
            if bookies_filtrados:
                bookmakers = bookies_filtrados

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

        override_data = st.session_state.overrides_live.get(partido_id, {})
        minuto_final_calc = override_data.get('minuto', minuto_num)
        goles_loc_final = override_data.get('goles_loc', marcador_local or 0)
        goles_vis_final = override_data.get('goles_vis', marcador_visita or 0)

        if es_en_vivo:
            probs_poisson = calcular_poisson_live(minuto_final_calc, goles_loc_final, goles_vis_final)
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

            if ev > 0.10:
                disparar_alerta_sonora_y_notificacion(
                    f"🔥 OPORTUNIDAD ALTO VALOR (+{round(ev*100,1)}%)",
                    f"{home} vs {away} - {opcion} @ {cuota_max} en {bookie_max}"
                )

            c_prev = datos_previos.get(partido_id, {}).get("mercados", {}).get(mercado, {}).get("max_cuotas", {}).get(opcion, cuota_max)
            if cuota_max < c_prev: variaciones_dict[opcion] = "📉 Bajando"
            elif cuota_max > c_prev: variaciones_dict[opcion] = "📈 Subiendo"
            else: variaciones_dict[opcion] = "➡️ Estable"

            clave_hist = f"{partido_id}_{mercado}_{opcion}"
            if clave_hist not in st.session_state.historico_cuotas_live:
                st.session_state.historico_cuotas_live[clave_hist] = []
            st.session_state.historico_cuotas_live[clave_hist].append(cuota_max)
            if len(st.session_state.historico_cuotas_live[clave_hist]) > 10:
                st.session_state.historico_cuotas_live[clave_hist].pop(0)

        info_surebet = detectar_surebet(max_cuotas)

        if info_surebet.get("es_surebet"):
            disparar_alerta_sonora_y_notificacion(
                f"💰 SUREBET ENCONTRADA (+{round(info_surebet['lucro'], 2)}%)",
                f"{home} vs {away} en {mercado}"
            )

        if max_cuotas:
            if partido_id not in diccionario_consolidador:
                diccionario_consolidador[partido_id] = {
                    "id": partido_id, "liga_origen": nombre_liga,
                    "fecha_str": fecha_local.strftime("%d/%m/%Y - %H:%M"),
                    "fecha_ts": fecha_utc.timestamp(),
                    "local": home, "visitante": away,
                    "es_en_vivo": es_en_vivo,
                    "marcador_local": goles_loc_final,
                    "marcador_visita": goles_vis_final,
                    "minuto_en_vivo": f"{minuto_final_calc}'",
                    "minuto_num": minuto_final_calc,
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
            "📈 BITÁCORA & ROI",
            "💬 ATENCIÓN Y MEJORAS"
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

    if st.session_state.ultimo_escaneo_ts is not None:
        segundos_transcurridos = int(time.time() - st.session_state.ultimo_escaneo_ts)
        hora_escaneo = datetime.fromtimestamp(st.session_state.ultimo_escaneo_ts).strftime("%H:%M:%S")
        
        if segundos_transcurridos > 120:
            st.error(f"🚨 **ATENCIÓN: CUOTAS DESACTUALIZADAS (STALE DATA)**. Último escaneo a las **{hora_escaneo}** (hace {segundos_transcurridos}s). Las cuotas en vivo cambian rápido; vuelve a escanear.")
        else:
            st.info(f"⏱️ **Cuotas actualizadas a las {hora_escaneo}** (hace {segundos_transcurridos} segundos).")

    if consultar:
        if (len(ligas_sels) > 0 or len(ligas_af_sels) > 0) and len(mercados_sels) > 0:
            st.cache_data.clear()
            consolidador = {}
            st.session_state.ha_consultado = True
            st.session_state.debug_api_errors = []

            total_ligas = len(ligas_sels) + len(ligas_af_sels)

            with st.status(f"🔄 Iniciando escaneo de mercado ({total_ligas} ligas)...", expanded=True) as status_consulta:
                for idx_liga, liga in enumerate(ligas_sels, start=1):
                    sport_keys_list = todas_las_ligas.get(liga, [])
                    status_consulta.update(label=f"📡 Conectando API Odds ({idx_liga}/{total_ligas}): {liga}...")
                    
                    if sport_keys_list:
                        raw_h2h = consultar_api_odds_con_fallback(sport_keys_list, market_key="h2h")
                        
                        if "1X2 (Ganador)" in mercados_sels:
                            status_consulta.update(label=f"📊 Procesando 1X2 & Poisson Dixon-Coles: {liga}")
                            procesar_e_inyectar_mercado(raw_h2h, "1X2 (Ganador)", limite_h, liga, consolidador)

                        if "Doble Oportunidad" in mercados_sels:
                            status_consulta.update(label=f"⚖️ Calculando Doble Oportunidad sintética: {liga}")
                            raw_dc = consultar_api_odds_con_fallback(sport_keys_list, market_key="double_chance")
                            if not raw_dc or len(raw_dc) == 0:
                                raw_dc = calcular_doble_oportunidad_sintetica(raw_h2h)
                            procesar_e_inyectar_mercado(raw_dc, "Doble Oportunidad", limite_h, liga, consolidador)

                        if "Goles Más/Menos 2.5" in mercados_sels:
                            status_consulta.update(label=f"⚽ Analizando Totales de Goles: {liga}")
                            raw_totals = consultar_api_odds_con_fallback(sport_keys_list, market_key="totals")
                            procesar_e_inyectar_mercado(raw_totals, "Goles Más/Menos 2.5", limite_h, liga, consolidador)

                        if "Ambos Anotan (BTTS)" in mercados_sels:
                            status_consulta.update(label=f"🎯 Escaneando Mercado BTTS: {liga}")
                            raw_btts = consultar_api_odds_con_fallback(sport_keys_list, market_key="btts")
                            if raw_btts:
                                procesar_e_inyectar_mercado(raw_btts, "Ambos Anotan (BTTS)", limite_h, liga, consolidador)

                for idx_af, liga_af in enumerate(ligas_af_sels, start=len(ligas_sels) + 1):
                    af_id = AF_LEAGUE_IDS.get(liga_af)
                    status_consulta.update(label=f"📡 Conectando API Football ({idx_af}/{total_ligas}): {liga_af}...")
                    if af_id and AF_API_KEY:
                        try:
                            r = requests.get(f"{AF_BASE_URL}/odds", headers=af_headers(), params={"league": af_id, "season": datetime.now().year}, timeout=10)
                            _actualizar_creditos_af(r.headers)
                            if r.status_code == 200:
                                raw_af_odds = r.json().get("response", [])
                                if not raw_af_odds:
                                    st.session_state.debug_api_errors.append(f"[API-Football] {liga_af}: respuesta vacía (revisa temporada/season o si la liga tiene cuotas cargadas).")
                                datos_normalizados = []
                                for item in raw_af_odds:
                                    fix = item.get("fixture", {})
                                    teams = item.get("teams", {})
                                    bookies_af = item.get("bookmakers", [])
                                    
                                    bookmakers_converted = []
                                    for b in bookies_af:
                                        b_markets = []
                                        for m in b.get("bets", []):
                                            m_name = m.get("name")
                                            key_m = "h2h" if m_name == "Match Winner" else ("totals" if m_name == "Goals Over/Under" else ("btts" if m_name == "Both Teams Score" else "double_chance"))
                                            outcomes = [{"name": o.get("value"), "price": float(o.get("odd"))} for o in m.get("values", [])]
                                            b_markets.append({"key": key_m, "outcomes": outcomes})
                                        bookmakers_converted.append({"key": str(b.get("id")), "title": b.get("name"), "markets": b_markets})

                                    datos_normalizados.append({
                                        "id": f"af_{fix.get('id')}",
                                        "home_team": teams.get("home", {}).get("name", "Local"),
                                        "away_team": teams.get("away", {}).get("name", "Visitante"),
                                        "commence_time": fix.get("date", datetime.now(timezone.utc).isoformat()),
                                        "bookmakers": bookmakers_converted
                                    })
                                for m_sel in mercados_sels:
                                    procesar_e_inyectar_mercado(datos_normalizados, m_sel, limite_h, liga_af, consolidador)
                            else:
                                st.session_state.debug_api_errors.append(f"[API-Football] {liga_af}: HTTP {r.status_code}: {r.text[:250]}")
                        except Exception as e:
                            st.session_state.debug_api_errors.append(f"[API-Football] {liga_af}: EXCEPCIÓN {e}")

                status_consulta.update(label=f"💰 Buscando SureBets de Arbitraje y brechas +EV...", state="running")
                time.sleep(0.3)
                status_consulta.update(label=f"✅ Consulta completa: {len(consolidador)} partidos procesados exitosamente.", state="complete")

            st.session_state.datos_cargados_previos = st.session_state.datos_cargados
            st.session_state.datos_cargados = consolidador
            st.session_state.claves_auto = set()
            st.session_state.version_ticket += 1
            st.session_state.ultimo_escaneo_ts = time.time()
            st.toast(f"✅ Escaneo finalizado. {len(consolidador)} partidos cargados.", icon="🎯")

            if st.session_state.get('auto_alertas_telegram', False):
                alertas_ev = []
                for p in consolidador.values():
                    for m_n, m_v in p['mercados'].items():
                        for op, val in m_v['value_bets'].items():
                            if val['ev'] > 0.05:
                                alertas_ev.append(f"🔥 *VALUEBET +EV ({round(val['ev']*100, 1)}%)*\n⚽ {p['local']} vs {p['visitante']}\n🎯 {m_n}: *{op}* (x{m_v['max_cuotas'][op]})\n🏢 {m_v['max_bookies'][op]}")
                if alertas_ev:
                    enviar_telegram("🚨 *OPORTUNIDADES DE VALOR ENCONTRADAS* 🚨\n\n" + "\n\n".join(alertas_ev[:5]))

    if st.session_state.get('debug_api_errors'):
        with st.expander(f"🐞 Diagnóstico: {len(st.session_state.debug_api_errors)} problema(s) detectado(s) durante el último escaneo", expanded=True):
            st.caption("Esto explica por qué faltan partidos o por qué salieron 0. Revisa cada línea: claves de liga inexistentes en tu plan, cuota agotada, o clave inválida.")
            errores_unicos = list(dict.fromkeys(st.session_state.debug_api_errors))
            for err in errores_unicos[:40]:
                st.code(err)

    dict_partidos = st.session_state.datos_cargados
    dict_previos = st.session_state.datos_cargados_previos

    if dict_partidos:
        texto_ai = generar_resumen_ejecutivo_ai(dict_partidos)
        if texto_ai:
            st.markdown(f"<div class='ai-summary-box'>{texto_ai}</div>", unsafe_allow_html=True)

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
                    elif "Cazador de Valor" in perfil_estrategia: cumple_perfil = ev_op > 0.02
                    elif "Doble Oportunidad" in perfil_estrategia: cumple_perfil = nombre_m == "Doble Oportunidad"

                    if cumple_perfil and (rango_cuota_auto[0] <= cuota_op <= rango_cuota_auto[1]) and (prob_op >= prob_min_auto):
                        clave_b = f"ap_{part['id']}_{nombre_m}_{opcion}"
                        opciones_todas.append({
                            "partido_id": part['id'], 
                            "clave": clave_b,
                            "partido_obj": part,
                            "mercado_nombre": nombre_m,
                            "opcion_nombre": opcion,
                            "cuota_val": cuota_op,
                            "casa_val": m_info['max_bookies'][opcion],
                            "prob_real": prob_op,
                            "ev": ev_op
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
            for item_sel in mejores_opciones:
                st.session_state.ticket_persistente[item_sel['clave']] = {
                    "evento": f"{item_sel['partido_obj']['local']} vs {item_sel['partido_obj']['visitante']}",
                    "liga": item_sel['partido_obj']['liga_origen'],
                    "mercado": item_sel['mercado_nombre'],
                    "seleccion": item_sel['opcion_nombre'],
                    "cuota": item_sel['cuota_val'],
                    "casa": item_sel['casa_val'],
                    "prob_real": item_sel['prob_real'],
                    "fecha_ts": item_sel['partido_obj'].get('fecha_ts', None)
                }
            st.session_state.version_ticket += 1
            st.toast(f"🎯 Marcados automáticamente {len(mejores_opciones)} eventos.", icon="🎲")

    if not st.session_state.ha_consultado:
        st.markdown("""
            <div class="empty-state-card">
                <h3 style="color:var(--rg-accent); margin-bottom:8px; font-weight:800;">⚡ Sistema Listo para el Análisis</h3>
                <p style="color:var(--rg-text-soft); margin-bottom:22px; font-size:13.5px;">Sigue estos pasos en el panel lateral para iniciar la búsqueda de cuotas y ValueBets:</p>
                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap:16px;">
                    <div class="empty-state-step">
                        <div class="step-badge">1</div><br>
                        <strong style="color:var(--rg-text); font-size:14px;">Selecciona Torneos</strong>
                        <p style="font-size:12px; color:var(--rg-text-soft); margin-top:6px; margin-bottom:0;">Añade Champions, Europa League o Ligas Domésticas.</p>
                    </div>
                    <div class="empty-state-step">
                        <div class="step-badge">2</div><br>
                        <strong style="color:var(--rg-text); font-size:14px;">Casas y Mercados</strong>
                        <p style="font-size:12px; color:var(--rg-text-soft); margin-top:6px; margin-bottom:0;">Selecciona la ventana de tiempo o activa 'Traer todo'.</p>
                    </div>
                    <div class="empty-state-step">
                        <div class="step-badge">3</div><br>
                        <strong style="color:var(--rg-text); font-size:14px;">Escanea y Ejecuta</strong>
                        <p style="font-size:12px; color:var(--rg-text-soft); margin-top:6px; margin-bottom:0;">Haz clic en 'Escanear Mercado Now' para analizar cuotas.</p>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    elif not dict_partidos:
        st.info("ℹ️ **No se encontraron partidos en el rango seleccionado.** Si estás buscando torneos europeos fuera de jornada inmediata, activa la casilla **'🌐 Traer todo sin filtro de días'** o amplia la ventana de tiempo. Si el problema persiste, abre el panel **'🐞 Diagnóstico de APIs'** en la barra lateral para ver la causa exacta.")
    else:
        with st.expander("🎛️ Filtros Avanzados de Cuotas y Estado", expanded=True):
            col_busq, col_rango_cuota, col_chk1, col_chk2 = st.columns([2, 2, 1.2, 1.2])
            with col_busq:
                busqueda_equipo = st.text_input("🔍 Buscador por equipo:", "").strip().lower()
            with col_rango_cuota:
                filtro_rango_c = st.slider("Filtro de Cuotas Máximas:", 1.05, 10.0, (1.10, 5.00), step=0.05)
            with col_chk1:
                solo_live = st.checkbox("🔴 Solo En Vivo", value=False)
            with col_chk2:
                solo_ev = st.checkbox("🔥 Solo +EV", value=False)

        dict_partidos_filtrados = {}
        for p_id, p in dict_partidos.items():
            coincide_nombre = (busqueda_equipo in p['local'].lower() or busqueda_equipo in p['visitante'].lower())
            if not coincide_nombre: continue
            if solo_live and not p.get('es_en_vivo', False): continue
            
            mercados_filtrados = {}
            for m_nombre, m_info in p['mercados'].items():
                cuotas_ok = {}
                for op, c_val in m_info['max_cuotas'].items():
                    if filtro_rango_c[0] <= c_val <= filtro_rango_c[1]:
                        if solo_ev and not m_info['value_bets'].get(op, {}).get('es_value', False):
                            continue
                        cuotas_ok[op] = c_val
                if cuotas_ok:
                    mercados_filtrados[m_nombre] = m_info

            if mercados_filtrados:
                p_copy = dict(p)
                p_copy['mercados'] = mercados_filtrados
                dict_partidos_filtrados[p_id] = p_copy

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
                            override_data = st.session_state.overrides_live.get(part['id'], {})
                            with st.container(border=True, key=f"match_{part['id']}"):
                                col_enc_1, col_enc_2 = st.columns([4, 1.5])
                                with col_enc_1:
                                    st.markdown(f"<span class='liga-chip'>🏆 {part['liga_origen']}</span>", unsafe_allow_html=True)
                                    if part.get('es_en_vivo'):
                                        m_loc = override_data.get('goles_loc', part.get('marcador_local', 0))
                                        m_vis = override_data.get('goles_vis', part.get('marcador_visita', 0))
                                        min_v = f"{override_data.get('minuto', part.get('minuto_num', 30))}'"
                                        st.markdown(f"<div class='match-header'>⚽ {part['local']} <span style='color:var(--rg-accent); font-weight:800;'>{m_loc} - {m_vis}</span> {part['visitante']}</div>", unsafe_allow_html=True)
                                        st.markdown(f"<span style='background:#e74c3c; color:white; padding:2px 8px; border-radius:12px; font-weight:bold; font-size:11px;'>🔴 EN VIVO</span> <span class='kickoff-chip'>⏱️ {min_v}</span>", unsafe_allow_html=True)
                                    else:
                                        st.markdown(f"<div class='match-header'>⚽ {part['local']} vs {part['visitante']}</div>", unsafe_allow_html=True)
                                        st.markdown(f"<span class='kickoff-chip'>📅 {part['fecha_str']}</span>", unsafe_allow_html=True)

                                if part.get('es_en_vivo'):
                                    with st.expander("⏱️ Ajustar Minuto y Marcador en Vivo (Live Override)", expanded=False):
                                        c_ov1, c_ov2, c_ov3 = st.columns([2, 1, 1])
                                        min_actual = override_data.get('minuto', part.get('minuto_num', 30))
                                        g_loc_act = override_data.get('goles_loc', part.get('marcador_local', 0))
                                        g_vis_act = override_data.get('goles_vis', part.get('marcador_visita', 0))
                                        
                                        def actualizar_live_state(p_id=part['id']):
                                            st.session_state.overrides_live[p_id] = {
                                                'minuto': st.session_state[f"sl_min_{p_id}"],
                                                'goles_loc': st.session_state[f"gl_{p_id}"],
                                                'goles_vis': st.session_state[f"gv_{p_id}"]
                                            }

                                        nuevo_minuto = c_ov1.slider("Minuto real del partido:", min_value=1, max_value=90, value=min_actual, key=f"sl_min_{part['id']}", on_change=actualizar_live_state)
                                        goles_l = c_ov2.number_input(f"Goles {part['local']}:", min_value=0, value=int(g_loc_act), key=f"gl_{part['id']}", on_change=actualizar_live_state)
                                        goles_v = c_ov3.number_input(f"Goles {part['visitante']}:", min_value=0, value=int(g_vis_act), key=f"gv_{part['id']}", on_change=actualizar_live_state)
                                        
                                        if st.button("🔄 Recalcular Modelo Poisson Live", key=f"btn_recalc_{part['id']}", type="primary"):
                                            actualizar_live_state(part['id'])
                                            part['marcador_local'] = goles_l
                                            part['marcador_visita'] = goles_v
                                            part['minuto_num'] = nuevo_minuto
                                            part['minuto_en_vivo'] = f"{nuevo_minuto}'"

                                            nuevas_probs = calcular_poisson_live(nuevo_minuto, goles_l, goles_v)
                                            for m_nombre, m_data in part['mercados'].items():
                                                for op_key, val_dict in m_data['value_bets'].items():
                                                    if op_key in nuevas_probs:
                                                        val_dict['prob_poisson'] = nuevas_probs[op_key]

                                            st.toast("⚡ Poisson Live recalculado exitosamente", icon="🎯")
                                            st.rerun()

                                with st.expander("⚡ Cobertura en Vivo / Hedging Automático", expanded=False):
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
                                                st.toast("⚡ ¡SureBet registrada en Bitácora!", icon="🚀")

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
                                                val_defecto = clave_base in st.session_state.ticket_persistente or clave_base in st.session_state.claves_auto

                                                chk = st.checkbox(
                                                    f"{opcion} ({cuota_m}) {lbl_val}", 
                                                    value=val_defecto, 
                                                    key=clave_base,
                                                    on_change=toggle_apuesta,
                                                    args=(part, text_m, opcion, cuota_m, m_info['max_bookies'][opcion], val['prob_real'], clave_base)
                                                )
                                                
                                                lbl_modelo = f"⏱️ Poisson Live ({part.get('minuto_en_vivo', '30\'')})" if part.get('es_en_vivo') else "📊 Dixon-Coles"
                                                st.markdown(f"<small>🏠 {m_info['max_bookies'][opcion]}<br>🎯 Implícita: {round(val['prob_real'],1)}%<br>{lbl_modelo}: {round(val['prob_poisson'],1)}% | {var_txt}</small>", unsafe_allow_html=True)

                                                with st.expander("🏬 Comparar Casas"):
                                                    todas_casas = m_info.get('todas_cuotas', {}).get(opcion, [])
                                                    if todas_casas:
                                                        df_casas = pd.DataFrame(todas_casas, columns=["Cuota", "Casa de Apuestas"]).sort_values("Cuota", ascending=False)
                                                        st.dataframe(df_casas, use_container_width=True, hide_index=True)
                                                    
                                                clave_hist = f"{part['id']}_{text_m}_{opcion}"
                                                hist_pts = st.session_state.historico_cuotas_live.get(clave_hist, [])
                                                if len(hist_pts) > 1:
                                                    st.caption("📈 **Tendencia de Cuota Live:**")
                                                    st.line_chart(hist_pts, height=100)

        with col_der:
            st.subheader("🎟️ Configuración de Parlay")
            
            apuestas_seleccionadas = list(st.session_state.ticket_persistente.values())

            col_b1, col_b2 = st.columns([2, 1])
            with col_b1:
                confirmar_limpieza = st.checkbox("⚠️ Confirmar limpiar boleto", value=False, key="chk_conf_limpiar")
            with col_b2:
                if st.button("🧹 Limpiar", use_container_width=True, disabled=not bool(confirmar_limpieza)):
                    st.session_state.ticket_persistente.clear()
                    st.session_state.claves_auto.clear()
                    st.toast("Boleto limpiado con éxito", icon="🧹")
                    st.rerun()

            if apuestas_seleccionadas:
                alertas_correlacion = detectar_correlaciones(apuestas_seleccionadas)
                for al in alertas_correlacion:
                    st.error(al)

                alertas_horario = detectar_conflictos_horarios(apuestas_seleccionadas)
                for al_h in alertas_horario:
                    st.warning(al_h)

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

                    with st.expander("📱 Transferencia Móvil mediante Código QR"):
                        datos_qr_encoded = urllib.parse.quote(texto_whatsapp)
                        url_qr = f"https://api.qrserver.com/v1/create-qr-code/?size=180x180&data={datos_qr_encoded}"
                        st.markdown(f"<div style='text-align:center;'><img src='{url_qr}' width='160' style='border-radius:10px; border:2px solid #00d2d3;'><br><small style='color:var(--rg-text-soft);'>Escanea para transferir boleto al móvil</small></div>", unsafe_allow_html=True)

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
                        st.toast("¡Guardado localmente en la Bitácora!", icon="💾")
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
                        "📄 Descargar Boleto HTML",
                        data=html_ticket,
                        file_name=f"Boleto_Parlay_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                        mime="text/html",
                        use_container_width=True
                    )

                    msg_encoded = urllib.parse.quote(texto_whatsapp)
                    st.markdown(f'<a href="https://api.whatsapp.com/send?text={msg_encoded}" target="_blank" style="text-decoration:none;"><button style="border:none; background-color:#25D366; color:white; padding:10px; border-radius:8px; font-weight:bold; width:100%; cursor:pointer;">📲 Compartir en WhatsApp</button></a>', unsafe_allow_html=True)

                    if st.button("📤 Enviar a Telegram", use_container_width=True, key="telegram_btn"):
                        if enviar_telegram(texto_whatsapp):
                            st.toast("¡Boleto enviado a Telegram!", icon="📤")
            else:
                st.info("🎟️ Marca casillas en el radar para construir tu boleto persistente.")

    if st.session_state.ticket_persistente:
        c_total_flotante = np.prod([float(ap['cuota']) for ap in st.session_state.ticket_persistente.values()])
        with st.expander(f"📱 🎟️ VER BOLETO FLOTANTE ({len(st.session_state.ticket_persistente)} Selecciones | x{round(c_total_flotante, 2)})", expanded=False):
            st.write("**Eventos en Boleto:**")
            for ap in st.session_state.ticket_persistente.values():
                st.write(f"• **{ap['evento']}**: `{ap['seleccion']}` @ x{ap['cuota']}")

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
                            
                            palabras_ignorar = [
                                "doble", "triple", "cuadruple", "cuádruple", "parlay", 
                                "combinada", "sistema", "apuesta", "boleta", "total", 
                                "cuota total", "ganancia", "importe", "resultado del partido"
                            ]

                            for linea in lineas:
                                linea_clean = linea.strip()
                                if not linea_clean:
                                    continue
                                
                                linea_lower = linea_clean.lower()
                                if any(re.search(rf'\b{p}\b', linea_lower) for p in palabras_ignorar):
                                    continue

                                linea_norm = re.sub(r'([^\w\d]|^)[xX@]\s*(?=\d)', r'\1', linea_clean)
                                linea_norm = linea_norm.replace(',', '.')
                                
                                cuotas_encontradas = re.findall(r'\b\d+\.\d+\b', linea_norm)
                                cuotas_validas = [float(c) for c in cuotas_encontradas if 1.01 <= float(c) <= 100.0]
                                
                                if cuotas_validas:
                                    cuota_val = cuotas_validas[-1]
                                    
                                    nombre_txt = re.sub(r'\(\+\d+\)', '', linea_clean)
                                    nombre_txt = re.sub(r'[\|\-\>\:\@\&\%\*\+\(\)]', ' ', nombre_txt)
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
                            st.warning("⚠️ No se pudo procesar la imagen mediante OCR (asegúrate de instalar pytesseract o easyocr).")
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
                        
                        cuotas_encontradas = re.findall(r'\b\d+\.\d+\b', linea_norm)
                        cuotas_validas = [float(c) for c in cuotas_encontradas if 1.01 <= float(c) <= 100.0]
                        
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
                                    if 1.01 <= val <= 100.0:
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

                intentos_str = round(100 / prob_porcentaje, 1) if prob_porcentaje > 0 else 0
                st.markdown(f"""
                    <div class="kpi-card" style="border-left: 5px solid #00d2d3; margin-bottom: 15px;">
                        <div class="kpi-label">🎯 PROBABILIDAD REAL DE ACERTAR ESTE PARLAY</div>
                        <div class="kpi-value" style="color: #00d2d3;">{round(prob_porcentaje, 2)}%</div>
                        <div class="kpi-sub">Equivale a acertar 1 de cada {intentos_str} intentos</div>
                    </div>
                """, unsafe_allow_html=True)

                k1, k2 = st.columns(2)
                k1.metric("Cuota Total de la Casa", f"x{round(cuota_total_ext, 2)}")
                k2.metric("Cuota Justa Sin Margen", f"x{round(cuota_justa, 2)}")

                res_riesgo = evaluar_riesgo_parlay(partidos_para_mc)
                with st.expander(f"🤖 Evaluador de Riesgo: Nivel {res_riesgo['nivel']} (Score: {int(res_riesgo['score'])}/100)", expanded=True):
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
        eq_local = st.text_input("⚽ Equipo Local:", value="Valencia")
    with c_h2:
        eq_visit = st.text_input("⚽ Equipo Visitante:", value="Real Betis")

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

                probs_custom_xg = calcular_modelo_poisson(
                    lambda_local=lambda_l_adj, 
                    lambda_visita=lambda_v_adj, 
                    usar_dixon_coles=True,
                    bajas_local_pct=0.0,
                    bajas_visita_pct=0.0
                )
                
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

        dicc_cuotas_input = {"Opción 1": cuota_1, "Opción X": cuota_X, "Opción 2": cuota_2}
        res_surebet_calc = detectar_surebet(dicc_cuotas_input)

        if res_surebet_calc["es_surebet"]:
            st.success(f"🔥 **SUREBET DETECTADA! Rendimiento Garantizado: +{round(res_surebet_calc['lucro'], 2)}%**")
            inv_p = res_surebet_calc["overround"]
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
            st.warning(f"⚠️ No hay SureBet con estas cuotas (Overround/Margen de la casa: {round(res_surebet_calc['overround']*100, 2)}%).")

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
# PESTAÑA 5: CAZADOR AUTOMÁTICO DE VALUEBETS (+EV)
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
        st.markdown(f"""
            <div class="empty-state-card" style="text-align:center; padding: 30px 20px;">
                <div style="font-size:38px; margin-bottom:8px;">🎯</div>
                <h3 style="color:var(--rg-accent); margin-bottom:8px;">Sin brechas de valor > +{umbral_ev_min}%</h3>
                <p style="color:var(--rg-text-soft); font-size:13.5px; max-width:500px; margin:0 auto 15px auto;">
                    Prueba reduciendo el umbral del slider o realiza un nuevo escaneo seleccionando más ligas en el sidebar.
                </p>
            </div>
        """, unsafe_allow_html=True)

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
# PESTAÑA 6: NOTICIAS, BAJAS Y ALINEACIONES REALES
# ---------------------------------------------------------
elif vista_seleccionada == "📰 BAJAS & ALINEACIONES":
    st.title("📰 Centro de Bajas, Lesiones y Alineaciones")
    st.caption("Información contextual en tiempo real mediante API-Football para validar tus selecciones.")

    dict_partidos = st.session_state.get('datos_cargados', {})

    @st.cache_data(ttl=3600)
    def obtener_id_equipo_af_local(team_name):
        if not AF_API_KEY or not team_name: return None
        try:
            r = requests.get(f"{AF_BASE_URL}/teams", headers=af_headers(), params={"search": team_name}, timeout=10)
            _actualizar_creditos_af(r.headers)
            data = r.json().get("response", [])
            return data[0]["team"]["id"] if data else None
        except Exception:
            return None

    @st.cache_data(ttl=1800)
    def buscar_fixture_id_af(team1_id, team2_id):
        if not AF_API_KEY or not team1_id or not team2_id: return None
        try:
            r = requests.get(f"{AF_BASE_URL}/fixtures", headers=af_headers(), params={"headtohead": f"{team1_id}-{team2_id}"}, timeout=10)
            _actualizar_creditos_af(r.headers)
            res = r.json().get("response", [])
            if res:
                return res[0]["fixture"]["id"]
            return None
        except Exception:
            return None

    @st.cache_data(ttl=600)
    def consultar_lineups_af(fixture_id):
        if not AF_API_KEY or not fixture_id: return []
        try:
            r = requests.get(f"{AF_BASE_URL}/fixtures/lineups", headers=af_headers(), params={"fixture": fixture_id}, timeout=10)
            _actualizar_creditos_af(r.headers)
            return r.json().get("response", [])
        except Exception:
            return []

    col_info_1, col_info_2 = st.columns(2)

    with col_info_1:
        st.subheader("🩹 Lesionados & Sancionados Reales")
        
        @st.cache_data(ttl=3600)
        def consultar_lesiones_af(team_name):
            if not AF_API_KEY or not team_name: return []
            try:
                t_id = obtener_id_equipo_af_local(team_name)
                if not t_id: return []
                r_injuries = requests.get(f"{AF_BASE_URL}/injuries", headers=af_headers(), params={"team": t_id}, timeout=10)
                _actualizar_creditos_af(r_injuries.headers)
                return r_injuries.json().get("response", [])
            except Exception:
                return []

        if dict_partidos:
            equipos_escaneados = sorted(list(set([p['local'] for p in dict_partidos.values()] + [p['visitante'] for p in dict_partidos.values()])))
            equipo_sel = st.selectbox("Selecciona un equipo de la lista escaneada:", equipos_escaneados)
            
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
            st.markdown("""
                <div class="empty-state-card" style="text-align:center; padding:30px 20px;">
                    <div style="font-size:36px; margin-bottom:8px;">🩹</div>
                    <h4 style="color:var(--rg-accent);">Escaneo Requerido</h4>
                    <p style="color:var(--rg-text-soft); font-size:12.5px;">Haz clic en 'Escanear Mercado Now' para listar los equipos y sus bajas.</p>
                </div>
            """, unsafe_allow_html=True)

    with col_info_2:
        st.subheader("📋 Alineaciones Confirmadas")
        
        if dict_partidos:
            partido_dict_map = {f"{p['local']} vs {p['visitante']}": p for p in dict_partidos.values()}
            partido_sel_str = st.selectbox("Selecciona un partido para revisar alineación:", list(partido_dict_map.keys()))
            
            p_obj = partido_dict_map[partido_sel_str]
            
            with st.spinner("Buscando alineaciones en vivo..."):
                id_loc = obtener_id_equipo_af_local(p_obj['local'])
                id_vis = obtener_id_equipo_af_local(p_obj['visitante'])
                fix_id = buscar_fixture_id_af(id_loc, id_vis) if (id_loc and id_vis) else None
                lineups = consultar_lineups_af(fix_id) if fix_id else []

            if lineups and len(lineups) >= 2:
                for team_lineup in lineups:
                    t_name = team_lineup.get("team", {}).get("name", "Equipo")
                    formation = team_lineup.get("formation", "N/A")
                    start_xi = team_lineup.get("startXI", [])
                    
                    with st.expander(f"👕 {t_name} (Formación: {formation})", expanded=True):
                        jugadores = [f"**#{j['player']['number']}** {j['player']['name']} ({j['player']['pos']})" for j in start_xi]
                        st.write(" • ".join(jugadores) if jugadores else "Sin datos de jugadores.")
            else:
                st.warning(f"⚠️ Las alineaciones oficiales para **{partido_sel_str}** aún no se han publicado en la API. Suelen estar disponibles 45-60 minutos antes del partido.")
        else:
            st.caption("Escanea partidos desde el panel lateral para habilitar este módulo.")

# ---------------------------------------------------------
# PESTAÑA 7: GENERADOR DE CARTEL
# ---------------------------------------------------------
elif vista_seleccionada == "🎨 GENERADOR DE CARTEL":
    st.title("🎨 Generador Visual de Pronósticos para Redes")
    st.caption("Crea carteles elegantes y profesionales ajustados automáticamente según las selecciones de tu boleto.")

    formato_cartel = st.segmented_control(
        "📐 Formato de Exportación:",
        options=["📱 Stories / WhatsApp Status (9:16 - 1080x1920)", "🖼️ Cuadrado / Post (4:5 - 1080x1350)"],
        default="📱 Stories / WhatsApp Status (9:16 - 1080x1920)"
    )

    st.subheader("🖼️ Diseñador de Tarjeta de Apuesta")
    c_t1, c_t2 = st.columns([1, 1])

    apuestas_en_boleto = list(st.session_state.ticket_persistente.values())

    cuota_acumulada_cartel = 1.0
    prob_combinada_cartel = 1.0
    html_eventos = ""

    if apuestas_en_boleto:
        for ap in apuestas_en_boleto:
            cuota_acumulada_cartel *= float(ap['cuota'])
            prob_combinada_cartel *= (float(ap['prob_real']) / 100.0)
            html_eventos += (
                f"<div style='margin-bottom: 12px; text-align: left; background: rgba(255,255,255,0.04); padding: 12px 14px; border-radius: 10px; border: 1px solid #232a38;'>"
                f"⚽ <b style='color:#ffffff; font-size:14px;'>{ap['evento']}</b><br>"
                f"🎯 Selección: <span style='color:#00d2d3; font-weight:bold;'>{ap['seleccion']} (x{ap['cuota']})</span>"
                f"</div>"
            )
        
        b = cuota_acumulada_cartel - 1.0
        p = prob_combinada_cartel
        q = 1.0 - p
        f_kelly = ((b * p) - q) / b if b > 0 else 0
        stake_sugerido_monto = max(0.0, f_kelly * fraccion_kelly * bankroll_total)
        
        pct_banca = (stake_sugerido_monto / bankroll_total) * 100 if bankroll_total > 0 else 0
        stake_auto_escala = 1 if pct_banca == 0 else min(10, max(1, int(np.ceil(pct_banca * 2))))
    else:
        html_eventos = (
            "<p style='font-style: italic; color: #ffffff; margin-bottom: 5px; text-align: center;'>"
            "No has marcado selecciones en el Radar.</p>"
            "<p style='color: #8a94a6; font-size: 12px; text-align: center;'>"
            "Ve a la pestaña <b>🚀 RADAR MULTI-MERCADO</b> y marca casillas para generar la tarjeta automáticamente.</p>"
        )
        cuota_acumulada_cartel = 0.0
        stake_auto_escala = 1
        stake_sugerido_monto = 0.0

    with c_t1:
        titulo_cartel = st.text_input("Título del Cartel:", "🔥 PARLAY DEL DÍA DE ALTA PROBABILIDAD")
        analista_nombre = st.text_input("Nombre de Tipster / Canal:", "@MiApuestaSeguraPro")
        
        monto_sugerido = st.number_input(
            "Stake Sugerido (1-10):", 
            min_value=1, max_value=10, 
            value=int(stake_auto_escala),
            help=f"Calculado automáticamente. Inversión estimada recomendada: ${round(stake_sugerido_monto, 2)} USD"
        )

    with c_t2:
        cuota_txt = f"x{round(cuota_acumulada_cartel, 2)}" if cuota_acumulada_cartel > 0 else "x1.00"

        es_vertical = "Stories" in formato_cartel
        ancho_px = "420px" if es_vertical else "480px"
        alto_px = "680px" if es_vertical else "520px"
        scale_val = "2.8" if es_vertical else "2.2"

        cartel_componente_html = f"""
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
        <div id="cartel_container" style="
            width: {ancho_px}; 
            min-height: {alto_px}; 
            background: linear-gradient(135deg, #0b0e14 0%, #171c27 100%); 
            border: 2px solid #00d2d3; 
            border-radius: 18px; 
            padding: 26px; 
            box-sizing: border-box; 
            font-family: 'Inter', sans-serif; 
            color: white; 
            margin: 0 auto;
            box-shadow: 0 10px 30px rgba(0,210,211,0.15);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        ">
            <div>
                <div style="text-align: center; margin-bottom: 18px;">
                    <span style="background: rgba(0,210,211,0.15); color: #00d2d3; padding: 5px 14px; border-radius: 20px; font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px;">PREDICCIÓN EXCLUSIVA</span>
                    <h3 style="color: #00d2d3; margin: 12px 0 6px 0; font-size: 19px; font-weight: 800;">{titulo_cartel}</h3>
                    <p style="color: #8a94a6; font-size: 12px; margin: 0;">Tipster: <b style="color:#ffffff;">{analista_nombre}</b> | Stake: <b style="color:#feca57;">{monto_sugerido}/10</b></p>
                </div>
                <hr style="border: 0; border-top: 1px solid #232a38; margin: 14px 0;">
                <div style="font-size: 13px;">
                    {html_eventos}
                </div>
            </div>
            <div>
                <hr style="border: 0; border-top: 1px solid #232a38; margin: 14px 0;">
                <div style="text-align: center; background: rgba(0,210,211,0.08); border-radius: 14px; padding: 14px; border: 1px dashed rgba(0,210,211,0.3);">
                    <div style="color: #8a94a6; font-size: 11px; font-weight: 700; text-transform: uppercase;">Cuota Total Acumulada</div>
                    <div style="color: #ffffff; font-size: 32px; font-weight: 800; font-family: sans-serif;">{cuota_txt}</div>
                    <div style="color: #00d2d3; font-size: 11px; margin-top: 2px;">💵 Inversión Recomendada: ${round(stake_sugerido_monto, 2)} USD</div>
                </div>
            </div>
        </div>

        <div style="text-align: center; margin-top: 18px;">
            <button onclick="descargarPNG()" style="
                background: linear-gradient(90deg, #00b3b4, #00d2d3); 
                color: black; 
                border: none; 
                padding: 12px 22px; 
                border-radius: 8px; 
                font-weight: 800; 
                font-size: 13.5px; 
                cursor: pointer; 
                box-shadow: 0 4px 15px rgba(0,210,211,0.3);
                width: 100%;
            ">📸 Descargar Imagen del Cartel (HD High Resolution PNG)</button>
        </div>

        <script>
        function descargarPNG() {{
            const container = document.getElementById('cartel_container');
            html2canvas(container, {{ scale: {scale_val}, backgroundColor: '#0b0e14' }}).then(canvas => {{
                let link = document.createElement('a');
                link.download = 'Cartel_Parlay_HD.png';
                link.href = canvas.toDataURL('image/png');
                link.click();
            }});
        }}
        </script>
        """
        components.html(cartel_componente_html, height=760 if es_vertical else 600)

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
                st.toast("Bitácora restaurada exitosamente", icon="✅")
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
                    st.toast(f"Ticket #{idx+1} actualizado a {nuevo}", icon="📝")
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

        col_d, col_b1, col_b2 = st.columns([2, 1.5, 1])
        col_d.download_button("📊 Descargar Bitácora (CSV)", data=df_act.to_csv(index=False).encode('utf-8'), file_name="Reporte_Apuestas.csv", mime='text/csv', use_container_width=True)
        with col_b1:
            confirmar_borrado_bit = st.checkbox("⚠️ Confirmar borrado", value=False, key="chk_conf_bitacora")
        with col_b2:
            if st.button("🗑️ Reiniciar", use_container_width=True, disabled=not bool(confirmar_borrado_bit)):
                BitacoraManager.limpiar()
                st.toast("Bitácora reiniciada por completo", icon="🧹")
                st.rerun()
    else:
        st.markdown("""
            <div class="empty-state-card" style="text-align: center; padding: 40px 20px;">
                <div style="font-size: 42px; margin-bottom: 10px;">📋</div>
                <h3 style="color:var(--rg-accent); margin-bottom: 8px;">Bitácora de Apuestas Vacía</h3>
                <p style="color:var(--rg-text-soft); max-width: 500px; margin: 0 auto 20px auto; font-size: 14px;">
                    Aún no tienes registros guardados. Ve a la pestaña Radar para armar tu primer boleto y guardarlo aquí.
                </p>
            </div>
        """, unsafe_allow_html=True)

# ---------------------------------------------------------
# PESTAÑA 9: ATENCIÓN AL CLIENTE Y SOPORTE
# ---------------------------------------------------------
elif vista_seleccionada == "💬 ATENCIÓN Y MEJORAS":
    st.title("💬 Centro de Atención, Soporte y Sugerencias")
    st.caption("Envía tus comentarios, reporta fallos o sugiere nuevas funcionalidades para el sistema.")

    col_form, col_info = st.columns([1.2, 1], gap="large")

    with col_form:
        with st.container(border=True):
            st.subheader("📬 Formulario de Contacto y Feedback")
            
            tipo_mensaje = st.selectbox(
                "Categoría del mensaje:",
                ["💡 Sugerencia de Mejora / Feature Request", "🐛 Reportar un Error / Bug", "❓ Consulta de Soporte / Ayuda", "⭐ Comentario General"]
            )

            nombre_usuario = st.text_input("Tu Nombre o Usuario (Opcional):", placeholder="Ej: Carlos V.")
            contacto_usuario = st.text_input("Correo o Telegram de contacto (Opcional):", placeholder="Ej: @usuario_tg o correo@ejemplo.com")

            prioridad = st.select_slider(
                "Nivel de urgencia / importancia:",
                options=["🟢 Baja", "🟡 Media", "🔴 Alta / Crítica"]
            )

            mensaje_cuerpo = st.text_area(
                "Detalle de tu mensaje o mejora propuesta:",
                height=150,
                placeholder="Describe a detalle la funcionalidad que te gustaría ver o el fallo detectado..."
            )

            if st.button("🚀 Enviar Mensaje de Feedback", type="primary", use_container_width=True):
                if not mensaje_cuerpo.strip():
                    st.warning("⚠️ Por favor escribe un mensaje antes de enviar.")
                else:
                    fecha_envio = datetime.now().strftime("%d/%m/%Y %H:%M")
                    usr_str = nombre_usuario.strip() if nombre_usuario.strip() else "Anónimo"
                    cnt_str = contacto_usuario.strip() if contacto_usuario.strip() else "No provisto"

                    texto_feedback = (
                        f"📩 *NUEVO FEEDBACK / SOPORTE*\n\n"
                        f"📌 *Categoría:* {tipo_mensaje}\n"
                        f"👤 *Usuario:* {usr_str}\n"
                        f"📇 *Contacto:* {cnt_str}\n"
                        f"🚨 *Urgencia:* {prioridad}\n"
                        f"📅 *Fecha:* {fecha_envio}\n\n"
                        f"💬 *Mensaje:*\n{mensaje_cuerpo.strip()}"
                    )

                    enviado_tg = enviar_telegram(texto_feedback)

                    feedback_file = "feedback_registros.json"
                    registros = []
                    if os.path.exists(feedback_file):
                        try:
                            with open(feedback_file, "r", encoding="utf-8") as f:
                                registros = json.load(f)
                        except Exception:
                            registros = []

                    registros.append({
                        "fecha": fecha_envio,
                        "tipo": tipo_mensaje,
                        "usuario": usr_str,
                        "contacto": cnt_str,
                        "prioridad": prioridad,
                        "mensaje": mensaje_cuerpo.strip()
                    })

                    try:
                        with open(feedback_file, "w", encoding="utf-8") as f:
                            json.dump(registros, f, ensure_ascii=False, indent=4)
                    except Exception as e:
                        st.error(f"Error al guardar registro local: {e}")

                    if enviado_tg:
                        st.toast("✅ Mensaje enviado a Telegram y registrado localmente.", icon="🚀")
                    else:
                        st.toast("✅ Mensaje registrado localmente.", icon="📝")

                    st.balloons()

    with col_info:
        with st.container(border=True):
            st.subheader("ℹ️ Canales Directos y FAQ")
            st.markdown("""
                **¿Necesitas ayuda inmediata?**
                * ✉️ **Soporte Directo:** Recibirás las sugerencias al instante en Telegram.
                * ⚡ **Respuesta:** Las sugerencias de mejoras se revisan periódicamente para ser incluidas en los próximos parches.

                ---

                **🛠️ ¿Qué puedes reportar aquí?**
                1. **Incompatibilidad de cuotas:** Si una casa de apuestas muestra valores desfasados.
                2. **Nuevos mercados:** Si deseas que se agreguen hándicaps, córners o tarjetas.
                3. **Errores visuales:** Problemas de carga en móvil o exportación de carteles.
            """)

            if os.path.exists("feedback_registros.json"):
                with st.expander("📋 Ver registro local de mensajes enviados"):
                    try:
                        with open("feedback_registros.json", "r", encoding="utf-8") as f:
                            data_fb = json.load(f)
                            if data_fb:
                                st.dataframe(pd.DataFrame(data_fb), use_container_width=True, hide_index=True)
                    except Exception:
                        st.caption("Sin registros que mostrar.")
