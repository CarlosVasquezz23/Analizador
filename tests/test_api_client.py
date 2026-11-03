from unittest.mock import Mock, patch
import requests
from api_client import get_json


def session_response(status=200, payload=None):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.return_value.status_code = status
    session.get.return_value.json.return_value = payload
    return session


def test_rate_limit_is_not_retried_and_error_is_redacted():
    session = session_response(429)
    with patch('api_client.requests.Session', return_value=session):
        assert get_json('https://example.com/?apikey=PRIVATE') == (None,'HTTP 429')
    policy = session.mount.call_args.args[1].max_retries
    assert 429 not in policy.status_forcelist
    assert session.get.call_count == 1


def test_success_preserves_payload():
    with patch('api_client.requests.Session', return_value=session_response(payload=[{'id':1}])):
        assert get_json('https://example.com') == ([{'id':1}],None)


def test_timeout_never_leaks_url():
    session = session_response()
    session.get.side_effect = requests.Timeout('https://example.com/?apikey=PRIVATE')
    with patch('api_client.requests.Session', return_value=session):
        data,error = get_json('https://example.com')
    assert data is None and 'PRIVATE' not in error
