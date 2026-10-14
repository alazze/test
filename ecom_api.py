"""Read-only EcomFulfilment API adapter. No token/password persistence."""
import argparse
import csv
import io
import json
import os
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urljoin
from app import ROOT, connect, import_service

BASE = 'https://api.ecomfulfilment.eu'
ALLOWED_PATHS = {'/api/leads', '/api/leads/details', '/api/leads/history', '/api/stores'}
MAX_PAGES = 500


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirection API refusée. Vérifiez le domaine officiel avant de modifier la configuration.')


def validate_url(url, expected_path=None):
    u = urlsplit(url)
    if u.scheme != 'https' or u.hostname != 'api.ecomfulfilment.eu' or u.port not in (None, 443) or u.username or u.password or u.fragment or u.path not in ALLOWED_PATHS:
        raise ValueError('Destination API non autorisée.')
    if expected_path and u.path != expected_path:
        raise ValueError('Pagination dirigée vers un autre endpoint.')
    return url


_SESSION = {'token': None, 'expires': 0}


def login_token(force=False):
    """Official POST login. Credentials/token only in memory; never logged."""
    if not force and _SESSION['token'] and time.monotonic() < _SESSION['expires']:
        return _SESSION['token']
    email, password = os.environ.get('ECOMFULFILMENT_EMAIL'), os.environ.get('ECOMFULFILMENT_PASSWORD')
    if not email or not password:
        raise ValueError('Identifiants service absents des secrets sécurisés.')
    # The official documentation specifies query parameters for login.
    # Do not print this request, its URL, exceptions, or response.
    url = BASE + '/api/login?' + urlencode({'email': email, 'password': password})
    req = Request(url, data=b'', headers={'Accept': 'application/json'}, method='POST')
    try:
        with build_opener(NoRedirect()).open(req, timeout=40) as r:
            raw = r.read(100001)
            if len(raw) > 100000:
                raise ValueError('Réponse authentification invalide.')
            result = json.loads(raw)
        token = result.get('token') if isinstance(result, dict) and result.get('status') is True else None
        if not isinstance(token, str) or not token:
            raise ValueError('Authentification service refusée. Vérifier le compte dans les paramètres sécurisés.')
    except (HTTPError, URLError, UnicodeError, json.JSONDecodeError):
        raise ValueError('Authentification service non validée. Réponse et identifiants non journalisés.') from None
    _SESSION.update(token=token, expires=time.monotonic() + 7 * 3600)
    return token


def get_json(url):
    validate_url(url)
    account_login = bool(os.environ.get('ECOMFULFILMENT_EMAIL') and os.environ.get('ECOMFULFILMENT_PASSWORD'))
    token = login_token() if account_login else os.environ.get('ECOMFULFILMENT_TOKEN')
    if not token:
        raise ValueError('Accès service non configuré : renseigner les identifiants sécurisés ou un token valide.')
    for attempt in range(2):
        req = Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}, method='GET')
        try:
            with build_opener(NoRedirect()).open(req, timeout=40) as r:
                data = r.read(20 * 1024 * 1024 + 1)
                if len(data) > 20 * 1024 * 1024:
                    raise ValueError('Réponse API trop volumineuse.')
                result = json.loads(data)
            # This API may return authentication failure with HTTP 200.
            unauthorized = isinstance(result, dict) and result.get('status') is False
            if unauthorized:
                if account_login and attempt == 0:
                    token = login_token(force=True)
                    continue
                raise ValueError('Accès API refusé. Vérifier les permissions et les identifiants sécurisés.')
            return result
        except HTTPError as e:
            if e.code == 401 and account_login and attempt == 0:
                token = login_token(force=True)
                continue
            raise ValueError(f'API HTTP {e.code}. Réponse non journalisée; vérifier autorisation ou quota.') from None
        except URLError:
            raise ValueError('API inaccessible via le réseau/proxy. Vérifier les domaines autorisés dans les paramètres.') from None
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError('L’API ne renvoie pas de JSON valide.') from None
    raise ValueError('Authentification API non validée.')


