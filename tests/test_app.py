from pathlib import Path
from datetime import datetime, timedelta, timezone
import pytest
from streamlit.testing.v1 import AppTest

@pytest.fixture
def app():
    return AppTest.from_file(Path(__file__).resolve().parents[1] / 'analizador.py', default_timeout=30).run()


def test_boot_and_every_screen_without_credentials(app):
    assert not app.exception
    for view in app.selectbox(key='navigation').options:
        app.selectbox(key='navigation').select(view).run()
        assert not app.exception, view


def test_theme_applies_immediately(app):
    app.radio(key='tema_visual_widget').set_value('Claro').run()
    assert app.session_state['tema_visual'] == 'Claro'
    assert not app.exception


def test_scan_without_keys_is_explained(app):
    next(b for b in app.button if b.label == 'Escanear Mercado').click().run()
    assert any('Configura al menos una API' in w.value for w in app.warning)
    assert not app.exception


def test_dashboard_with_events_and_ticket(app):
    app.session_state['datos_cargados'] = {'1': {
        'id':'1','liga_origen':'Premier League','local':'Arsenal','visitante':'Chelsea',
        'fecha_str':'03/11/2026','fecha_ts':1793746800,
        'mercados':{'1X2 (Resultado Final)':{
            'max_cuotas':{'Local':2.5,'Empate':3.2,'Visitante':3.4},
            'max_bookies':dict.fromkeys(['Local','Empate','Visitante'],'Casa'),
            'value_bets':{k:{'ev':.04,'prob_real':35,'es_value':True} for k in ['Local','Empate','Visitante']},
            'surebet':{'es_surebet':False}}}}}
    app.session_state['ha_consultado'] = True
    app.run()
    assert not app.exception
    for view in ['Radar Multi-Mercado','Matriz de Cobertura','Oportunidades +EV']:
        app.selectbox(key='navigation').select(view).run()
        assert not app.exception
    app.selectbox(key='navigation').select('Radar Multi-Mercado').run()
    next(c for c in app.checkbox if c.label.startswith('Local (')).check().run()
    assert len(app.session_state['ticket_persistente']) == 1
    assert not app.exception
    next(b for b in app.button if b.label == 'Guardar Historial').click().run()
    assert len(app.session_state['historial_apuestas']) == 1
    app.selectbox(key='navigation').select('Bitácora Financiera').run()
    assert not app.exception


def test_verifier_rejects_zero_odds_and_parses_valid_text(app):
    app.selectbox(key='navigation').select('Verificador & Monte Carlo').run()
    next(t for t in app.text_area if t.label.startswith('Pega tus selecciones')).set_value('Equipo 0.0\nArsenal 1.80').run()
    assert not app.exception
    assert any(m.label == 'Cuota' and m.value == '@1.8' for m in app.metric)


def test_financial_session_isolation(app):
    app.session_state['historial_apuestas'] = [{'Detalles':'Privado'}]
    other = AppTest.from_file(Path(__file__).resolve().parents[1] / 'analizador.py').run()
    assert other.session_state['historial_apuestas'] == []
