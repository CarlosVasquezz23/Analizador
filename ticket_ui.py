"""Ticket interactions shared by Radar and its dedicated workspace."""
import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape
import streamlit as st
from workspace_logic import ticket_totals


def remove_selection(key):
    st.session_state.ticket_persistente.pop(key, None)
    st.session_state[key] = False


def clear_ticket():
    for key in list(st.session_state.ticket_persistente):
        remove_selection(key)


def render_ticket(save_history):
    st.subheader('Tu boleto')
    selected = list(st.session_state.ticket_persistente.values())
    if not selected:
        st.info('Tu boleto está vacío. Añade selecciones desde las tarjetas del Radar.')
        return
    with st.container(border=True):
        st.caption(f'{len(selected)} SELECCIONES · CUOTAS DEL ÚLTIMO ESCANEO')
        for key,selection in list(st.session_state.ticket_persistente.items()):
            st.markdown(f"<div class='ticket-item'><b>{escape(selection['evento'])}</b><br><span class='ticket-cuota-tag'>{escape(selection['seleccion'])} · @{selection['cuota']:g}</span><br><small>{escape(selection['casa'])}</small></div>",unsafe_allow_html=True)
            st.button('Quitar selección',key='remove_'+key,on_click=remove_selection,args=(key,),width="stretch")
        st.button('Vaciar boleto',on_click=clear_ticket,width="stretch")
        stake = st.number_input('Inversión ($):',min_value=1.,value=float(st.session_state.get('monto_inversion_base',10.)),step=1.,key='ticket_stake')
        try: totals = ticket_totals(selected,stake)
        except ValueError as error:
            st.error(str(error)); return
        a,b = st.columns(2)
        a.metric('Cuota total',f"@{totals['odds']:.2f}")
        b.metric('Retorno bruto',f"${totals['gross']:,.2f}")
        st.metric('Ganancia neta potencial',f"${totals['net']:,.2f}",help='Retorno bruto menos la inversión. Solo se obtiene si el boleto gana.')
        st.caption(f'Incluye ${stake:.2f} de inversión en el retorno bruto.')
        if not totals['independent']:
            st.warning('Hay selecciones del mismo evento. No se calcula probabilidad combinada porque pueden estar correlacionadas.')
        elif totals['known']:
            st.metric('Consenso combinado estimado',f"{totals['probability']*100:.2f}%",help='Multiplica probabilidades de consenso suponiendo independencia. No es una predicción deportiva validada.')
            probability = totals['probability']
            if 0 < probability < 1:
                sharpe = (probability - 1/totals['odds']) / (probability*(1-probability))**0.5
                with st.expander('Indicadores del boleto'):
                    st.metric('Sharpe estimado',f'{sharpe:.3f}',help='Retorno esperado dividido por la desviación del retorno, suponiendo independencia y usando el consenso de cuotas.')
        else:
            st.caption('Consenso combinado no disponible para este boleto.')
        canonical = [{key:value for key,value in selection.items() if key != 'stale'} for selection in selected]
        canonical.sort(key=lambda selection:(selection['evento'],selection['mercado'],selection['seleccion']))
        fingerprint = hashlib.sha256(json.dumps([canonical,stake],sort_keys=True,default=str).encode()).hexdigest()
        stale = any(s.get('stale') for s in selected)
        if stale: st.warning('Hay selecciones sin cuota en el último escaneo. Quítalas o vuelve a consultar antes de guardar.')
        saved = any(r.get('Ticket ID') == fingerprint for r in st.session_state.historial_apuestas)
        if st.button('Guardar Historial',type='primary',width="stretch",disabled=saved or stale):
            st.session_state.historial_apuestas.append({
                'Ticket ID':fingerprint,'Fecha':datetime.now(ZoneInfo('America/Guayaquil')).strftime('%d/%m/%Y %H:%M'),
                'Detalles':' | '.join(f"{p['evento']}: {p['seleccion']}" for p in selected),
                'Liga':', '.join(sorted({p['liga'] for p in selected})), 'Market':'Combinada' if len(selected)>1 else selected[0]['mercado'],
                'Cuota':totals['odds'],'Inversión':stake,'Estado':'Pendiente','Ganancia Potencial':totals['net'],
                'Selecciones':selected})
            save_history(st.session_state.historial_apuestas)
            st.success('Boleto guardado en tu bitácora.')
            st.rerun()
        if saved: st.caption('Este boleto ya está guardado. Cambia la selección o el importe para registrar otro.')
        st.download_button('Descargar boleto JSON',json.dumps(selected,ensure_ascii=False,indent=2),'boleto.json','application/json',width="stretch")