def paginate(path='/api/leads', fetch=get_json, country=None):
    """Conservative support for common Laravel envelopes; never silently truncate."""
    url = BASE + path + ('?' + urlencode({'country': country}) if country else '')
    seen, result = set(), []
    for _ in range(MAX_PAGES):
        validate_url(url, path)
        if url in seen:
            raise ValueError('Boucle de pagination détectée.')
        seen.add(url)
        payload = fetch(url)
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise ValueError('Schéma de liste non validé. Utiliser inspect puis adapter au format officiel avant synchronisation.')
        if any(not isinstance(x, dict) for x in payload['data']):
            raise ValueError('Liste de commandes invalide.')
        result.extend(payload['data'])
        links = payload.get('links') or {}
        meta = payload.get('meta') or payload
        current, last = meta.get('current_page'), meta.get('last_page')
        explicit_next = 'next_page_url' in payload or isinstance(links, dict) and 'next' in links
        next_url = payload.get('next_page_url') or (links.get('next') if isinstance(links, dict) else None)
        if next_url:
            url = validate_url(urljoin(BASE, next_url), path)
            continue
        if isinstance(current, int) and isinstance(last, int):
            if not 1 <= current <= last:
                raise ValueError('Métadonnées de pagination incohérentes.')
            if current < last:
                u = urlsplit(url)
                q = dict(parse_qsl(u.query)); q['page'] = str(current + 1)
                url = urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ''))
                continue
            if isinstance(meta.get('total'), int) and meta['total'] != len(result):
                raise ValueError('Nombre de commandes différent du total annoncé par l’API.')
            return result
        if explicit_next:
            return result
        raise ValueError('Pagination non documentée dans la réponse; ne pas considérer cette liste comme complète.')
    raise ValueError('Limite de pagination atteinte; synchronisation interrompue sans enregistrer de statuts.')


def field(obj, path):
    if not path:
        return None
    for key in path.split('.'):
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def normalize(leads, mapping):
    """Mapping must come from verified API fields/status meanings, not guesses."""
    columns = ['store', 'shopify_id', 'order_name', 'confirmation_status', 'shipping_status', 'delivery_status', 'updated_at']
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=columns); writer.writeheader()
    for lead in leads:
        store = mapping['stores'].get(str(field(lead, mapping['store_field'])))
        if not store:
            raise ValueError('Store API non associé : renseigner la correspondance vérifiée des boutiques avant de synchroniser.')
        row = {'store': store, 'shopify_id': field(lead, mapping.get('shopify_id_field')), 'order_name': field(lead, mapping.get('order_name_field')), 'updated_at': field(lead, mapping['updated_at_field'])}
        for name in ['confirmation_status', 'shipping_status', 'delivery_status']:
            spec = mapping[name]; raw = field(lead, spec['field'])
            row[name] = spec['values'].get(str(raw), 'unknown')
        writer.writerow(row)
    return out.getvalue().encode('utf-8')


def structure(value, depth=0):
    """Return key/type information only; never disclose API field values."""
    if depth > 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k)[:100]: structure(v, depth+1) for k,v in list(value.items())[:100]}
    if isinstance(value, list):
        return {'type': 'list', 'count': len(value), 'first_item_shape': structure(value[0], depth+1) if value else None}
    return type(value).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    i = sub.add_parser('inspect'); i.add_argument('--endpoint', choices=['leads','stores'], default='leads'); i.add_argument('--country')
    s = sub.add_parser('sync'); s.add_argument('--mapping', required=True); s.add_argument('--country', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'inspect':
            print(json.dumps(structure(get_json(BASE+'/api/'+args.endpoint + ('?' + urlencode({'country': args.country}) if args.country else ''))), ensure_ascii=False, indent=2))
        else:
            mapping = json.loads(open(args.mapping).read())
            leads = paginate(country=args.country)
            blob = normalize(leads, mapping)
            with connect() as c:
                print(json.dumps(import_service(c, blob, 'API EcomFulfilment'), ensure_ascii=False))
    except (ValueError, KeyError, TypeError, OSError):
        # Avoid printing paths/values from account payloads or auth failures.
        print('Connexion ou synchronisation non validée. Vérifier le secret, le réseau, le schéma, les statuts et la pagination. Aucun accès en écriture à EcomFulfilment.')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
