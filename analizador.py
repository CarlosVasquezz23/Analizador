import streamlit as st
import pandas as pd
import requests
import json
import os
import urllib.parse
from datetime import datetime, timedelta

# Configuración de página
st.set_page_config(page_title="Radar Enterprise Parlay Global", layout="wide", page_icon="⚽")

# Estilos CSS personalizados para mejorar el diseño UX
st.markdown("""
<style>
    .match-header { font-size: 18px; font-weight: bold; color: #1E88E5; margin-bottom: 2px; }
    .prob-alta { color: #2E7D32; font-weight: bold; }
    .prob-media { color: #EF6C00; font-weight: bold; }
    .prob-baja { color: #C62828; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# Claves de Almacenamiento Local (Simulado mediante archivo JSON)
HISTORIAL_FILE = "historial_apuestas_local.json"

def cargar_historial_local():
    if os.path.exists(HISTORIAL_FILE):
        try:
            with open(HISTORIAL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def guardar_historial_local(historial):
    try:
        with open(HISTORIAL_FILE, "w", encoding="utf-8") as f:
            json.dump(historial, f, ensure_ascii=False, indent=4)
    except Exception as e:
        st.error(f"Error al guardar historial local: {e}")

# Inicialización de estados de Streamlit
if "historial_apuestas" not in st.session_state:
    st.session_state.historial_apuestas = cargar_historial_local()

if "datos_cargados" not in st.session_state:
    st.session_state.datos_cargados = {}

if "ha_consultado" not in st.session_state:
    st.session_state.ha_consultado = False

if "claves_auto" not in st.session_state:
    st.session_state.claves_auto = set()

if "version_ticket" not in st.session_state:
    st.session_state.version_ticket = 0

if "versiones_partidos" not in st.session_state:
    st.session_state.versiones_partidos = {}

# Diccionarios de configuración y mapeo API (The Odds API)
API_KEY = st.sidebar.text_input("🔑 API Key (The Odds API):", type="password", value="TU_API_KEY_AQUI")

todas_las_ligas = {
    "La Liga (España)": "soccer_spain_la_liga",
    "Premier League (Inglaterra)": "soccer_epl",
    "Serie A (Italia)": "soccer_italy_serie_a",
    "Bundesliga (Alemania)": "soccer_germany_bundesliga",
    "Ligue 1 (Francia)": "soccer_france_ligue1",
    "Champions League": "soccer_uefa_champs_league",
    "Liga MX (México)": "soccer_mexico_ligamx",
    "Primera División (Argentina)": "soccer_argentina_primera_division",
    "Brasileirao (Brasil)": "soccer_brazil_campeonato"
}

diccionario_mercados = {
    "1X2 (Ganador)": "h2h",
    "Doble Oportunidad": "h2h",
    "Ambos Anotan (BTTS)": "btts",
    "Goles Totales (Over/Under 2.5)": "totals"
}

# ==========================================
# MENÚ LATERAL: CONTROL GLOBAL
# ==========================================
st.sidebar.header("⚙️ Filtros de Control Global")

if st.session_state.ha_consultado:
    st.sidebar.success("📊 Consulta Activa")
else:
    st.sidebar.info("🔑 No consultado")

ligas_sels = st.sidebar.multiselect("Selecciona los Torneos a Analizar:", list(todas_las_ligas.keys()))
mercados_sels = st.sidebar.multiselect("Mercados de Análisis:", list(diccionario_mercados.keys()), default=["1X2 (Ganador)"])
rango_h = st.sidebar.selectbox("Rango Temporal:", ["24 Horas", "48 Horas", "72 Horas"], index=1)
limite_h = int(rango_h.split()[0])

monto_inversion = st.sidebar.number_input("Inversión Base ($):", min_value=1.0, value=10.0, step=1.0)
consultar = st.sidebar.button("🔍 Consultar Radar Múltiple", type="primary", use_container_width=True)

st.sidebar.markdown("---")
num_eventos_auto = st.sidebar.slider("Eventos para el Generador Automático:", min_value=1, max_value=10, value=3)
generar_auto = st.sidebar.button("🎯 Auto-Selección Inteligente", use_container_width=True)

# Pestañas Principales de la Aplicación
pestana_radar, pestana_historial = st.tabs(["⚽ Radar Global & Parlays", "📊 Auditoría Financiera & ROI"])

def consultar_api_odds(sport_key, market_key="h2h"):
    if API_KEY == "TU_API_KEY_AQUI" or not API_KEY:
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {"apiKey": API_KEY, "regions": "eu", "markets": market_key, "oddsFormat": "decimal"}
    try:
        r = requests.get(url, params=params)
        if r.status_code == 200: return r.json()
    except: pass
    return []

def consultar_api_odds_evento(sport_key, event_id, market_key="btts"):
    if API_KEY == "TU_API_KEY_AQUI" or not API_KEY:
        return None
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/events/{event_id}/odds/"
    params = {"apiKey": API_KEY, "regions": "eu", "markets": market_key, "oddsFormat": "decimal"}
    try:
        r = requests.get(url, params=params)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def filtrar_partidos_por_fecha(partidos, horas):
    ahora = datetime.utcnow()
    limite = ahora + timedelta(hours=horas)
    filtrados = []
    for p in partidos:
        try:
            f_partido = datetime.strptime(p['commence_time'], "%Y-%m-%dT%H:%M:%SZ")
            if ahora <= f_partido <= limite: filtrados.append(p)
        except: pass
    return filtrados

def procesar_e_inyectar_mercado(raw_data, mercado, horas, nombre_liga, diccionario_consolidador):
    eventos_validos = filtrar_partidos_por_fecha(raw_data, horas)
    for evento in eventos_validos:
        partido_id = evento['id']
        home = evento['home_team']
        away = evento['away_team']
        fecha_local = datetime.strptime(evento['commence_time'], "%Y-%m-%dT%H:%M:%SZ") - timedelta(hours=5)
        
        max_cuotas, max_bookies, betano_cuotas = {}, {}, {}
        
        if mercado == "1X2 (Ganador)":
            opciones = ["Local", "Empate", "Visitante"]
            for bookie in evento.get('bookmakers', []):
                b_name = bookie['title']
                for market in bookie.get('markets', []):
                    if market['key'] == 'h2h':
                        for outcomes in market.get('outcomes', []):
                            oc_name = outcomes['name']
                            cuota = float(outcomes['price'])
                            idx_op = 0 if oc_name == home else (2 if oc_name == away else 1)
                            op_label = opciones[idx_op]
                            if b_name.lower() == "betano": betano_cuotas[op_label] = cuota
                            if cuota > max_cuotas.get(op_label, 0):
                                max_cuotas[op_label] = cuota
                                max_bookies[op_label] = b_name
                                
        elif mercado == "Doble Oportunidad":
            c_home, c_draw, c_away = 0, 0, 0
            b_h, b_d, b_a = "", "", ""
            for bookie in evento.get('bookmakers', []):
                for market in bookie.get('markets', []):
                    if market['key'] == 'h2h':
                        for outcomes in market.get('outcomes', []):
                            p = float(outcomes['price'])
                            if outcomes['name'] == home and p > c_home: c_home, b_h = p, bookie['title']
                            elif outcomes['name'] == away and p > c_away: c_away, b_a = p, bookie['title']
                            elif outcomes['name'] == "Draw" and p > c_draw: c_draw, b_d = p, bookie['title']
            
            if c_home > 0 and c_draw > 0 and c_away > 0:
                max_cuotas["1X (Local o Empate)"] = round((c_home * c_draw) / (c_home + c_draw), 2)
                max_bookies["1X (Local o Empate)"] = f"Sintética ({b_h}/{b_d})"
                max_cuotas["12 (Local o Visitante)"] = round((c_home * c_away) / (c_home + c_away), 2)
                max_bookies["12 (Local o Visitante)"] = f"Sintética ({b_h}/{b_a})"
                max_cuotas["X2 (Visitante o Empate)"] = round((c_away * c_draw) / (c_away + c_draw), 2)
                max_bookies["X2 (Visitante o Empate)"] = f"Sintética ({b_a}/{b_d})"
                
        elif mercado == "Ambos Anotan (BTTS)":
            for bookie in evento.get('bookmakers', []):
                b_name = bookie['title']
                for market in bookie.get('markets', []):
                    if market['key'] == 'btts':
                        for outcomes in market.get('outcomes', []):
                            op_label = "Sí" if outcomes['name'].lower() == "yes" else "No"
                            cuota = float(outcomes['price'])
                            if b_name.lower() == "betano": betano_cuotas[op_label] = cuota
                            if cuota > max_cuotas.get(op_label, 0):
                                max_cuotas[op_label] = cuota
                                max_bookies[op_label] = b_name
                                
        elif mercado == "Goles Totales (Over/Under 2.5)":
            for bookie in evento.get('bookmakers', []):
                b_name = bookie['title']
                for market in bookie.get('markets', []):
                    if market['key'] == 'totals':
                        for outcomes in market.get('outcomes', []):
                            if outcomes.get('point') == 2.5:
                                op_label = "Más 2.5" if outcomes['name'].lower() == "over" else "Menos 2.5"
                                cuota = float(outcomes['price'])
                                if b_name.lower() == "betano": betano_cuotas[op_label] = cuota
                                if cuota > max_cuotas.get(op_label, 0):
                                    max_cuotas[op_label] = cuota
                                    max_bookies[op_label] = b_name

        value_bets = {}
        if max_cuotas:
            sum_inversas = sum([1.0 / c for c in max_cuotas.values()])
            for opcion, cuota in max_cuotas.items():
                prob_real = ((1.0 / cuota) / sum_inversas) * 100
                ev = (cuota / (100.0 / prob_real)) - 1.0
                value_bets[opcion] = {"prob_real": prob_real, "es_value": ev > 0.02, "ev": ev}
                
            if partido_id not in diccionario_consolidador:
                diccionario_consolidador[partido_id] = {
                    "id": partido_id, "liga_origen": nombre_liga,
                    "fecha_str": fecha_local.strftime("%d/%m/%Y - %H:%M"),
                    "local": home, "visitante": away, "mercados": {}
                }
            diccionario_consolidador[partido_id]["mercados"][mercado] = {
                "max_cuotas": max_cuotas, "max_bookies": max_bookies,
                "betano_cuotas": betano_cuotas, "value_bets": value_bets
            }

with pestana_radar:
    st.title("⚽ Radar Avanzado Multi-Mercado Global")

    if consultar:
        if len(ligas_sels) > 0 and len(mercados_sels) > 0:
            st.cache_data.clear()
            consolidador = {}
            st.session_state.ha_consultado = True
            mercados_featured = [m for m in mercados_sels if diccionario_mercados[m] in ("h2h", "totals")]
            pidio_doble_oportunidad = "Doble Oportunidad" in mercados_sels
            pidio_btts = "Ambos Anotan (BTTS)" in mercados_sels

            for liga in ligas_sels:
                sport_key = todas_las_ligas[liga]
                for m_sel in mercados_featured:
                    procesar_e_inyectar_mercado(consultar_api_odds(sport_key, market_key=diccionario_mercados[m_sel]), m_sel, limite_h, liga, consolidador)
                if pidio_doble_oportunidad:
                    procesar_e_inyectar_mercado(consultar_api_odds(sport_key, market_key="h2h"), "Doble Oportunidad", limite_h, liga, consolidador)
                if pidio_btts:
                    for partido_base in filtrar_partidos_por_fecha(consultar_api_odds(sport_key, market_key="h2h"), limite_h):
                        datos_evento = consultar_api_odds_evento(sport_key, partido_base['id'], "btts")
                        if datos_evento: procesar_e_inyectar_mercado([datos_evento], "Ambos Anotan (BTTS)", limite_h, liga, consolidador)

            st.session_state.datos_cargados = consolidador
            st.session_state.claves_auto = set()
            st.session_state.version_ticket += 1
        else:
            st.warning("Elige al menos una liga y un mercado antes de consultar.")

    dict_partidos = st.session_state.datos_cargados

    if generar_auto:
        if not dict_partidos:
            st.error("❌ Primero debes hacer clic en '🔍 Consultar Radar Múltiple' para cargar datos.")
        else:
            bolsa = []
            for p_id, part in dict_partidos.items():
                for nombre_m, m_info in part['mercados'].items():
                    for opcion, val_data in m_info['value_bets'].items():
                        bolsa.append({"clave": f"ap_{part['id']}_{nombre_m}_{opcion}", "prob_real": val_data['prob_real']})
            bolsa = sorted(bolsa, key=lambda x: x['prob_real'], reverse=True)
            k_seleccion = min(len(bolsa), num_eventos_auto)
            if k_seleccion > 0:
                st.session_state.claves_auto = set([x['clave'] for x in bolsa[:k_seleccion]])
                st.session_state.version_ticket += 1
                st.success(f"🎯 Marcados automáticamente los {k_seleccion} eventos.")

    apuestas_seleccionadas = []

    if not st.session_state.ha_consultado:
        st.info("💡 **Sistema en espera de instrucciones**. Selecciona filtros y presiona 'Consultar Radar Múltiple'.")
    elif st.session_state.ha_consultado and not dict_partidos:
        st.warning("⚠️ No se encontraron partidos activos o cuotas disponibles.")
    else:
        busqueda_equipo = st.text_input("🔍 Buscador rápido por nombre de equipo:", "").strip().lower()
        dict_partidos_filtrados = {p_id: p for p_id, p in dict_partidos.items() if busqueda_equipo in p['local'].lower() or busqueda_equipo in p['visitante'].lower()}

        col_izquierda, col_derecha = st.columns([6.5, 3.5])

        with col_izquierda:
            st.subheader(f"📋 Eventos Consolidados Encontrados ({len(dict_partidos_filtrados)})")
            if st.session_state.claves_auto or dict_partidos_filtrados:
                if st.button("🧹 Limpiar todas las casillas marcadas"):
                    st.session_state.claves_auto = set()
                    st.session_state.version_ticket += 1
                    st.rerun()

            v_ticket = st.session_state.version_ticket
            ligas_con_datos = list(set([p['liga_origen'] for p in dict_partidos_filtrados.values()]))
            
            if ligas_con_datos:
                pestanas_ligas = st.tabs(ligas_con_datos)
                for p_idx, liga_pestaña in enumerate(ligas_con_datos):
                    with pestanas_ligas[p_idx]:
                        for part in [p for p in dict_partidos_filtrados.values() if p['liga_origen'] == liga_pestaña]:
                            if part['id'] not in st.session_state.versiones_partidos: st.session_state.versiones_partidos[part['id']] = 0
                            v_partido = st.session_state.versiones_partidos[part['id']]

                            with st.container(border=True):
                                col_borrar, col_info = st.columns([0.6, 5.4])
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

                                                    chk = st.checkbox(f"{plantilla_opcion} ({cuota_m}) {lbl_val}", value=marcado_inicial, key=f"render_{clave_base}_vp{v_partido}_vt{v_ticket}")
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

        with col_derecha:
            st.subheader("🎟️ Configuración de Parlay")
            if apuestas_seleccionadas:
                cuota_acumulada = 1.0
                texto_whatsapp = "🚀 *TICKET PARLAY SUGERIDO DESDE RADAR GLOBAL* 🚀\n\n"
                with st.container(border=True):
                    for ap in apuestas_seleccionadas:
                        cuota_acumulada *= float(ap['cuota'])
                        st.markdown(f"✔️ **{ap['evento']}**<br><small>{ap['liga']}</small><br>➔ `{ap['seleccion']}` | *{ap['mercado']}* (x{ap['cuota']})", unsafe_allow_html=True)
                        texto_whatsapp += f"⚽ *{ap['evento']}*\n🏆 {ap['liga']}\n🎯 {ap['mercado']}: *{ap['seleccion']}*\n\n"

                    st.markdown("---")
                    ganancia_neta = (cuota_acumulada * monto_inversion) - monto_inversion
                    st.metric("Cuota Final", f"x{round(cuota_acumulada, 2)}")
                    st.metric("Ganancia Neta", f"${round(ganancia_neta, 2)}")

                    if st.button("💾 Registrar y Enviar al Historial", type="primary", use_container_width=True):
                        st.session_state.historial_apuestas.append({
                            "Fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                            "Detalles": f"{len(apuestas_seleccionadas)} markets combinados",
                            "Market": "Multi-Mercado", "Cuota": cuota_acumulada,
                            "Inversión": monto_inversion, "Estado": "Pendiente", "Ganancia Potencial": ganancia_neta
                        })
                        guardar_historial_local(st.session_state.historial_apuestas)
                        st.session_state.version_ticket += 1
                        st.session_state.claves_auto = set()
                        st.toast("¡Apuesta registrada exitosamente!", icon="💾")
                        st.rerun()

                    msg_encoded = urllib.parse.quote(texto_whatsapp + f"📈 Cuota Final: x{round(cuota_acumulada, 2)}")
                    st.markdown(f'<a href="https://api.whatsapp.com/send?text={msg_encoded}" target="_blank" style="text-decoration:none;"><button style="border:none; background-color:#25D366; color:white; padding:10px 14px; border-radius:8px; font-size:16px; font-weight:bold; width:100%; height:43px; cursor:pointer; display:flex; align-items:center; justify-content:center; gap:8px;">📲 Compartir en WhatsApp</button></a>', unsafe_allow_html=True)
            else:
                st.info("Marca las casillas de las cuotas a la izquierda para armar tu ticket aquí en tiempo real.")

# ==========================================
# VISTA: PESTAÑA 2 - AUDITORÍA & ROI (CON ELIMINAR INDIVIDUAL)
# ==========================================
with pestana_historial:
    st.title("📊 Módulo de Auditoría Financiera Avanzada")

    if st.session_state.historial_apuestas:
        df_historial = pd.DataFrame(st.session_state.historial_apuestas)

        col_acc1, col_acc2 = st.columns([2, 6])
        with col_acc1:
            if st.button("🚨 Borrar Todo el Historial", type="secondary", use_container_width=True):
                st.session_state.historial_apuestas = []
                guardar_historial_local([])
                st.toast("Historial eliminado por completo", icon="🗑️")
                st.rerun()

        st.markdown("---")
        st.subheader("📝 Modificar Resultados Recientes")
        
        # Iteramos a la inversa para mostrar el más reciente arriba
        indices_reversos = list(range(len(st.session_state.historial_apuestas)))[::-1]
        for idx in indices_reversos:
            fila = st.session_state.historial_apuestas[idx]
            
            with st.container(border=True):
                col_d, col_est, col_del = st.columns([5.5, 2.5, 1])
                with col_d:
                    st.markdown(f"🆔 **Ticket #{idx+1}** ({fila['Fecha']})<br>Inversión: `${fila['Inversión']}` | Cuota: `x{round(fila['Cuota'],2)}`<br>✨ {fila['Detalles']}", unsafe_allow_html=True)
                with col_est:
                    opciones_resultado = ["Pendiente", "Ganado", "Perdido"]
                    index_actual = opciones_resultado.index(fila['Estado']) if fila['Estado'] in opciones_resultado else 0
                    nuevo_estado = st.selectbox("Resultado:", opciones_resultado, index=index_actual, key=f"estado_{idx}")
                    
                    if nuevo_estado != fila['Estado']:
                        st.session_state.historial_apuestas[idx]['Estado'] = nuevo_estado
                        guardar_historial_local(st.session_state.historial_apuestas)
                        st.rerun()
                with col_del:
                    st.write("") # Pequeño margen estético
                    # NUEVO: Botón de eliminación individual
                    if st.button("🗑️", key=f"del_ticket_{idx}", help="Eliminar este ticket permanentemente", use_container_width=True):
                        st.session_state.historial_apuestas.pop(idx)
                        guardar_historial_local(st.session_state.historial_apuestas)
                        st.toast(f"Ticket #{idx+1} eliminado correctamente", icon="🗑️")
                        st.rerun()

        # Recalcular métricas vivas globales basándose en el estado actualizado de la lista
        df_actualizado = pd.DataFrame(st.session_state.historial_apuestas)
        if not df_actualizado.empty:
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

            df_actualizado['Ganancia_Efectiva'] = df_actualizado.apply(
                lambda r: (r['Inversión']*r['Cuota'] - r['Inversión']) if r['Estado'] == "Ganado" else (-r['Inversión'] if r['Estado'] == "Perdido" else 0), axis=1
            )
            df_actualizado['Rendimiento Acumulado ($)'] = df_actualizado['Ganancia_Efectiva'].cumsum()
            st.dataframe(df_actualizado[['Fecha', 'Detalles', 'Inversión', 'Estado', 'Rendimiento Acumulado ($)']], use_container_width=True)
            
            csv_data = df_actualizado.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Descargar Bitácora Completa (CSV)", data=csv_data, file_name=f"Reporte_Apuestas.csv", mime='text/csv', use_container_width=True)
    else:
        st.info("Aún no tienes apuestas registradas en la bitácora.")
