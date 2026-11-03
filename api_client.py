"""Bounded HTTP reads. Errors never include credential-bearing URLs."""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def get_json(url, *, params=None, headers=None):
    retry = Retry(total=2, connect=2, read=0, status=2, backoff_factor=0.3,
                  status_forcelist=(502, 503, 504), allowed_methods={'GET'})
    try:
        with requests.Session() as session:
            session.mount('https://', HTTPAdapter(max_retries=retry))
            response = session.get(url, params=params, headers=headers, timeout=(3, 8))
            if response.status_code != 200:
                return None, f'HTTP {response.status_code}'
            return response.json(), None
    except requests.exceptions.JSONDecodeError:
        return None, 'Respuesta JSON inválida'
    except (requests.RequestException, ValueError):
        return None, 'Proveedor no disponible o respuesta inválida'
