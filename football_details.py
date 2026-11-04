"""On-demand fixture, lineup and injury lookup through API-Football."""
import pandas as pd
import streamlit as st
from api_client import get_json


@st.cache_data(ttl=300, max_entries=64, show_spinner=False)
def fixtures(league_id, api_key):
    data,error = get_json('https://v3.football.api-sports.io/fixtures',
                          headers={'x-apisports-key':api_key}, params={'league':league_id,'next':10})
    if not isinstance(data,dict) or data.get('errors'): return [],error or 'No se pudieron consultar los eventos con este plan.'
    return data.get('response',[]),None


@st.cache_data(ttl=180, max_entries=64, show_spinner=False)
def details(fixture_id, api_key):
    results, errors = {}, []
    for resource in ('lineups','injuries'):
        data,error = get_json('https://v3.football.api-sports.io/' + resource,
                              headers={'x-apisports-key':api_key}, params={'fixture':fixture_id})
        if error or not isinstance(data,dict) or data.get('errors'):
            errors.append(f'{resource}: {error or "sin acceso o respuesta válida"}')
            results[resource] = []
        else: results[resource] = data.get('response',[])
    return results,errors


def render(catalog, api_key):
    st.caption('Consulta bajo demanda. La disponibilidad de alineaciones y lesiones depende de la competición y de tu plan de API-Football.')
    if not api_key:
        st.info('Configura AF_API_KEY en Secrets para consultar alineaciones y bajas.'); return
    league = st.selectbox('Competición', list(catalog))
    if st.button('Consultar próximos eventos'):
        data,error = fixtures(catalog[league]['af_id'], api_key)
        st.session_state.fixture_lookup = (league,data,error)
    lookup = st.session_state.get('fixture_lookup')
    if not lookup or lookup[0] != league: return
    _,data,error = lookup
    if error: st.warning(error)
    if not data:
        st.info('Sin próximos eventos disponibles para esta consulta.'); return
    mapping = {item['fixture']['id']:item for item in data}
    selected = st.selectbox('Partido', list(mapping), format_func=lambda key: f"{mapping[key]['teams']['home']['name']} vs {mapping[key]['teams']['away']['name']}")
    if st.button('Cargar alineaciones y bajas'):
        result,errors = details(selected,api_key)
        for error in errors: st.warning(error)
        st.subheader('Alineaciones')
        if not result['lineups']: st.info('Alineaciones aún no publicadas o no disponibles.')
        for team in result['lineups']:
            st.write(team.get('team',{}).get('name','Equipo'), '·', team.get('formation') or 'Sin formación')
            players = [{'Jugador':p.get('player',{}).get('name'), 'Dorsal':p.get('player',{}).get('number'), 'Posición':p.get('player',{}).get('pos')} for p in team.get('startXI',[])]
            st.dataframe(pd.DataFrame(players), hide_index=True, width="stretch")
        st.subheader('Bajas reportadas')
        if not result['injuries']: st.info('Sin registros de bajas en la respuesta; no confirma disponibilidad de todos los jugadores.')
        else:
            st.dataframe(pd.DataFrame([{'Equipo':i.get('team',{}).get('name'), 'Jugador':i.get('player',{}).get('name'), 'Tipo':i.get('player',{}).get('type'), 'Motivo':i.get('player',{}).get('reason')} for i in result['injuries']]), hide_index=True, width="stretch")
