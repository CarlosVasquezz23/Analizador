"""Pure validation and market helpers; no network calls or Streamlit state."""
import math
import re

EXPECTED = {
    '1X2 (Resultado Final)': {'Local', 'Empate', 'Visitante'},
    'Ambos Anotan (BTTS)': {'Sí', 'No'},
    'Total Goles (+/- 2.5)': {'Más de 2.5', 'Menos de 2.5'},
}


def valid_odds(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 1 else None
    except (TypeError, ValueError, OverflowError):
        return None


def normalize_outcome(market, outcome, home, away):
    """Reject unknown outcomes and totals other than an explicit 2.5 line."""
    name = str(outcome.get('name', '')).strip()
    label = name.lower()
    if market == 'h2h':
        if name == home or label in ('home', '1'): return 'Local'
        if name == away or label in ('away', '2'): return 'Visitante'
        if label in ('draw', 'x', 'empate'): return 'Empate'
    if market == 'btts':
        if label in ('yes', 'sí', 'si'): return 'Sí'
        if label in ('no',): return 'No'
    if market == 'double_chance':
        return {'home_draw': '1X (Local o Empate)', 'home or draw': '1X (Local o Empate)',
                '1x': '1X (Local o Empate)', 'away_draw': 'X2 (Visitante o Empate)',
                'away or draw': 'X2 (Visitante o Empate)', 'x2': 'X2 (Visitante o Empate)',
                'home_away': '12 (Local o Visitante)', 'home or away': '12 (Local o Visitante)',
                '12': '12 (Local o Visitante)'}.get(label)
    if market == 'totals':
        try:
            if float(outcome.get('point')) != 2.5: return None
        except (ValueError, TypeError):
            return None
        if label in ('over', 'más', 'mas'): return 'Más de 2.5'
        if label in ('under', 'menos'): return 'Menos de 2.5'
    return None


def surebet(odds, expected=None):
    invalid = {'es_surebet': False, 'overround': 1.0, 'lucro': 0.0}
    if len(odds) < 2 or (expected is not None and set(odds) != set(expected)):
        return invalid
    values = [valid_odds(v) for v in odds.values()]
    if any(v is None for v in values): return invalid
    margin = sum(1 / v for v in values)
    return {'es_surebet': margin < 1, 'overround': margin,
            'lucro': (1 / margin - 1) * 100 if margin < 1 else 0.0}


def parse_ticket(text):
    selections = []
    for line in text.splitlines():
        match = re.search(r'\s+(\d+(?:[.,]\d+)?)\s*$', line.strip())
        if match and (odd := valid_odds(match[1].replace(',', '.'))):
            name = line[:match.start()].strip()
            if name:
                selections.append({'nombre': name, 'cuota': odd, 'prob_real': 95 / odd})
    return selections


def api_football_markets(bets, home, away):
    mapping = {'Match Winner': 'h2h', 'Goals Over/Under': 'totals',
               'Both Teams Score': 'btts', 'Double Chance': 'double_chance'}
    markets = []
    for bet in bets:
        key = mapping.get(bet.get('name'))
        if not key: continue
        outcomes = []
        for item in bet.get('values', []):
            odd = valid_odds(item.get('odd'))
            if odd is None: continue
            name = str(item.get('value', ''))
            outcome = {'name': name, 'price': odd}
            if key == 'totals':
                parts = name.rsplit(' ', 1)
                if len(parts) != 2: continue
                try: outcome.update(name=parts[0], point=float(parts[1]))
                except ValueError: continue
            if normalize_outcome(key, outcome, home, away): outcomes.append(outcome)
        if outcomes: markets.append({'key': key, 'outcomes': outcomes})
    return markets
