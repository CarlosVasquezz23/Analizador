"""Session ledger, explicit settlement and portable backups."""
import json
import pandas as pd
import plotly.express as px
import streamlit as st
from workspace_logic import STATES, settled_profit, ledger_summary, validate_history
from pro_ui import themed_chart


def render_finance(save_history):
    st.title('Bitácora financiera')
    st.caption('Registra resultados y revisa el balance de tus boletos. Conserva una copia descargada: los datos pertenecen a esta sesión.')
    history = st.session_state.historial_apuestas
    if history:
        summary = ledger_summary(history)
        a,b,c,d = st.columns(4)
        a.metric('Balance realizado',f"${summary['net']:,.2f}")
        b.metric('ROI realizado',f"{summary['roi']:.1f}%",help='Beneficio neto sobre inversión de boletos ganados o perdidos. Excluye anulados y pendientes.')
        c.metric('Boletos pendientes',summary['pending'])
        d.metric('Inversión pendiente',f"${summary['exposure']:,.2f}")
        table = pd.DataFrame(history).drop(columns=['Selecciones','Ticket ID'],errors='ignore')
        with st.expander('Actualizar resultados',expanded=False):
            st.caption('Cambia Estado y pulsa Guardar resultados. Las ganancias se calculan con la cuota e inversión registradas.')
            edited = st.data_editor(table,hide_index=True,use_container_width=True,
                disabled=[col for col in table.columns if col != 'Estado'],
                column_config={'Estado':st.column_config.SelectboxColumn(options=STATES,required=True)},key='ledger_editor')
            if st.button('Guardar resultados',type='primary'):
                for index,state in enumerate(edited['Estado']):
                    if state in STATES: history[index]['Estado'] = state
                save_history(history); st.success('Resultados actualizados.'); st.rerun()
        state = st.selectbox('Mostrar boletos',['Todos',*STATES],key='ledger_filter')
        shown = table if state == 'Todos' else table[table.Estado==state]
        st.dataframe(shown,hide_index=True,use_container_width=True)
        settled = [r for r in history if r['Estado'] in ('Ganada','Perdida')]
        if settled:
            curve = pd.DataFrame([{'Boleto':i+1,'Beneficio neto':settled_profit(r)} for i,r in enumerate(settled)])
            curve['Balance acumulado'] = curve['Beneficio neto'].cumsum()
            fig = px.line(curve,x='Boleto',y='Balance acumulado',markers=True,title='Evolución del balance registrado',color_discrete_sequence=['#21cfa4'])
            themed_chart(fig,key='ledger_curve')
        a,b = st.columns(2)
        a.download_button('Exportar historial JSON',json.dumps(history,ensure_ascii=False,indent=2),'bitacora_backup.json','application/json',use_container_width=True)
        b.download_button('Descargar CSV',table.to_csv(index=False).encode('utf-8-sig'),'historial.csv','text/csv',use_container_width=True)
    else:
        st.info('Aún no tienes boletos guardados. Crea uno en Radar y pulsa Guardar Historial.')
    with st.expander('Restaurar un respaldo'):
        backup = st.file_uploader('Selecciona tu archivo JSON',type=['json'])
        st.caption('La importación reemplaza la bitácora de esta sesión.')
        if backup and st.button('Importar respaldo'):
            try:
                imported = validate_history(json.load(backup))
                save_history(imported)
                st.session_state.pop('ledger_editor',None)
                st.rerun()
            except (ValueError,TypeError,KeyError,OverflowError):
                st.error('Respaldo inválido. Usa un JSON exportado desde la bitácora.')
