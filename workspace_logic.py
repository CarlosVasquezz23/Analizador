"""Deterministic workspace behavior, independent of Streamlit."""
import math

STATES = ('Pendiente', 'Ganada', 'Perdida', 'Anulada')


def filter_events(events, search='', league='Todas', order='Más próximos', only_value=False):
    result = [p for p in events.values()
              if (league == 'Todas' or p['liga_origen'] == league)
              and search.strip().casefold() in f"{p['local']} {p['visitante']}".casefold()
              and (not only_value or any(v.get('es_value') for m in p['mercados'].values() for v in m['value_bets'].values()))]
    if order == 'Mayor valor':
        return sorted(result, key=lambda p: max((v['ev'] for m in p['mercados'].values() for v in m['value_bets'].values()), default=0), reverse=True)
    return sorted(result, key=lambda p: (p.get('fecha_ts', 0), p['local']))


def ticket_totals(selections, stake):
    if not selections: return {'odds':1.,'probability':0.,'gross':0.,'net':0.,'independent':True,'known':False}
    if len(selections) > 20: raise ValueError('El boleto admite un máximo de 20 selecciones.')
    odds = math.prod(float(s['cuota']) for s in selections)
    if not math.isfinite(odds) or odds > 1e100: raise ValueError('La cuota combinada es demasiado alta para analizarla.')
    known = all(0 < float(s['prob_real']) <= 100 for s in selections)
    independent = len({s['evento'] for s in selections}) == len(selections)
    probability = math.prod(float(s['prob_real']) / 100 for s in selections) if known else 0
    return dict(odds=odds, probability=probability, gross=odds*stake, net=(odds-1)*stake,
                independent=independent,known=known)


def settled_profit(record):
    stake = float(record['Inversión'])
    if record['Estado'] == 'Ganada': return (float(record['Cuota'])-1)*stake
    if record['Estado'] == 'Perdida': return -stake
    return 0.


def ledger_summary(history):
    settled = [r for r in history if r['Estado'] in ('Ganada','Perdida')]
    stake = sum(float(r['Inversión']) for r in settled)
    profit = sum(settled_profit(r) for r in settled)
    return {'net':profit,'roi':100*profit/stake if stake else 0.,'settled':len(settled),
            'pending':sum(r['Estado']=='Pendiente' for r in history),
            'exposure':sum(float(r['Inversión']) for r in history if r['Estado']=='Pendiente')}


def validate_history(data):
    required = {'Fecha','Detalles','Liga','Market','Cuota','Inversión','Estado','Ganancia Potencial'}
    if not isinstance(data,list) or len(data)>10000: raise ValueError('Formato de respaldo inválido')
    clean = []
    for row in data:
        if not isinstance(row,dict) or not required <= row.keys(): raise ValueError('Faltan columnas del historial')
        row = dict(row)
        for key in ('Cuota','Inversión','Ganancia Potencial'):
            value = float(row[key])
            if not math.isfinite(value): raise ValueError('Número inválido')
            row[key] = value
        if row['Cuota'] <= 1 or row['Inversión'] <= 0 or row['Estado'] not in STATES: raise ValueError('Registro inválido')
        clean.append(row)
    return clean
