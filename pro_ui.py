"""Presentation layer for the sports workspace."""
from html import escape
import pandas as pd
import plotly.express as px
import streamlit as st


def apply_theme(light=False):
    bg, card, text, muted, line = ('#f4f7fb','#ffffff','#122139','#53647c','#dbe4ef') if light else ('#0a101c','#111d2f','#f1f5fc','#a6b7d0','#283952')
    st.markdown(f'''<style>
    :root {{ --rg-text-soft:{muted}; }}
    .stApp {{background:{bg}; color:{text};}}
    [data-testid="stSidebar"] {{background:{card}; border-right:1px solid {line};}}
    [data-testid="stHeader"] {{background:transparent;}}
    .block-container {{max-width:1440px; padding:2.2rem 2rem 4rem;}}
    h1,h2,h3 {{letter-spacing:-.035em; color:{text};}}
    .stMain p,.stMain label,[data-testid="stSidebar"] p,[data-testid="stMetricLabel"] p,[data-testid="stMetricValue"] {{color:{text};}}
    input,textarea,[data-baseweb="select"]>div {{background:{card} !important; color:{text} !important;}}
    [data-testid="stCaptionContainer"] p {{color:{muted};}}
    .hero {{padding:1.6rem 1.8rem; background:linear-gradient(115deg,{card},#153b46); border:1px solid {line}; border-radius:22px; margin-bottom:1.5rem;}}
    .hero small {{color:#5ee7c5; font-weight:700; letter-spacing:.16em;}}
    .hero h1 {{color:#f1f5fc; margin:.5rem 0; font-size:2.15rem;}}
    .hero p {{color:#d3e4ee; margin:0; max-width:780px;}}
    .section-eyebrow {{color:{muted}; text-transform:uppercase; letter-spacing:.14em; font-size:.75rem; font-weight:700;}}
    [data-testid="stMetric"] {{background:{card}; border:1px solid {line}; border-radius:16px; padding:1.1rem;}}
    [data-testid="stMetricValue"] {{font-variant-numeric:tabular-nums;}}
    [data-testid="stVerticalBlockBorderWrapper"] {{border-radius:16px;}}
    .stButton button,.stDownloadButton button {{border-radius:10px; min-height:44px;}}
    .stTabs [data-baseweb="tab-list"] {{gap:12px; overflow-x:auto;}}
    .stTabs [data-baseweb="tab"] {{white-space:nowrap; min-height:46px;}}
    .match-header {{font-size:1.15rem; font-weight:700; margin:.65rem 0; color:{text}; overflow-wrap:anywhere;}}
    .liga-chip,.kickoff-chip {{display:inline-block; padding:4px 9px; border-radius:8px; font-size:.72rem; margin:2px; background:{bg}; color:{muted}; border:1px solid {line};}}
    .liga-chip {{color:#10b981;}}
    .ai-summary-box,.empty-state-card,.veredicto-box {{background:{card}; border:1px solid {line}; border-radius:16px; padding:1.3rem; margin-bottom:1rem;}}
    .ticket-item {{padding:.8rem 0; border-bottom:1px solid {line};}}
    .ticket-titulo,.ticket-cuota-tag {{color:#10b981; font-weight:700;}}
    @media(max-width:768px) {{.block-container{{padding:1rem .8rem 3rem;}} .hero{{padding:1.2rem;}} .hero h1{{font-size:1.7rem;}} [data-testid="stHorizontalBlock"]{{flex-wrap:wrap;}} [data-testid="stColumn"]{{min-width:min(100%,260px); flex:1 1 260px;}}}}
    </style>''', unsafe_allow_html=True)


def header():
    st.markdown('''<div class="hero"><small>ANALIZADOR / SPORTS WORKSPACE</small>
    <h1>El juego, desde los datos.</h1><p>Explora eventos, compara cuotas y estudia escenarios en un solo lugar.</p></div>''', unsafe_allow_html=True)


def rows_for(events):
    return [dict(Evento=f"{p['local']} vs {p['visitante']}", Liga=p['liga_origen'], Mercado=market,
                 Selección=choice, Cuota=value['max_cuotas'][choice], Casa=value['max_bookies'][choice],
                 EV=info['ev'] * 100, Probabilidad=info['prob_real'])
            for p in events.values() for market,value in p['mercados'].items()
            for choice,info in value['value_bets'].items()]


def dashboard(events):
    rows = rows_for(events)
    values = sum(r['EV'] > 2 for r in rows)
    columns = st.columns(4)
    for col,label,value in zip(columns, ['Eventos analizados','Ligas con datos','Selecciones +EV','En tu boleto'],
                               [len(events),len({p['liga_origen'] for p in events.values()}),values,len(st.session_state.ticket_persistente)]):
        col.metric(label, value)
    st.caption('EV basado en consenso de cuotas; no es una predicción validada. Horarios de Ecuador (UTC−5).')
    if not events:
        st.info('Comienza por elegir torneos y mercados en Filtros de escaneo. No se muestran datos de ejemplo como resultados reales.')
        return
    frame = pd.DataFrame(rows)
    left,right = st.columns([3,2])
    with left:
        counts = pd.DataFrame([{'Liga':p['liga_origen']} for p in events.values()]).value_counts('Liga').reset_index(name='Eventos')
        fig = px.bar(counts, x='Eventos', y='Liga', orientation='h', title='Cobertura por competición', color_discrete_sequence=['#10b981'])
        st.plotly_chart(fig, use_container_width=True, key='coverage_chart')
    with right:
        fig = px.histogram(frame, x='Cuota', nbins=20, title='Distribución de mejores cuotas', color_discrete_sequence=['#60a5fa'])
        st.plotly_chart(fig, use_container_width=True, key='odds_chart')
    if st.session_state.get('ultima_consulta'):
        st.caption('Último escaneo: ' + st.session_state.ultima_consulta)


def opportunity_table(events, positive_only=False):
    frame = pd.DataFrame(rows_for(events))
    if frame.empty:
        st.info('Escanea el mercado para cargar eventos.'); return
    if positive_only: frame = frame[frame.EV > 2]
    if frame.empty:
        st.info('No hay selecciones con EV superior al 2 % en los datos cargados.'); return
    frame = frame.sort_values('EV', ascending=False)
    st.dataframe(frame, hide_index=True, use_container_width=True,
                 column_config={'EV':st.column_config.NumberColumn('EV (%)',format='%.2f'),
                                'Probabilidad':st.column_config.ProgressColumn('Consenso (%)',min_value=0,max_value=100,format='%.1f%%')})
    st.download_button('Descargar comparación CSV', frame.to_csv(index=False).encode('utf-8-sig'), 'comparacion.csv','text/csv')
