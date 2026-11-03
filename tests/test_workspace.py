import pytest
from workspace_logic import filter_events,ticket_totals,settled_profit,ledger_summary,validate_history


def test_filtered_events_are_sorted_and_value_filter_is_honored():
    a={'local':'Arsenal','visitante':'Chelsea','liga_origen':'Premier','fecha_ts':2,'mercados':{'M':{'value_bets':{'A':{'es_value':True,'ev':.05}}}}}
    b={'local':'B','visitante':'C','liga_origen':'Otra','fecha_ts':1,'mercados':{'M':{'value_bets':{'A':{'es_value':False,'ev':0}}}}}
    assert filter_events({'a':a,'b':b}) == [b,a]
    assert filter_events({'a':a,'b':b},search=' arsenal ',only_value=True) == [a]
    assert filter_events({'a':a},league='Otra') == []


def test_ticket_returns_distinct_gross_net_and_correlation():
    selections=[{'evento':'A vs B','cuota':2.,'prob_real':50},{'evento':'A vs B','cuota':3.,'prob_real':40}]
    totals=ticket_totals(selections,10)
    assert totals['gross']==60 and totals['net']==50
    assert not totals['independent']
    selections[1]['prob_real']=0
    assert not ticket_totals(selections,10)['known']


def record(state='Pendiente',odds=3,stake=10):
    return dict(Fecha='03/11/2026',Detalles='A',Liga='Liga',Market='1X2',Cuota=odds,Inversión=stake,Estado=state,**{'Ganancia Potencial':20})


def test_settled_ledger_excludes_pending_and_voided_from_roi():
    history=[record('Ganada'),record('Perdida'),record('Anulada'),record()]
    summary=ledger_summary(history)
    assert summary['net']==10 and summary['roi']==50
    assert summary['exposure']==10 and summary['pending']==1
    assert settled_profit(record('Anulada'))==0


@pytest.mark.parametrize('row',[record('Unknown'),record(odds=0),record(stake=-2),record(stake='nan'),{}])
def test_bad_backups_are_rejected(row):
    with pytest.raises((ValueError,TypeError)): validate_history([row])


def test_old_backups_remain_compatible():
    assert validate_history([record()])[0]['Cuota']==3
