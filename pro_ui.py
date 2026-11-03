"""Visual system and dashboard presentation for Analizador."""
from html import escape
import pandas as pd
import plotly.express as px
import streamlit as st


def go_to(view):
    st.session_state.navigation = view


def apply_theme(light=False):
    bg,card,text,muted,line = ('#f3f6fa','#ffffff','#14253d','#50647c','#dce5ef') if light else ('#080f1c','#101d30','#eaf1fc','#9bafca','#233650')
    st.markdown(f'''<style>
    :root {{ --rg-text-soft:{muted}; --surface:{card}; --stroke:{line}; --ink:{text}; }}
    .stApp {{background:{bg};color:{text};font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;}}
    [data-testid="stSidebar"] {{background:{card};border-right:1px solid {line};}}
    [data-testid="stHeader"] {{background:{bg}e8;}}
    .block-container {{max-width:1450px;padding:2rem 2.2rem 4rem;}}
    h1,h2,h3,h4 {{color:{text};letter-spacing:-.035em;}}
    .stMain p,.stMain label,[data-testid="stSidebar"] p,[data-testid="stMetricLabel"] p,[data-testid="stMetricValue"] {{color:{text};}}
    input,textarea,[data-baseweb="select"]>div {{background:{card} !important;color:{text} !important;}}
    input::placeholder,textarea::placeholder {{color:{muted} !important;opacity:1;}}
    [data-testid="stCaptionContainer"] p {{color:{muted};}}
    button:focus-visible,input:focus-visible,textarea:focus-visible {{outline:3px solid #21cfa4 !important;outline-offset:3px;}}
    .hero {{position:relative;overflow:hidden;padding:1.7rem 2rem;background:linear-gradient(115deg,#102135,#123d42);border:1px solid #2b5262;border-radius:20px;margin-bottom:1.25rem;}}
    .hero:after {{content:"";position:absolute;right:-35px;top:-80px;width:300px;height:300px;border:1px solid #43918044;border-radius:50%;box-shadow:0 0 0 55px #43918011,0 0 0 110px #43918009;pointer-events:none;}}
    .hero small {{color:#74ecd0;font-weight:700;letter-spacing:.17em;font-size:.68rem;}}
    .hero h1 {{position:relative;color:#f4f8ff;font-size:2rem;line-height:1.2;margin:.6rem 0;z-index:1;}}
    .hero p {{position:relative;color:#c0d5e4;font-size:.94rem;max-width:640px;margin:0;z-index:1;}}
    .brand {{font-size:1.3rem;font-weight:800;letter-spacing:-.04em;color:{text};padding:.4rem 0 1rem;}}
    .brand span {{color:#21cfa4;}}
    .brand-sub {{font-size:.68rem;letter-spacing:.14em;color:{muted};font-weight:500;}}
    .step-label {{font-size:.68rem;color:#21cfa4;text-transform:uppercase;letter-spacing:.14em;font-weight:750;}}
    .step-card {{background:{card};border:1px solid {line};border-radius:16px;padding:1.2rem;min-height:155px;}}
    .step-card h4 {{font-size:1rem;margin:.6rem 0;}}
    .step-card p {{color:{muted};font-size:.85rem;line-height:1.5;margin:0;}}
    [data-testid="stMetric"] {{background:{card};border:1px solid {line};border-radius:15px;padding:1rem 1.2rem;min-height:112px;}}
    [data-testid="stMetricValue"] {{font-variant-numeric:tabular-nums;font-size:1.9rem;}}
    [data-testid="stMetricLabel"] {{font-size:.8rem;}}
    .stButton button,.stDownloadButton button {{border-radius:9px;min-height:43px;font-weight:600;transition:border-color .15s;}}
    .stButton button:hover,.stDownloadButton button:hover {{border-color:#21cfa4;}}
    [data-testid="stVerticalBlockBorderWrapper"], [data-testid="stVerticalBlockBorderWrapper"]>div {{border-radius:16px;}}
    .event-preview {{padding:1rem 1.2rem;background:{card};border:1px solid {line};border-radius:14px;margin-bottom:.65rem;}}
    .event-preview b {{display:block;font-size:.96rem;margin:.45rem 0;color:{text};}}
    .event-preview small {{color:{muted};font-size:.75rem;}}
    .match-header {{display:flex;align-items:center;gap:.7rem;font-size:1.12rem;font-weight:750;margin:1rem 0;color:{text};overflow-wrap:anywhere;}}
    .team-mark {{display:inline-flex;align-items:center;justify-content:center;width:35px;height:35px;border-radius:10px;background:{bg};border:1px solid {line};font-size:.66rem;color:#21cfa4;flex-shrink:0;}}
    .versus {{font-size:.7rem;color:{muted};font-weight:500;}}
    .liga-chip,.kickoff-chip {{display:inline-block;padding:4px 9px;border-radius:6px;font-size:.68rem;margin:2px;background:{bg};color:{muted};border:1px solid {line};}}
    .liga-chip {{color:{'#0c7560' if light else '#63dfc0'};}}
    .ai-summary-box,.empty-state-card,.veredicto-box {{background:{card};border:1px solid {line};border-radius:14px;padding:1.1rem;margin-bottom:1rem;}}
    .ticket-item {{padding:.8rem 0 .4rem;font-size:.88rem;}}
    .ticket-item small {{color:{muted};}}
    .ticket-cuota-tag {{color:{'#0a775b' if light else '#63dfc0'};font-weight:650;}}
    .stTabs [data-baseweb="tab-list"] {{gap:12px;overflow-x:auto;}}
    .stTabs [data-baseweb="tab"] {{white-space:nowrap;min-height:44px;}}
    @media(max-width:768px) {{.block-container{{padding:1rem .85rem 3rem;}} .hero{{padding:1.25rem;}} .hero h1{{font-size:1.55rem;}} .hero:after{{opacity:.35;}} [data-testid="stHorizontalBlock"]{{flex-wrap:wrap;}} [data-testid="stColumn"]{{min-width:min(100%,240px);flex:1 1 240px;}} [data-testid="stMetric"]{{min-height:100px;}}}}
    @media(max-width:768px) {{.st-key-quicknav [data-testid="stColumn"]{{min-width:0;flex:1 1 0;}} .st-key-dashboard_metrics [data-testid="stColumn"]{{min-width:0;flex:1 1 42%;}} .st-key-dashboard_metrics [data-testid="stMetricValue"]{{font-size:1.5rem;}} .st-key-dashboard_metrics [data-testid="stMetric"]{{padding:.85rem;}}}}
    @media(prefers-reduced-motion:reduce) {{*{{transition:none !important;scroll-behavior:auto !important;}}}}
    </style>''',unsafe_allow_html=True)


