import math
import pytest
from analytics import valid_odds, normalize_outcome, surebet, parse_ticket, api_football_markets

@pytest.mark.parametrize('value', [None, '', 0, 1, -2, 'nan', 'inf', {}, 'not an odd'])
def test_invalid_odds(value):
    assert valid_odds(value) is None


def test_complete_surebet_only():
    expected = {'Local', 'Empate', 'Visitante'}
    assert not surebet({'Local': 4, 'Visitante': 4}, expected)['es_surebet']
    assert surebet(dict.fromkeys(expected, 4), expected)['es_surebet']
    assert not surebet({'yes': 0, 'no': 3})['es_surebet']


def test_total_requires_explicit_line():
    assert normalize_outcome('totals', {'name':'Over'}, 'A','B') is None
    assert normalize_outcome('totals', {'name':'Over','point':3.5}, 'A','B') is None
    assert normalize_outcome('totals', {'name':'Over','point':'2.5'}, 'A','B') == 'Más de 2.5'
    assert normalize_outcome('h2h', {'name':'Unknown'}, 'A','B') is None


def test_ticket_rejects_zero_and_preserves_names():
    result = parse_ticket('Equipo 0.0\nArsenal 1,80\nBarcelona 2\nEquipo nan\nSin cuota')
    assert [r['nombre'] for r in result] == ['Arsenal', 'Barcelona']
    assert result[0]['cuota'] == 1.8


def test_af_markets_do_not_convert_unknown_bets_into_btts():
    result = api_football_markets([
        {'name':'Correct Score', 'values':[{'value':'1:0','odd':'9'}]},
        {'name':'Goals Over/Under', 'values':[{'value':'Over 2.5','odd':'1.9'},{'value':'Under 3.5','odd':'bad'}]},
        {'name':'Match Winner', 'values':[{'value':'Home','odd':'2.2'}]},
    ], 'A','B')
    assert len(result) == 2
    assert result[0]['outcomes'] == [{'name':'Over','point':2.5,'price':1.9}]
    assert result[1]['key'] == 'h2h'
