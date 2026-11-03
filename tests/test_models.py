"""Exercise the legacy entrypoint's pure models without running its UI."""
import ast
import math
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace
import numpy as np
import pytest
from analytics import EXPECTED, normalize_outcome, valid_odds, surebet


def functions():
    source = ast.parse(Path('analizador.py').read_text())
    names = {'poisson_pmf_vec','calcular_modelo_poisson','calcular_sharpe_parlay','ejecutar_simulacion_montecarlo','procesar_e_inyectar_mercado'}
    tree = ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[])
    scope = dict(np=np, math=math, Dict=dict, List=list, Any=object, datetime=datetime, timedelta=timedelta,timezone=timezone,ZoneInfo=ZoneInfo,
                 st=SimpleNamespace(session_state={}), EXPECTED=EXPECTED, normalize_outcome=normalize_outcome,valid_odds=valid_odds,surebet=surebet,
                 diccionario_mercados={'1X2 (Resultado Final)':'h2h','Doble Oportunidad':'double_chance'})
    exec(compile(tree,'models','exec'), scope)
    return scope


def test_poisson_normalization_and_high_scoring_tail():
    fn = functions()['calcular_modelo_poisson']
    result = fn(3.5,3.5)
    assert sum(result[k] for k in ['Local','Empate','Visitante']) == pytest.approx(100)
    assert result['Local'] == pytest.approx(result['Visitante'])
    assert result['Sí'] + result['No'] == pytest.approx(100)
    assert fn(0,0)['Empate'] == pytest.approx(100)
    with pytest.raises(ValueError): fn(float('nan'),1)


def test_simulation_reproducible_and_sharpe_variance():
    scope = functions()
    mc = scope['ejecutar_simulacion_montecarlo']
    result = mc([{'prob_real':50}])
    assert 48 < result['pleno_acierto'] < 52
    assert result == mc([{'prob_real':50}])
    assert scope['calcular_sharpe_parlay'](2,.5) == 0


def event(hours=1, outcomes=None):
    return {'id':'1','home_team':'A','away_team':'B','commence_time':(datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat(),
            'bookmakers':[{'title':'Casa','markets':[{'key':'h2h','outcomes':outcomes or [{'name':'A','price':4},{'name':'Draw','price':4},{'name':'B','price':4}]}]}]}


def test_future_and_bookmaker_filters_and_incomplete_market():
    scope = functions(); process = scope['procesar_e_inyectar_mercado']
    output = {}; process([event(200)], '1X2 (Resultado Final)', 168, 'Liga',output)
    assert not output
    scope['st'].session_state['casas_preferidas'] = ['Otra']
    process([event()], '1X2 (Resultado Final)', 168, 'Liga',output)
    assert not output
    scope['st'].session_state.clear()
    process([event(outcomes=[{'name':'A','price':4},{'name':'B','price':4}])], '1X2 (Resultado Final)',168,'Liga',output)
    market = output['1']['mercados']['1X2 (Resultado Final)']
    assert not market['surebet']['es_surebet']
    assert all(not x['es_value'] for x in market['value_bets'].values())


def test_double_chance_never_labeled_surebet():
    scope = functions(); data = event()
    data['bookmakers'][0]['markets'] = [{'key':'double_chance','outcomes':[{'name':'1X','price':4},{'name':'X2','price':4},{'name':'12','price':4}]}]
    output = {}; scope['procesar_e_inyectar_mercado']([data], 'Doble Oportunidad',168,'Liga',output)
    assert not output['1']['mercados']['Doble Oportunidad']['surebet']['es_surebet']


def test_worker_preserves_good_provider_when_another_is_malformed():
    source = ast.parse(Path('analizador.py').read_text())
    node = next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name == 'escanear_liga_worker')
    scope = dict(API_KEY='test',AF_API_KEY='test',ALLSPORTS_KEY='',SPORTAPI_KEY='',
                 diccionario_mercados={'1X2 (Resultado Final)':'h2h'},
                 consultar_api_odds=lambda *args:([event()],None),
                 consultar_af_con_fallback_fixtures=lambda *args:([None],None))
    exec(compile(ast.Module(body=[node],type_ignores=[]),'worker','exec'),scope)
    league,results,notes = scope['escanear_liga_worker']('Liga',{'odds_api':['test'],'af_id':1},['1X2 (Resultado Final)'],168)
    assert league == 'Liga' and len(results) == 1
    assert any('API-Football' in note for note in notes)
