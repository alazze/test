"""COD analytics. Python standard library only; private local dashboard."""
import argparse
import csv
import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'analytics.sqlite'
STORES = {'orzein-portugal': 'Orzein · Portugal', 'orzein-spain': 'Orzein · Spain', 'orsiga-spain': 'ORSIGA · Spain'}
MAX_BYTES = 32 * 1024 * 1024


def connect(path=None):
    p = Path(path or DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.execute('CREATE TABLE IF NOT EXISTS orders (store TEXT, id TEXT, name TEXT, body TEXT NOT NULL, PRIMARY KEY(store,id))')
    c.execute('CREATE TABLE IF NOT EXISTS service (store TEXT, id TEXT, updated_at TEXT, body TEXT NOT NULL, PRIMARY KEY(store,id))')
    c.execute('CREATE TABLE IF NOT EXISTS imports (at TEXT, store TEXT, kind TEXT, count INTEGER, filename TEXT)')
    import business
    business.initialize(c)
    return c


def csv_rows(blob):
    if len(blob) > MAX_BYTES:
        raise ValueError('Fichier trop volumineux (maximum 32 Mo).')
    if zipfile.is_zipfile(io.BytesIO(blob)):
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            files = [x for x in z.infolist() if x.filename.lower().endswith('.csv') and not x.is_dir()]
            if not files or sum(x.file_size for x in files) > MAX_BYTES:
                raise ValueError('Archive sans CSV ou contenu trop volumineux.')
            return [row for member in files for row in read_csv(z.read(member))]
    return read_csv(blob)


def read_csv(blob):
    text = blob.decode('utf-8-sig')
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text, newline=''), delimiter=dialect.delimiter, quotechar='"', doublequote=True, strict=True)
    if not reader.fieldnames:
        raise ValueError('CSV vide.')
    return list(reader)


