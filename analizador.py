import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
import urllib.parse

# Configuración de página avanzada
st.set_page_config(page_title="Radar Enterprise Parlay Global", page_icon="⚽", layout="wide")

# Estilos visuales limpios para las métricas de probabilidad
st.markdown("""
    <style>
    .prob-alta { color: #2ecc71; font-weight: bold; }
    .prob-media { color: #f1c40f; font-weight: bold; }
    .prob-baja { color: #e74c3c; font-weight: bold; }
    </style>
""", unsafe_allow_html=True)

# --- TU CONFIGURACIÓN ---
API_KEY = "e6414a3efabaf34994030cd0a8ea88b1"

# --- ESTRUCTURAS DE DATOS ---
ligas_top = {
    "🇪🇺 Champions League (Europa)": "soccer_uefa_champions_league",
    "🏆 Copa Libertadores (CONMEBOL)": "soccer_conmebol_copa_distribuidores",
    "🥈 Copa Sudamericana (CONMEBOL)": "soccer_conmebol_copa_sudamericana"
}

ligas_locales = {
    "🇺🇾 Primera División (Uruguay)": "soccer_uruguay_primera_division",
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
    "🇳🇱 Eredivisie (Países Bajos)": "soccer_netherlands_eredivisie"
}

ligas_locales_ordenadas = dict(sorted(ligas_locales.items()))
todas_las_ligas = {**ligas_top, **ligas_locales_ordenadas}

# Mapeo de mercados múltiples
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
if 'sugerencias_ids' not in st.session_state:
    st.session_state.sugerencias_ids = {}
if 'datos_cargados' not in st.session_state:
    st.session_state.datos_cargados = []
if 'versiones_partidos' not in st.session_state:
    st.session_state.versiones_partidos = {}

# --- NAVEGACIÓN PRINCIPAL ---
pestana_radar, pestana_historial = st.tabs(["🚀 RADAR MULTI-MERCADO & VALUEBETS", "📊 BITÁCORA PRO & AUDITORÍA ROI"])

# --- CACHÉ INTELIGENTE ---
@st.cache_data(ttl=600)  
def consultar_api_odds(sport_key, market_key):
    if not sport_key:
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={API_KEY}&regions=eu&markets={market_key}&oddsFormat=decimal"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            res_json = response.json()
            if res_json and len(res_json) > 0 and any(p.get('bookmakers') for p in res_json):
                return res_json
    except Exception:
        pass
        
    url_fallback = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"
    try:
        resp = requests.get(url_fallback)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []

