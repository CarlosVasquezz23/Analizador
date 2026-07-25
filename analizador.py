import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
import urllib.parse

# Configuración de página avanzada
st.set_page_config(page_title="Radar Enterprise Parlay Global", page_icon="⚽", layout="wide")

# Estilos visuales limpios para las métricas de probabilidad
st.markdown("""
    <style>
    .prob-alta { color: #2ecc71; font-weight: bold; }
    .prob-media { color: #f1c40f; font-weight: bold; }
    .prob-baja { color: #e74c3c; font-weight: bold; }
    .match-header { font-size: 18px; font-weight: bold; margin-bottom: 2px; }
    </style>
""", unsafe_allow_html=True)

# --- TU CONFIGURACIÓN ---
API_KEY = "e6414a3efabaf34994030cd0a8ea88b1"

# --- ESTRUCTURAS DE DATOS EXTENDIDAS ---
ligas_top = {
    "🇪🇺 Champions League (Europa)": "soccer_uefa_champions_league",
    "🇪🇺 Europa League (Europa)": "soccer_uefa_europa_league",
    "🏆 Copa Libertadores (CONMEBOL)": "soccer_conmebol_copa_distribuidores",
    "🥈 Copa Sudamericana (CONMEBOL)": "soccer_conmebol_copa_sudamericana"
}