def header():
    st.markdown('''<div class="hero"><small>ANALIZADOR · CENTRO DEPORTIVO</small>
    <h1>Una visión más clara del juego.</h1><p>Tu espacio para explorar eventos, comparar mercados y llevar el control de cada selección.</p></div>''',unsafe_allow_html=True)


def quick_navigation():
    with st.container(key='quicknav'):
        for col,label,view in zip(st.columns(3),['Resumen','Radar','Mi boleto'],['Dashboard','Radar Multi-Mercado','Generador Visual']):
            col.button(label,on_click=go_to,args=(view,),use_container_width=True,key='quick_'+view)


def themed_chart(fig, key=None):
    light = st.session_state.get('tema_visual') == 'Claro'
    text = '#243a54' if light else '#c6d8ef'
    fig.update_layout(template='plotly_white' if light else 'plotly_dark',paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
                      font=dict(family='Inter, system-ui, sans-serif',color=text,size=12),
                      margin=dict(l=12,r=12,t=55,b=15),height=330,title_font_size=16,
                      hoverlabel=dict(font_size=13),legend=dict(orientation='h',y=-.2))
    fig.update_xaxes(gridcolor='#dce5ef' if light else '#233650',zeroline=False)
    fig.update_yaxes(gridcolor='#dce5ef' if light else '#233650',zeroline=False)
    st.plotly_chart(fig,use_container_width=True,key=key,config={'displaylogo':False,'scrollZoom':False,'modeBarButtonsToRemove':['lasso2d','select2d']})


def rows_for(events):
    return [dict(Evento=f"{p['local']} vs {p['visitante']}",Liga=p['liga_origen'],Mercado=market,
                 Selección=choice,Cuota=value['max_cuotas'][choice],Casa=value['max_bookies'][choice],
                 EV=info['ev']*100,Probabilidad=info['prob_real'] if info['prob_real'] else None)
            for p in events.values() for market,value in p['mercados'].items() for choice,info in value['value_bets'].items()]