# --- PROCESADOR INTEGRADO MULTI-MERCADO ---
def procesar_datos_mercado(datos, mercado, limite_horas, nombre_liga):
    lista_limpia = []
    ahora = datetime.now()
    
    if not datos or not isinstance(datos, list):
        return lista_limpia

    for partido in datos:
        home = partido['home_team']
        away = partido['away_team']
        
        fecha_str_limpia = partido['commence_time'].replace('Z', '').split('.')[0]
        try:
            fecha_utc = datetime.strptime(fecha_str_limpia, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
            
        fecha_local = fecha_utc - timedelta(hours=5)
        tiempo_restante = fecha_local - ahora
        horas_para_partido = tiempo_restante.total_seconds() / 3600
        
        if horas_para_partido < -5.0 or horas_para_partido > limite_horas:
            continue
            
        bookmakers = partido.get('bookmakers', [])
        if not bookmakers:
            continue
            
        cuotas_globales = {}
        betano_cuotas = {}
        
        for b in bookmakers:
            b_key = b['key'].lower()
            if not b.get('markets') or len(b['markets']) == 0: continue
            
            mercado_recibido = b['markets'][0]['key']
            outcomes = b['markets'][0]['outcomes']
            
            if mercado_recibido == "double_chance" and mercado == "Doble Oportunidad":
                for o in outcomes:
                    o_name = o['name']
                    if o_name in ["home_draw", "home or draw", "1X"]: o_name = "1X (Local o Empate)"
                    elif o_name in ["away_draw", "away or draw", "X2"]: o_name = "X2 (Visitante o Empate)"
                    elif o_name in ["home_away", "home or away", "12"]: o_name = "12 (Local o Visitante)"
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                    if b_key == "betano": betano_cuotas[o_name] = float(o['price'])
                    
            elif mercado_recibido == "btts" and mercado == "Ambos Anotan (BTTS)":
                for o in outcomes:
                    o_name = "Sí" if o['name'].lower() in ["yes", "sí", "si"] else "No"
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                    if b_key == "betano": betano_cuotas[o_name] = float(o['price'])
                    
            elif mercado_recibido == "totals" and "Goles" in mercado:
                for o in outcomes:
                    point = o.get('point', 2.5)
                    o_name = f"Más de {point}" if o['name'].lower() in ["over", "más"] else f"Menos de {point}"
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                    if b_key == "betano": betano_cuotas[o_name] = float(o['price'])
                    
            elif mercado_recibido == "h2h" and mercado == "1X2 (Ganador)":
                for o in outcomes:
                    o_name = "Local" if o['name'] == home else ("Visitante" if o['name'] == away else "Empate")
                    cuotas_globales.setdefault(o_name, []).append((float(o['price']), b['title']))
                    if b_key == "betano": betano_cuotas[o_name] = float(o['price'])

            elif mercado_recibido == "h2h" and mercado in ["Doble Oportunidad", "Ambos Anotan (BTTS)"]:
                precios_h2h = {o['name']: float(o['price']) for o in outcomes}
                draw_key = None
                for k in precios_h2h.keys():
                    if k not in [home, away]:
                        draw_key = k
                        break
                
                if home in precios_h2h and away in precios_h2h and draw_key is not None:
                    ph, pd, pa = 1 / precios_h2h[home], 1 / precios_h2h[draw_key], 1 / precios_h2h[away]
                    margin = ph + pd + pa
                    ph, pd, pa = ph/margin, pd/margin, pa/margin
                    
                    if mercado == "Doble Oportunidad":
                        c_1x = round(1 / max(0.01, (ph + pd)), 2)
                        c_x2 = round(1 / max(0.01, (pd + pa)), 2)
                        c_12 = round(1 / max(0.01, (ph + pa)), 2)
                        cuotas_globales.setdefault("1X (Local o Empate)", []).append((c_1x, b['title']))
                        cuotas_globales.setdefault("X2 (Visitante o Empate)", []).append((c_x2, b['title']))
                        cuotas_globales.setdefault("12 (Local o Visitante)", []).append((c_12, b['title']))
                        if b_key == "betano":
                            betano_cuotas["1X (Local o Empate)"], betano_cuotas["X2 (Visitante o Empate)"], betano_cuotas["12 (Local o Visitante)"] = c_1x, c_x2, c_12
                            
                    elif mercado == "Ambos Anotan (BTTS)":
                        prob_si = max(0.3, min(0.75, round((0.68 * (ph + pa)), 2)))
                        c_si, c_no = round(1 / prob_si, 2), round(1 / (1 - prob_si), 2)
                        cuotas_globales.setdefault("Sí", []).append((c_si, b['title']))
                        cuotas_globales.setdefault("No", []).append((c_no, b['title']))
                        if b_key == "betano":
                            betano_cuotas["Sí"], betano_cuotas["No"] = c_si, c_no

        max_cuotas, max_bookies, value_bets = {}, {}, {}
        for opcion, tuplas in cuotas_globales.items():
            precios = [t[0] for t in tuplas]
            cuota_promedio = sum(precios) / len(precios) if precios else 1.0
            probabilidad_mercado = 1 / cuota_promedio
            cuota_maxima = max(precios)
            idx_max = precios.index(cuota_maxima)
            bookie_maximo = tuplas[idx_max][1]
            
            max_cuotas[opcion] = cuota_maxima
            max_bookies[opcion] = bookie_maximo
            
            ev = (cuota_maxima * probabilidad_mercado) - 1
            value_bets[opcion] = {
                "ev": ev,
                "prob_real": probabilidad_mercado * 100,
                "es_value": ev > 0.01  
            }
        
        if max_cuotas:
            lista_limpia.append({
                "id": f"{partido['id']}_{mercado.replace(' ', '_')}", # ID único por partido y mercado
                "partido_base_id": partido['id'],
                "liga_origen": nombre_liga,
                "fecha_str": fecha_local.strftime("%d/%m/%Y - %H:%M"),
                "local": home, "visitante": away, "mercado": mercado,
                "max_cuotas": max_cuotas, "max_bookies": max_bookies,
                "betano_cuotas": betano_cuotas, "value_bets": value_bets
            })
            
    return lista_limpia

# ==========================================
# VISTA: PESTAÑA 1 - RADAR Y APUESTAS
# ==========================================
with pestana_radar:
    st.title("⚽ Radar Avanzado Multi-Mercado Global")
    
    col_l, col_m, col_t, col_inv = st.columns([2, 1.5, 1, 1])
    with col_l:
        ligas_sels = st.multiselect("Selecciona los Torneos a Analizar:", list(todas_las_ligas.keys()), default=[])
            
    with col_m:
        # CORREGIDO: Ahora es multiselect para permitir múltiples opciones en simultáneo
        mercados_sels = st.multiselect("Mercados de Análisis:", list(diccionario_mercados.keys()), default=["1X2 (Ganador)"])
        
    with col_t:
        tiempo_sel = st.selectbox("Rango Temporal:", ["24 Horas", "48 Horas", "72 Horas"], index=1)
        limite_h = int(tiempo_sel.split()[0])
        
    with col_inv:
        monto_inversion = st.number_input("Inversión Base ($):", min_value=1.0, value=10.0, step=1.0)
        
    st.markdown(" ")
    if st.button("🔍 Consultar Radar Múltiple", type="primary", use_container_width=True):
        if len(ligas_sels) > 0 and len(mercados_sels) > 0:
            partidos_acumulados = []
            for liga in ligas_sels:
                sport_key = todas_las_ligas[liga]
                for m_sel in mercados_sels:
                    mercado_api = diccionario_mercados[m_sel]
                    raw_data = consultar_api_odds(sport_key, market_key=mercado_api)
                    partidos_liga = procesar_datos_mercado(raw_data, m_sel, limite_h, liga)
                    partidos_acumulados.extend(partidos_liga)
            st.session_state.datos_cargados = partidos_acumulados
            st.session_state.sugerencias_ids = {}
        else:
            st.warning("Elige al menos una liga y un mercado antes de consultar.")
            
    st.markdown("---")
    
    todos_los_partidos = st.session_state.datos_cargados
            
    if not ligas_sels or not mercados_sels:
        st.info("💡 Selecciona ligas y mercados arriba y pulsa 'Consultar Radar Múltiple'.")
    else:
        if todos_los_partidos:
            with st.expander("🤖 Asistente Multi-Torneo: Auto-Parlay Inteligente", expanded=True):
                if st.button("⚡ Autogenerar Parlay Óptimo Cruzado", use_container_width=True):
                    candidatos_parlay = []
                    for part in todos_los_partidos:
                        for opcion, info in part['value_bets'].items():
                            if info['ev'] > 0.005 and info['prob_real'] >= 35.0:
                                candidatos_parlay.append({"partido_id": part['id'], "opcion": opcion, "prob_real": info['prob_real']})
                    
                    if candidatos_parlay:
                        candidatos_parlay = sorted(candidatos_parlay, key=lambda x: x['prob_real'], reverse=True)
                        sugeridos = {}
                        for item in candidatos_parlay[:4]:
                            if item['partido_id'] not in sugeridos:
                                sugeridos[item['partido_id']] = item['opcion']
                        st.session_state.sugerencias_ids = sugeridos
                        st.session_state.version_ticket += 1
                        st.toast("🎯 ¡Parlay óptimo pre-seleccionado con éxito!", icon="⚡")
                        st.rerun()
        
        apuestas_seleccionadas = []
        if todos_los_partidos:
            st.subheader(f"📋 Eventos Consolidados Encontrados ({len(todos_los_partidos)})")
            v_ticket = st.session_state.version_ticket
            
            ligas_con_datos = list(set([p['liga_origen'] for p in todos_los_partidos]))
            pestanas_ligas = st.tabs(ligas_con_datos)
            
            for p_idx, liga_pestaña in enumerate(ligas_con_datos):
                with pestanas_ligas[p_idx]:
                    partidos_filtrados = [p for p in todos_los_partidos if p['liga_origen'] == liga_pestaña]
                    for part in partidos_filtrados:
                        if part['id'] not in st.session_state.versiones_partidos:
                            st.session_state.versiones_partidos[part['id']] = 0
                            
                        v_partido = st.session_state.versiones_partidos[part['id']]
                        
                        with st.container(border=True):
                            col_borrar, col_info, col_opciones = st.columns([0.4, 2.1, 3.5])
                            
                            with col_borrar:
                                if st.button("🗑️", key=f"clear_{part['id']}", help="Limpiar opciones"):
                                    st.session_state.versiones_partidos[part['id']] += 1
                                    if part['id'] in st.session_state.sugerencias_ids:
                                        del st.session_state.sugerencias_ids[part['id']]
                                    st.rerun()
                                    
                            with col_info:
                                st.markdown(f"⚽ **{part['local']} vs {part['visitante']}**")
                                st.caption(f"📊 Mercado: `{part['mercado']}`")
                                st.caption(f"📅 Hora: {part['fecha_str']}")
                                
                            with col_opciones:
                                opciones_disponibles = list(part['max_cuotas'].keys())
                                if part['mercado'] == "1X2 (Ganador)": orden_estricto = ["Local", "Empate", "Visitante"]
                                elif part['mercado'] == "Doble Oportunidad": orden_estricto = ["1X (Local o Empate)", "12 (Local o Visitante)", "X2 (Visitante o Empate)"]
                                elif part['mercado'] == "Ambos Anotan (BTTS)": orden_estricto = ["Sí", "No"]
                                else: orden_estricto = sorted(opciones_disponibles, key=lambda x: 0 if "Más" in x else 1)
                                
                                sub_cols = st.columns(len(orden_estricto))
                                for idx, plantilla_opcion in enumerate(orden_estricto):
                                    with sub_cols[idx]:
                                        if plantilla_opcion in opciones_disponibles:
                                            cuota_m = part['max_cuotas'][plantilla_opcion]
                                            casa_m = part['max_bookies'][plantilla_opcion]
                                            info_val = part['value_bets'][plantilla_opcion]
                                            
                                            sugerido_por_ia = st.session_state.sugerencias_ids.get(part['id']) == plantilla_opcion
                                            lbl_val = "🔥 VALOR" if info_val['es_value'] else ""
                                            
                                            chk = st.checkbox(f"{plantilla_opcion} ({cuota_m}) {lbl_val}", value=sugerido_por_ia, key=f"ap_{part['id']}_{plantilla_opcion}_vp{v_partido}_vt{v_ticket}")
                                            c_betano = part['betano_cuotas'].get(plantilla_opcion, "N/A")
                                            p_real = info_val['prob_real']
                                            clase_color = "prob-alta" if p_real >= 60 else ("prob-media" if p_real >= 40 else "prob-baja")
                                            
                                            st.markdown(f"<small>🏠 {casa_m}<br>(Betano: {c_betano})<br>🎯 Prob: <span class='{clase_color}'>{round(p_real,1)}%</span></small>", unsafe_allow_html=True)
                                            
                                            if chk:
                                                apuestas_seleccionadas.append({
                                                    "id_partido": part['id'], "evento": f"{part['local']} vs {part['visitante']}",
                                                    "liga": part['liga_origen'], "mercado": part['mercado'],
                                                    "seleccion": plantilla_opcion, "cuota": cuota_m, "casa": casa_m, "prob_real": p_real
                                                })
                                            
            # --- COMPARTIR PARLAY COMPLETO EN WHATSAPP ---
            if apuestas_seleccionadas:
                st.markdown("---")
                st.header("🎟️ Configuración de Parlay Global")
                
                cuota_acumulada = 1.0
                texto_whatsapp = "🚀 *TICKET PARLAY ENVIADO DESDE RADAR GLOBAL* 🚀\n\n"
                
                for ap in apuestas_seleccionadas:
                    cuota_acumulada *= ap['cuota']
                    st.markdown(f"✔️ **{ap['evento']}** ({ap['liga']}) ➔ `{ap['seleccion']}` | Mercado: *{ap['mercado']}* | Cuota: **{ap['cuota']}**")
                    texto_whatsapp += f"⚽ *{ap['evento']}*\n🏆 {ap['liga']}\n🎯 {ap['mercado']}: *{ap['seleccion']}* (x{ap['cuota']}) - 🏢 {ap['casa']}\n\n"
                    
                ganancia_estimada = cuota_acumulada * monto_inversion
                ganancia_neta = ganancia_estimada - monto_inversion
                
                texto_whatsapp += f"📊 *RESUMEN DEL PARLAY*\n🔹 Eventos combinados: {len(apuestas_seleccionadas)}\n📈 Cuota Final total: *x{round(cuota_acumulada, 2)}*\n💵 Inversión: *${round(monto_inversion, 2)}*\n💰 Ganancia Neta Potencial: *${round(ganancia_neta, 2)}*"
                msg_encoded = urllib.parse.quote(texto_whatsapp)
                
                c_m1, c_m2, c_m3 = st.columns(3)
                c_m1.metric("Cuota Final", f"{round(cuota_acumulada, 2)}")
                c_m2.metric("Retorno Total", f"${round(ganancia_estimada, 2)}")
                c_m3.metric("Ganancia Neta", f"${round(ganancia_neta, 2)}")
                
                col_btn_reg, col_btn_ws = st.columns([1, 1])
                with col_btn_reg:
                    if st.button("💾 Registrar y Enviar Apuesta al Historial", type="primary", use_container_width=True):
                        st.session_state.historial_apuestas.append({
                            "Fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
                            "Detalles": f"{len(apuestas_seleccionadas)} mercados combinados",
                            "Mercado": "Multi-Mercado",
                            "Cuota": cuota_acumulada,
                            "Inversión": monto_inversion,
                            "Estado": "Pendiente",
                            "Ganancia Potencial": ganancia_neta
                        })
                        st.session_state.sugerencias_ids = {}
                        st.session_state.version_ticket += 1
                        st.toast("¡Apuesta registrada exitosamente!", icon="💾")
                        st.rerun()
                        
                with col_btn_ws:
                    # CORREGIDO: Botón verde para mandar el Parlay COMPLETO unificado
                    st.markdown(f'<a href="https://api.whatsapp.com/send?text={msg_encoded}" target="_blank" style="text-decoration:none;"><button style="border:none; background-color:#25D366; color:white; padding:10px 14px; border-radius:8px; font-size:16px; font-weight:bold; width:100%; height:43px; cursor:pointer; display:flex; align-items:center; justify-content:center; gap:8px;">📲 Compartir Parlay Completo en WhatsApp</button></a>', unsafe_allow_html=True)
        else:
            st.warning("Haz clic en 'Consultar Radar Múltiple' para cargar la información.")

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
        
        st.markdown("### 📊 Evolución del Negocio (Métrica Monetaria)")
        df_actualizado['Ganancia_Efectiva'] = df_actualizado.apply(lambda r: (r['Inversión']*r['Cuota'] - r['Inversión']) if r['Estado'] == "Ganado" else (-r['Inversión'] if r['Estado'] == "Perdido" else 0), axis=1)
        df_actualizado['Rendimiento Acumulado ($)'] = df_actualizado['Ganancia_Efectiva'].cumsum()
        
        st.dataframe(df_actualizado[['Fecha', 'Detalles', 'Inversión', 'Estado', 'Rendimiento Acumulado ($)']], use_container_width=True)
                
        st.markdown("### 📋 Registro Total de Operaciones")
        st.dataframe(df_actualizado, use_container_width=True)
        
        csv_data = df_actualizado.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar Bitácora Completa (CSV)", data=csv_data, file_name=f"Reporte_Apuestas.csv", mime='text/csv', use_container_width=True)
    else:
        st.info("Aún no tienes apuestas registradas en la bitácora.")