def cents(value):
    from decimal import Decimal, ROUND_HALF_UP
    return int((Decimal(value or '0') * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def iso(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Date avec fuseau horaire obligatoire.')
    return dt.astimezone(timezone.utc).isoformat()


def import_shopify(c, blob, store, filename='export'):
    if store not in __import__('business').registry(c):
        raise ValueError('Store inconnu.')
    rows = csv_rows(blob)
    if not rows or not {'Name', 'Id', 'Created at', 'Total', 'Lineitem name'}.issubset(rows[0]):
        raise ValueError('Export Shopify attendu avec Name, Id, Created at, Total et Lineitem name.')
    orders, current, seen_lines = {}, None, 0
    for r in rows:
        name = (r.get('Name') or '').strip()
        rid = (r.get('Id') or '').strip()
        # Shopify continuation rows omit Id and order-level fields. Attach only by name.
        if rid:
            current = rid
        elif current is None or orders[current]['name'] != name:
            matches = [k for k, v in orders.items() if v['name'] == name]
            if len(matches) != 1:
                raise ValueError('Ligne article sans commande identifiable.')
            current = matches[0]
        if current not in orders:
            created = r.get('Created at', '')
            iso(created)  # validate, retain the source-local calendar date for filtering
            orders[current] = {'id': current, 'name': name, 'store': store, 'date': created[:10],
                'created_at': created, 'country': r.get('Shipping Country', '') or 'UNKNOWN',
                'currency': r.get('Currency', ''), 'total': cents(r.get('Total')),
                'refund': cents(r.get('Refunded Amount')), 'financial': r.get('Financial Status', ''),
                'fulfillment': r.get('Fulfillment Status', ''), 'cancelled': bool(r.get('Cancelled at')),
                'items': []}
        elif rid and r.get('Total'):
            raise ValueError('Commande principale répétée dans le même fichier; import arrêté pour éviter un double comptage.')
        o = orders[current]
        if name != o['name']:
            raise ValueError('Ordre des lignes Shopify incohérent.')
        if r.get('Lineitem name'):
            quantity = int(r.get('Lineitem quantity') or '0')
            if quantity < 0:
                raise ValueError('Quantité négative.')
            o['items'].append({'name': r['Lineitem name'], 'sku': (r.get('Lineitem sku') or '').lstrip("'").lstrip('-'),
                'quantity': quantity, 'gross': quantity * cents(r.get('Lineitem price')) - cents(r.get('Lineitem discount'))})
            seen_lines += 1
    with c:
        for o in orders.values():
            c.execute('INSERT INTO orders VALUES (?,?,?,?) ON CONFLICT(store,id) DO UPDATE SET name=excluded.name,body=excluded.body',
                      (store, o['id'], o['name'], json.dumps(o, ensure_ascii=False)))
        c.execute('INSERT INTO imports VALUES (?,?,?,?,?)', (datetime.now(timezone.utc).isoformat(), store, 'shopify', len(orders), Path(filename).name))
    return {'orders': len(orders), 'lineitems': seen_lines, 'store': store}


FIELDS = {'confirmation_status': {'confirmed', 'cancelled', 'pending', 'unreachable', 'unknown'},
          'shipping_status': {'shipped', 'not_shipped', 'unknown'},
          'delivery_status': {'delivered', 'in_transit', 'refused', 'returned', 'failed', 'pending', 'unknown'}}


def import_service(c, blob, filename='service'):
    rows = csv_rows(blob)
    if not rows or not {'store', 'updated_at', *FIELDS}.issubset(rows[0]):
        raise ValueError('Utilisez le modèle de statuts normalisés; le format API sera adapté après réception de sa documentation.')
    matched, unmatched, stale, batch = 0, [], 0, {}
    for index, r in enumerate(rows, 2):
        store = r.get('store', '').strip()
        if store not in __import__('business').registry(c):
            raise ValueError(f'Ligne {index}: store inconnu.')
        timestamp = iso(r['updated_at'])
        body = {f: r.get(f, '').strip().lower() or 'unknown' for f in FIELDS}
        if any(body[f] not in allowed for f, allowed in FIELDS.items()):
            raise ValueError(f'Ligne {index}: statut non reconnu. Ne pas deviner sa signification.')
        if body['delivery_status'] in {'delivered', 'in_transit', 'refused', 'returned'} and body['shipping_status'] != 'shipped':
            raise ValueError(f'Ligne {index}: statut livraison incompatible avec shipping_status.')
        rid, name = r.get('shopify_id', '').strip(), r.get('order_name', '').strip()
        if not rid and not name:
            raise ValueError(f'Ligne {index}: shopify_id ou order_name requis.')
        hits = c.execute('SELECT id,name FROM orders WHERE store=? AND ' + ('id=?' if rid else 'name=?'), (store, rid or name)).fetchall()
        if len(hits) != 1 or (rid and name and hits[0][1] != name):
            unmatched.append({'row': index, 'store': store, 'reference': rid or name})
            continue
        key = (store, hits[0][0])
        old = batch.get(key)
        if old is None:
            db_old = c.execute('SELECT updated_at,body FROM service WHERE store=? AND id=?', key).fetchone()
            if db_old:
                old = (db_old[0], json.loads(db_old[1]))
        if old and timestamp < old[0]:
            stale += 1
            continue
        if old and timestamp == old[0] and body != old[1]:
            raise ValueError(f'Ligne {index}: deux statuts différents à la même date.')
        batch[key] = (timestamp, body)
    with c:
        for (store, rid), (timestamp, body) in batch.items():
            c.execute('INSERT INTO service VALUES (?,?,?,?) ON CONFLICT(store,id) DO UPDATE SET updated_at=excluded.updated_at,body=excluded.body',
                      (store, rid, timestamp, json.dumps(body)))
        matched = len(batch)
        c.execute('INSERT INTO imports VALUES (?,?,?,?,?)', (datetime.now(timezone.utc).isoformat(), '', 'service', matched, Path(filename).name))
    return {'matched': matched, 'unmatched': unmatched, 'stale': stale}


def snapshot(c):
    orders = [json.loads(r[0]) for r in c.execute('SELECT body FROM orders ORDER BY store,id')]
    services = {(r[0], r[1]): {**json.loads(r[3]), 'updated_at': r[2]} for r in c.execute('SELECT * FROM service')}
    for o in orders:
        o['service'] = services.get((o['store'], o['id']))
    return {**__import__('business').metadata(c), 'orders': orders, 'stores': __import__('business').registry(c), 'imports': [dict(zip(['at', 'store', 'kind', 'count', 'filename'], r)) for r in c.execute('SELECT * FROM imports ORDER BY at DESC LIMIT 20')]}


def standalone(c, path):
    html = (ROOT / 'web/index.html').read_text()
    payload = json.dumps(snapshot(c), ensure_ascii=False).replace('<', '\\u003c')
    html = html.replace('/* SNAPSHOT */ null', payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return path


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # no request payload or identifiers in server logs

    def send(self, status, data, kind='application/json; charset=utf-8'):
        blob = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(blob)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
        if getattr(self, 'session_cookie', None):
            self.send_header('Set-Cookie', self.session_cookie)
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/data':
            with connect() as c:
                self.send(200, snapshot(c))
        elif path == '/':
            self.send(200, (ROOT / 'web/index.html').read_bytes(), 'text/html; charset=utf-8')
        elif path == '/service-template.csv':
            self.send(200, b'store,shopify_id,order_name,confirmation_status,shipping_status,delivery_status,updated_at\n', 'text/csv; charset=utf-8')
        elif path == '/health':
            self.send(200, {'status': 'ok'})
        else:
            self.send(404, {'error': 'Page introuvable.'})

    def do_POST(self):
        # Reject cross-origin uploads, including malicious sites targeting loopback.
        origin = self.headers.get('Origin')
        host = self.headers.get('Host')
        if origin and origin != f'http://{host}':
            return self.send(403, {'error': 'Origine interdite.'})
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(403, {'error': 'Import JSON de même origine requis.'})
        path = urlsplit(self.path).path
        if path not in {'/api/import/shopify', '/api/import/service'}:
            return self.send(404, {'error': 'Endpoint introuvable.'})
        try:
            import base64
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < MAX_BYTES * 1.4:
                raise ValueError('Taille du fichier invalide.')
            req = json.loads(self.rfile.read(length))
            blob = base64.b64decode(req['data'], validate=True)
            with connect() as c:
                result = import_shopify(c, blob, req['store'], req.get('filename', 'export')) if path.endswith('shopify') else import_service(c, blob, req.get('filename', 'service'))
            return self.send(200, result)
        except (ValueError, KeyError, UnicodeError, csv.Error, zipfile.BadZipFile) as e:
            self.send(400, {'error': str(e)})
        except Exception:
            self.send(500, {'error': 'Import interrompu. Aucune confirmation de réussite; vérifier le fichier.'})


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)
    i = sub.add_parser('import-shopify'); i.add_argument('--store', required=True, ); i.add_argument('file')
    i = sub.add_parser('import-service'); i.add_argument('file')
    e = sub.add_parser('export'); e.add_argument('file')
    s = sub.add_parser('serve'); s.add_argument('--port', type=int, default=8765)
    args = p.parse_args()
    if args.command == 'serve':
        from server import SecureHandler
        server = ThreadingHTTPServer(('127.0.0.1', args.port), SecureHandler)
        print(f'COD analytics running locally on port {args.port}', flush=True)
        server.serve_forever()
    else:
        with connect() as c:
            if args.command == 'export':
                print(standalone(c, args.file))
            elif args.command == 'import-shopify':
                print(json.dumps(import_shopify(c, Path(args.file).read_bytes(), args.store, args.file)))
            else:
                print(json.dumps(import_service(c, Path(args.file).read_bytes(), args.file)))


if __name__ == '__main__':
    main()