ligas_locales = {
    "🇺🇾 Primera División (Uruguay)": "soccer_uruguay_primera_division",
    "🇵🇪 Liga 1 (Perú)": "soccer_peru_primera_division",
    "🇦🇷 Liga Profesional (Argentina)": "soccer_argentina_primera_division",
    "🇨🇴 Primera A (Colombia)": "soccer_colombia_primera_a",
    "🇨🇱 Primera División (Chile)": "soccer_chile_campeonato",
    "🇪🇨 LigaPro (Ecuador)": "soccer_ecuador_liga_pro",
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

ligas_locales_ordenadas = dict(sorted(ligas_locales.items()))
todas_las_ligas = {**ligas_top, **ligas_locales_ordenadas}

diccionario_mercados = {
    "1X2 (Ganador)": "h2h",
    "Doble Oportunidad": "double_chance",
    "Ambos Anotan (BTTS)": "btts",
    "Goles Más/Menos 2.5": "totals"
}

# --- INICIALIZACIÓN DE ESTADOS ---
if 'historial_apuestas' not in st.session_state:
    st.session_state.historial_apuestas = []
if 'version_ticket' not in st.session_state:
    st.session_state.version_ticket = 0
if 'datos_cargados' not in st.session_state:
    st.session_state.datos_cargados = {}
if 'versiones_partidos' not in st.session_state:
    st.session_state.versiones_partidos = {}
if 'claves_auto' not in st.session_state:
    st.session_state.claves_auto = set()

# --- CONFIGURACIÓN E INTERFAZ EN EL SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Filtros de Control Global")
    
    ligas_sels = st.multiselect("Selecciona los Torneos a Analizar:", list(todas_las_ligas.keys()), default=[])

    mercados_sels = st.multiselect("Mercados de Análisis:", list(diccionario_mercados.keys()), default=["1X2 (Ganador)"])
    if "Ambos Anotan (BTTS)" in mercados_sels:
        st.caption("⚠️ BTTS consulta la API 1 vez por cada partido (más gasto de créditos). Doble Oportunidad se calcula matemáticamente.")

    tiempo_sel = st.selectbox("Rango Temporal:", ["24 Horas", "48 Horas", "72 Horas"], index=1)
    limite_h = int(tiempo_sel.split()[0])

    monto_inversion = st.number_input("Inversión Base ($):", min_value=1.0, value=10.0, step=1.0)
    
    st.markdown("---")
    consultar = st.button("🔍 Consultar Radar Múltiple", type="primary", use_container_width=True)
    num_eventos_auto = st.slider("Eventos para el Generador Automático:", min_value=2, max_value=6, value=3)
    generar_auto = st.button("🎲 ¡Pre-seleccionar Muestras!", use_container_width=True)

# --- NAVEGACIÓN PRINCIPAL ---
pestana_radar, pestana_historial = st.tabs(["🚀 RADAR MULTI-MERCADO & VALUEBETS", "📊 BITÁCORA PRO & AUDITORÍA ROI"])

# --- CACHÉ INTELIGENTE ---
@st.cache_data(ttl=120)
def consultar_api_odds(sport_key, market_key):
    if not sport_key:
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={API_KEY}&regions=eu,us&markets={market_key}&oddsFormat=decimal"
    try:
        response = requests.get(url)
        if response.status_code == 429:
            st.error("❌ ¡Límite de créditos mensuales agotado en The Odds API!")
            return []
        elif response.status_code == 401:
            st.error("❌ API Key inválida.")
            return []
        elif response.status_code != 200:
            return []
        res_json = response.json()
        if res_json and len(res_json) > 0:
            return res_json
    except Exception:
        pass
    return []

@st.cache_data(ttl=120)
def consultar_api_odds_evento(sport_key, event_id, market_key):
    if not sport_key or not event_id:
        return None
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/odds/?apiKey={API_KEY}&regions=eu,us&markets={market_key}&oddsFormat=decimal"
    try:
        response = requests.get(url)
        if response.status_code == 429:
            st.error("❌ ¡Límite de créditos mensuales agotado!")
            return None
        elif response.status_code == 401:
            st.error("❌ API Key inválida.")
            return None
        elif response.status_code != 200:
            return None
        return response.json()
    except Exception:
        return None

def filtrar_partidos_por_fecha(datos, limite_horas):
    ahora_utc = datetime.now(timezone.utc)
    resultado = []
    if not datos or not isinstance(datos, list):
        return resultado

    for partido in datos:
        try:
            fecha_utc = datetime.fromisoformat(partido['commence_time'].replace('Z', '+00:00'))
        except (ValueError, KeyError):
            continue

        horas_para_partido = (fecha_utc - ahora_utc).total_seconds() / 3600
        if horas_para_partido < -6.0 or horas_para_partido > (limite_horas + 12):
            continue

        resultado.append(partido)
    return resultado

# --- PROCESADOR MULTI-MERCADO MEJORADO CON CÁLCULO DE MARGEN CIENTÍFICO ---
def procesar_e_inyectar_mercado(datos, mercado, limite_horas, nombre_liga, diccionario_consolidador):
    ahora_utc = datetime.now(timezone.utc)
    if not datos or not isinstance(datos, list):
        return

    for partido in datos:
        partido_id = partido['id']
        home = partido['home_team']
        away = partido['away_team']

        try:
            fecha_utc = datetime.fromisoformat(partido['commence_time'].replace('Z', '+00:00'))
        except ValueError:
            continue

        horas_para_partido = (fecha_utc - ahora_utc).total_seconds() / 3600
        if horas_para_partido < -6.0 or horas_para_partido > (limite_horas + 12):
            continue

        fecha_local = fecha_utc - timedelta(hours=5)
        bookmakers = partido.get('bookmakers', [])
        if not bookmakers:
            continue

        cuotas_globales = {}
        betano_cuotas = {}

        for b in bookmakers:
            b_key = b['key'].lower()
            if not b.get('markets'):
                continue

            dict_b_markets = {m['key']: m['outcomes'] for m in b['markets']}

            if mercado == "Doble Oportunidad":
                tiene_nativo = "double_chance" in dict_b_markets
                if tiene_nativo:
                    for o in dict_b_markets["double_chance"]:
                        o_name = o['name']
                        if o_name in ["home_draw", "home or draw", "1X", f"{home} or draw"]: o_name = "1X (Local o Empate)"
                        elif o_name in ["away_draw", "away or draw", "X2", f"{away} or draw"]: o_name = "X2 (Visitante o Empate)"
                        elif o_name in ["home_away", "home or away", "12", f"{home} or {away}"]: o_name = "12 (Local o Visitante)"

                        cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                        if b_key == "betano": betano_cuotas[o_name] = float(o['price'])

                elif "h2h" in dict_b_markets:
                    outcomes_h2h = dict_b_markets["h2h"]
                    precios_h2h = {o['name']: float(o['price']) for o in outcomes_h2h}
                    draw_key = next((k for k in precios_h2h.keys() if k not in [home, away]), None)

                    if home in precios_h2h and away in precios_h2h and draw_key:
                        c_home, c_draw, c_away = precios_h2h[home], precios_h2h[draw_key], precios_h2h[away]
                        c_1x = round((c_home * c_draw) / (c_home + c_draw), 2)
                        c_x2 = round((c_away * c_draw) / (c_away + c_draw), 2)
                        c_12 = round((c_home * c_away) / (c_home + c_away), 2)

                        cuotas_globales.setdefault("1X (Local o Empate)", []).append((c_1x, b['title']))
                        cuotas_globales.setdefault("X2 (Visitante o Empate)", []).append((c_x2, b['title']))
                        cuotas_globales.setdefault("12 (Local o Visitante)", []).append((c_12, b['title']))
                        if b_key == "betano":
                            betano_cuotas["1X (Local o Empate)"] = c_1x
                            betano_cuotas["X2 (Visitante o Empate)"] = c_x2
                            betano_cuotas["12 (Local o Visitante)"] = c_12

            elif mercado == "Ambos Anotan (BTTS)":
                if "btts" in dict_b_markets:
                    for o in dict_b_markets["btts"]:
                        o_name = "Sí" if o['name'].lower() in ["yes", "sí", "si"] else "No"
                        cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                        if b_key == "betano": betano_cuotas[o_name] = float(o['price'])

            elif "Goles" in mercado and "totals" in dict_b_markets:
                for o in dict_b_markets["totals"]:
                    point = o.get('point', 2.5)
                    if point == 2.5:
                        o_name = "Más de 2.5" if o['name'].lower() in ["over", "más", "mas"] else "Menos de 2.5"
                        cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                        if b_key == "betano": betano_cuotas[o_name] = float(o['price'])

            elif mercado == "1X2 (Ganador)" and "h2h" in dict_b_markets:
                for o in dict_b_markets["h2h"]:
                    o_name = "Local" if o['name'] == home else ("Visitante" if o['name'] == away else "Empate")
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                    if b_key == "betano": betano_cuotas[o_name] = float(o['price'])

        max_cuotas, max_bookies, value_bets = {}, {}, {}
        
        # --- ALGORITMO DE DETECCIÓN CLEAN-VALUEBETS ---
        # Calculamos primero las cuotas promedio del mercado general
        cuotas_promedio_dict = {}
        for opcion, tuplas in cuotas_globales.items():
            precios = [t[0] for t in tuplas]
            cuotas_promedio_dict[opcion] = sum(precios) / len(precios) if precios else 1.0

        # Calculamos el overround (margen implícito acumulado del promedio del mercado)
        overround = sum([1 / cp for cp in cuotas_promedio_dict.values()])

        for opcion, tuplas in cuotas_globales.items():
            precios = [t[0] for t in tuplas]
            cuota_promedio = cuotas_promedio_dict[opcion]
            
            # Probabilidad Real libre de overround (Normalizada matemáticamente)
            probabilidad_real = (1 / cuota_promedio) / overround if overround > 0 else (1 / cuota_promedio)
            
            cuota_maxima = max(precios)
            bookie_maximo = tuplas[precios.index(cuota_maxima)][1]

            max_cuotas[opcion] = cuota_maxima
            max_bookies[opcion] = bookie_maximo
            
            # EV calculado usando la probabilidad implícita justa sin margen del mercado
            ev = (cuota_maxima * probabilidad_real) - 1
            value_bets[opcion] = {"ev": ev, "prob_real": probabilidad_real * 100, "es_value": ev > 0.02}

        if max_cuotas:
            if partido_id not in diccionario_consolidador:
                diccionario_consolidador[partido_id] = {
                    "id": partido_id,
                    "liga_origen": nombre_liga,
                    "fecha_str": fecha_local.strftime("%d/%m/%Y - %H:%M"),
                    "local": home, "visitante": away,
                    "mercados": {}
                }
            diccionario_consolidador[partido_id]["mercados"][mercado] = {
                "max_cuotas": max_cuotas,
                "max_bookies": max_bookies,
                "betano_cuotas": betano_cuotas,
                "value_bets": value_bets
            }

# ==========================================
# VISTA: PESTAÑA 1 - RADAR Y APUESTAS
# ==========================================
with pestana_radar:
    st.title("⚽ Radar Avanzado Multi-Mercado Global")

    if consultar:
        if len(ligas_sels) > 0 and len(mercados_sels) > 0:
            st.cache_data.clear()
            consolidador = {}

            mercados_featured = [m for m in mercados_sels if diccionario_mercados[m] in ("h2h", "totals")]
            pidio_doble_oportunidad = "Doble Oportunidad" in mercados_sels
            pidio_btts = "Ambos Anotan (BTTS)" in mercados_sels

            for liga in ligas_sels:
                sport_key = todas_las_ligas[liga]

                # 1) MERCADOS FEATURED
                for m_sel in mercados_featured:
                    market_api = diccionario_mercados[m_sel]
                    raw_data = consultar_api_odds(sport_key, market_key=market_api)
                    procesar_e_inyectar_mercado(raw_data, m_sel, limite_h, liga, consolidador)

                # 2) DOBLE OPORTUNIDAD
                if pidio_doble_oportunidad:
                    base_h2h = consultar_api_odds(sport_key, market_key="h2h")
                    procesar_e_inyectar_mercado(base_h2h, "Doble Oportunidad", limite_h, liga, consolidador)

                # 3) BTTS
                if pidio_btts:
                    base_para_filtrar = consultar_api_odds(sport_key, market_key="h2h")
                    eventos_filtrados = filtrar_partidos_por_fecha(base_para_filtrar, limite_h)
                    for partido_base in eventos_filtrados:
                        event_id = partido_base['id']
                        datos_evento = consultar_api_odds_evento(sport_key, event_id, "btts")
                        if datos_evento:
                            procesar_e_inyectar_mercado([datos_evento], "Ambos Anotan (BTTS)", limite_h, liga, consolidador)

            st.session_state.datos_cargados = consolidador
            st.session_state.claves_auto = set()
            st.session_state.version_ticket += 1

            if not consolidador:
                st.info("⚠️ No se encontraron partidos activos o cuotas disponibles en el rango de horas seleccionado.")
        else:
            st.warning("Elige al menos una liga y un mercado antes de consultar en el menú lateral.")

    dict_partidos = st.session_state.datos_cargados

    # LÓGICA DE PRE-SELECCIÓN AUTOMÁTICA
    if generar_auto:
        if not dict_partidos:
            st.error("❌ Primero debes hacer clic en 'Consultar Radar Múltiple' para cargar datos.")
        else:
            bolsa_probabilidades = []
            for p_id, part in dict_partidos.items():
                for nombre_m, m_info in part['mercados'].items():
                    for opcion, val_data in m_info['value_bets'].items():
                        clave_chk = f"ap_{part['id']}_{nombre_m}_{opcion}"
                        bolsa_probabilidades.append({
                            "clave": clave_chk,
                            "prob_real": val_data['prob_real']
                        })

            bolsa_probabilidades = sorted(bolsa_probabilidades, key=lambda x: x['prob_real'], reverse=True)
            k_seleccion = min(len(bolsa_probabilidades), num_eventos_auto)

            if k_seleccion > 0:
                st.session_state.claves_auto = set([x['clave'] for x in bolsa_probabilidades[:k_seleccion]])
                st.session_state.version_ticket += 1
                st.success(f"🎯 Marcados automáticamente los {k_seleccion} eventos con mayor probabilidad.")
            else:
                st.error("No se encontraron eventos bajo los filtros actuales.")

    apuestas_seleccionadas = []

    if st.session_state.claves_auto or dict_partidos:
        if st.button("🧹 Limpiar todas las casillas marcadas"):
            st.session_state.claves_auto = set()
            st.session_state.version_ticket += 1
            st.rerun()

    if dict_partidos:
        st.subheader(f"📋 Eventos Consolidados Encontrados ({len(dict_partidos)})")
        v_ticket = st.session_state.version_ticket
        ligas_con_datos = list(set([p['liga_origen'] for p in dict_partidos.values()]))
        pestanas_ligas = st.tabs(ligas_con_datos)

        for p_idx, liga_pestaña in enumerate(ligas_con_datos):
            with pestanas_ligas[p_idx]:
                partidos_filtrados = [p for p in dict_partidos.values() if p['liga_origen'] == liga_pestaña]
                for part in partidos_filtrados:
                    if part['id'] not in st.session_state.versiones_partidos:
                        st.session_state.versiones_partidos[part['id']] = 0
                    v_partido = st.session_state.versiones_partidos[part['id']]

                    with st.container(border=True):
                        col_borrar, col_info = st.columns([0.4, 5.6])
                        with col_borrar:
                            if st.button("🗑️", key=f"clear_{part['id']}"):
                                del st.session_state.datos_cargados[part['id']]
                                st.rerun()

                        with col_info:
                            st.markdown(f"<div class='match-header'>⚽ {part['local']} vs {part['visitante']}</div>", unsafe_allow_html=True)
                            st.caption(f"📅 Hora Local: {part['fecha_str']}")

                        mercados_del_partido = list(part['mercados'].keys())
                        sub_tabs_mercados = st.tabs(mercados_del_partido)

                        for m_idx, nombre_m in enumerate(mercados_del_partido):
                            with sub_tabs_mercados[m_idx]:
                                m_info = part['mercados'][nombre_m]
                                opciones_disponibles = list(m_info['max_cuotas'].keys())

                                if nombre_m == "1X2 (Ganador)": orden_estricto = ["Local", "Empate", "Visitante"]
                                elif nombre_m == "Doble Oportunidad": orden_estricto = ["1X (Local o Empate)", "12 (Local o Visitante)", "X2 (Visitante o Empate)"]
                                elif nombre_m == "Ambos Anotan (BTTS)": orden_estricto = ["Sí", "No"]
                                else: orden_estricto = sorted(opciones_disponibles, key=lambda x: 0 if "Más" in x else 1)

                                sub_cols = st.columns(len(orden_estricto))
                                for idx, plantilla_opcion in enumerate(orden_estricto):
                                    with sub_cols[idx]:
                                        if plantilla_opcion in opciones_disponibles:
                                            cuota_m = m_info['max_cuotas'][plantilla_opcion]
                                            casa_m = m_info['max_bookies'][plantilla_opcion]
                                            info_val = m_info['value_bets'][plantilla_opcion]
                                            lbl_val = "🔥 VALOR" if info_val['es_value'] else ""

                                            clave_base = f"ap_{part['id']}_{nombre_m}_{plantilla_opcion}"
                                            marcado_inicial = clave_base in st.session_state.claves_auto

                                            chk = st.checkbox(
                                                f"{plantilla_opcion} ({cuota_m}) {lbl_val}",
                                                value=marcado_inicial,
                                                key=f"render_{clave_base}_vp{v_partido}_vt{v_ticket}"
                                            )

                                            facing_betano = m_info['betano_cuotas'].get(plantilla_opcion, None)
                                            txt_betano = f" | Betano: {facing_betano}" if facing_betano else ""
                                            p_real = info_val['prob_real']
                                            clase_color = "prob-alta" if p_real >= 60 else ("prob-media" if p_real >= 40 else "prob-baja")

                                            st.markdown(f"<small>🏠 {casa_m}{txt_betano}<br>🎯 Prob: <span class='{clase_color}'>{round(p_real,1)}%</span></small>", unsafe_allow_html=True)

                                            if chk:
                                                apuestas_seleccionadas.append({
                                                    "evento": f"{part['local']} vs {part['visitante']}",
                                                    "liga": part['liga_origen'], "mercado": nombre_m,
                                                    "seleccion": plantilla_opcion, "cuota": cuota_m, "casa": casa_m
                                                })

# --- SECCIÓN DE BOLETO / PARLAY ---
    if apuestas_seleccionadas:
        st.markdown("---")
        st.header("🎟️ Configuración de Parlay Global")

        cuota_acumulada = 1.0
        texto_whatsapp = "🚀 *TICKET PARLAY SUGERIDO DESDE RADAR GLOBAL* 🚀\n\n"

        with st.container(border=True):
            for ap in apuestas_seleccionadas:
                cuota_acumulada *= float(ap['cuota'])
                st.markdown(f"✔️ **{ap['evento']}** ({ap['liga']}) ➔ `{ap['seleccion']}` | *{ap['mercado']}* | Cuota: **{ap['cuota']}** (Casa: *{ap['casa']}*)")
                texto_whatsapp += f"⚽ *{ap['evento']}*\n🏆 {ap['liga']}\n🎯 {ap['mercado']}: *{ap['seleccion']}* (x{ap['cuota']}) - 🏢 {ap['casa']}\n\n"

            ganancia_estimada = cuota_acumulada * monto_inversion
            ganancia_neta = ganancia_estimada - monto_inversion

            texto_whatsapp += f"📊 *RESUMEN DEL PARLAY*\n🔹 Eventos combinados: {len(apuestas_seleccionadas)}\n📈 Cuota Final total: *x{round(cuota_acumulada, 2)}*\n💵 Inversión: *${round(monto_inversion, 2)}*\n💰 Ganancia Neta Potencial: *${round(ganancia_neta, 2)}*"
            msg_encoded = urllib.parse.quote(texto_whatsapp)

            st.markdown(" ")
            c_m1, c_m2, c_m3 = st.columns(3)
            c_m1.metric("Cuota Final", f"x{round(cuota_acumulada, 2)}")
            c_m2.metric("Retorno Total", f"${round(ganancia_estimada, 2)}")
            c_m3.metric("Ganancia Neta", f"${round(ganancia_neta, 2)}")

            col_btn_reg, col_btn_ws = st.columns([1, 1])
            with col_btn_reg:
                if st.button("💾 Registrar y Enviar Apuesta al Historial", type="primary", use_container_width=True):
                    st.session_state.historial_apuestas.append({
                        "Fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "Detalles": f"{len(apuestas_seleccionadas)} markets combinados",
                        "Market": "Multi-Mercado",
                        "Cuota": cuota_acumulada,
                        "Inversión": monto_inversion,
                        "Estado": "Pendiente",
                        "Ganancia Potencial": ganancia_neta
                    })
                    st.session_state.version_ticket += 1
                    st.session_state.claves_auto = set()
                    st.toast("¡Apuesta registrada exitosamente!", icon="💾")
                    st.rerun()

            with col_btn_ws:
                st.markdown(f'<a href="https://api.whatsapp.com/send?text={msg_encoded}" target="_blank" style="text-decoration:none;"><button style="border:none; background-color:#25D366; color:white; padding:10px 14px; border-radius:8px; font-size:16px; font-weight:bold; width:100%; height:43px; cursor:pointer; display:flex; align-items:center; justify-content:center; gap:8px;">📲 Compartir Parlay Completo en WhatsApp</button></a>', unsafe_allow_html=True)

# ==========================================
# VISTA: PESTAÑA 2 - AUDITORÍA & ROI
# ==========================================
with pestana_historial:
    st.title("📊 Módulo de Auditoría Financiera Avanzada")

    if st.session_state.historial_apuestas:
        df_historial = pd.DataFrame(st.session_state.historial_apuestas)

        st.subheader("📝 Modificar Resultados Recientes")
        for idx, fila in df_historial.iterrows():
            col_d, col_est = st.columns([3, 1])
            with col_d:
                st.write(f"🆔 **Ticket #{idx+1}** ({fila['Fecha']}) | Inversión: ${fila['Inversión']} | Cuota: x{round(fila['Cuota'],2)} | {fila['Detalles']}")
            with col_est:
                opciones_resultado = ["Pendiente", "Ganado", "Perdido"]
                index_actual = opciones_resultado.index(fila['Estado'])

                nuevo_estado = st.selectbox("Resultado:", opciones_resultado, index=index_actual, key=f"estado_{idx}")
                if nuevo_estado != fila['Estado']:
                    st.session_state.historial_apuestas[idx]['Estado'] = nuevo_estado
                    st.rerun()

        df_actualizado = pd.DataFrame(st.session_state.historial_apuestas)
        total_invertido = df_actualizado['Inversión'].sum()
        ganado_mask = df_actualizado['Estado'] == "Ganado"
        total_retornado = (df_actualizado[ganado_mask]['Inversión'] * df_actualizado[ganado_mask]['Cuota']).sum()
        balance_neto = total_retornado - total_invertido
        roi = (balance_neto / total_invertido * 100) if total_invertido > 0 else 0
        tickets_terminados = df_actualizado[df_actualizado['Estado'] != "Pendiente"]
        tasa_acierto = (len(df_actualizado[ganado_mask]) / len(tickets_terminados) * 100) if len(tickets_terminados) > 0 else 0

        st.markdown("---")
        st.subheader("📈 Tus Métricas de Rendimiento Real")
        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("Total Invertido", f"${round(total_invertido, 2)}")
        kpi2.metric("Balance Neto", f"${round(balance_neto, 2)}", delta=f"{round(roi,2)}% ROI")
        kpi3.metric("Tasa de Acierto", f"{round(tasa_acierto, 1)}%")

        df_actualizado['Ganancia_Efectiva'] = df_actualizado.apply(lambda r: (r['Inversión']*r['Cuota'] - r['Inversión']) if r['Estado'] == "Ganado" else (-r['Inversión'] if r['Estado'] == "Perdido" else 0), axis=1)
        df_actualizado['Rendimiento Acumulado ($)'] = df_actualizado['Ganancia_Efectiva'].cumsum()

        st.dataframe(df_actualizado[['Fecha', 'Detalles', 'Inversión', 'Estado', 'Rendimiento Acumulado ($)']], use_container_width=True)
        csv_data = df_actualizado.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar Bitácora Completa (CSV)", data=csv_data, file_name=f"Reporte_Apuestas.csv", mime='text/csv', use_container_width=True)
    else:
        st.info("Aún no tienes apuestas registradas en la bitácora.")