def dashboard(events):
    rows = rows_for(events)
    with st.container(key='dashboard_metrics'):
        cols = st.columns(4)
        for col,label,value in zip(cols,['Eventos en radar','Competiciones','Selecciones +EV','En tu boleto'],
                                   [len(events),len({p['liga_origen'] for p in events.values()}),sum(r['EV']>2 for r in rows),len(st.session_state.ticket_persistente)]):
            col.metric(label,value)
    st.caption('Horarios de Ecuador · El valor estimado se compara con el consenso de cuotas, sin garantía de resultados.')
    if not events:
        st.subheader('Prepara tu primer análisis')
        for col,(number,title,description) in zip(st.columns(3),[
            ('01','Elige tus competiciones','Abre Filtros de escaneo en el menú lateral. En celular, toca ☰ para abrirlo.'),
            ('02','Consulta el mercado','Selecciona los mercados y pulsa Escanear Mercado. Los resultados aparecerán aquí.'),
            ('03','Construye tu boleto','Compara eventos en Radar, añade selecciones y guarda el boleto en tu bitácora.')]):
            col.markdown(f'<div class="step-card"><span class="step-label">Paso {number}</span><h4>{title}</h4><p>{description}</p></div>',unsafe_allow_html=True)
        st.button('Ver guía y estado de APIs',on_click=go_to,args=('Soporte',),key='onboarding_help')
        return
    st.subheader('Eventos destacados')
    upcoming = sorted(events.values(),key=lambda p:p.get('fecha_ts',0))[:6]
    cols = st.columns(2)
    for i,event in enumerate(upcoming):
        with cols[i%2]:
            st.markdown(f"<div class='event-preview'><span class='liga-chip'>{escape(event['liga_origen'])}</span><b>{escape(event['local'])} <span class='versus'>vs</span> {escape(event['visitante'])}</b><small>{escape(event['fecha_str'])} · {len(event['mercados'])} mercados disponibles</small></div>",unsafe_allow_html=True)
    st.button('Explorar todos los eventos',on_click=go_to,args=('Radar Multi-Mercado',),use_container_width=True)
    frame = pd.DataFrame(rows)
    left,right = st.columns([3,2])
    with left:
        counts = pd.DataFrame([{'Liga':p['liga_origen']} for p in events.values()]).value_counts('Liga').reset_index(name='Eventos')
        themed_chart(px.bar(counts,x='Eventos',y='Liga',orientation='h',title='Cobertura por competición',color_discrete_sequence=['#21cfa4']),key='coverage_chart')
    with right:
        if not frame.empty:
            themed_chart(px.histogram(frame,x='Cuota',nbins=18,title='Distribución de cuotas',color_discrete_sequence=['#63a9f5']),key='odds_chart')


def opportunity_table(events,positive_only=False):
    frame = pd.DataFrame(rows_for(events))
    if frame.empty:
        st.info('Escanea el mercado para cargar eventos.');return
    prefix = 'ev' if positive_only else 'compare'
    a,b = st.columns(2)
    search = a.text_input('Buscar evento o equipo',placeholder='Ej.: Arsenal',key=prefix+'_search')
    league = b.selectbox('Competición',['Todas',*sorted(frame.Liga.unique())],key=prefix+'_league')
    if search: frame = frame[frame.Evento.str.contains(search,case=False,regex=False)]
    if league != 'Todas': frame = frame[frame.Liga==league]
    if positive_only: frame = frame[frame.EV>2]
    if frame.empty:
        st.info('No hay selecciones que coincidan con estos filtros.');return
    frame = frame.sort_values('EV',ascending=False)
    st.caption(f'{len(frame)} selecciones · Ordenadas por valor estimado')
    st.dataframe(frame,hide_index=True,use_container_width=True,
                 column_config={'Cuota':st.column_config.NumberColumn(format='%.2f'),'EV':st.column_config.NumberColumn('EV (%)',format='%.2f'),
                                'Probabilidad':st.column_config.ProgressColumn('Consenso (%)',min_value=0,max_value=100,format='%.1f%%')})
    st.download_button('Descargar comparación CSV',frame.to_csv(index=False).encode('utf-8-sig'),'comparacion.csv','text/csv',key=prefix+'_csv')
