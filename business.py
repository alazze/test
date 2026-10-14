"""Private-account, country/service configuration and COD cost ledger."""
import hashlib,hmac,secrets,time,json,re
from datetime import date
from http.cookies import SimpleCookie


def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,salt TEXT,hash TEXT);
    CREATE TABLE IF NOT EXISTS sessions(digest TEXT PRIMARY KEY,username TEXT,expires REAL);
    CREATE TABLE IF NOT EXISTS attempts(address TEXT PRIMARY KEY,count INTEGER,until REAL);
    CREATE TABLE IF NOT EXISTS countries(code TEXT PRIMARY KEY,name TEXT);
    CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY,name TEXT,connector TEXT);
    CREATE TABLE IF NOT EXISTS stores(id TEXT PRIMARY KEY,name TEXT,country TEXT,provider TEXT,domain TEXT);
    CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY,kind TEXT,body TEXT);
    ''')
    for code,name in [('ES','Espagne'),('PT','Portugal'),('IT','Italie')]:c.execute('INSERT OR IGNORE INTO countries VALUES (?,?)',(code,name))
    c.execute('INSERT OR IGNORE INTO providers VALUES (?,?,?)',('ecomfulfilment','EcomFulfilment','ecom'))
    for sid,name,country in [('orzein-spain','Orzein · Spain','ES'),('orzein-portugal','Orzein · Portugal','PT'),('orsiga-spain','ORSIGA · Spain','ES')]:
        c.execute('INSERT OR IGNORE INTO stores VALUES (?,?,?,?,?)',(sid,name,country,'ecomfulfilment',''))
    c.commit()


def registry(c):
    return {r[0]:r[1] for r in c.execute('SELECT id,name FROM stores')}


def metadata(c):
    return {'countries':[dict(zip(['code','name'],r)) for r in c.execute('SELECT * FROM countries ORDER BY code')],
     'providers':[dict(zip(['id','name','connector'],r)) for r in c.execute('SELECT * FROM providers')],
     'store_config':[dict(zip(['id','name','country','provider','domain'],r)) for r in c.execute('SELECT * FROM stores')],
     'ledger':[dict(json.loads(r[2]),id=r[0],kind=r[1]) for r in c.execute('SELECT * FROM ledger ORDER BY rowid DESC')]}


def digest(token):return hashlib.sha256(token.encode()).hexdigest()

def user(c,headers):
    cookie=SimpleCookie()
    try:cookie.load(headers.get('Cookie',''))
    except Exception:return None
    token=cookie.get('cod_session')
    if not token:return None
    r=c.execute('SELECT username FROM sessions WHERE digest=? AND expires>?',(digest(token.value),time.time())).fetchone()
    return r[0] if r else None


def authenticate(c,payload,setup=False,address='local'):
    now=time.time()
    attempt=c.execute('SELECT count,until FROM attempts WHERE address=?',(address,)).fetchone()
    if attempt and attempt[0]>=5 and attempt[1]>now:raise ValueError('Trop de tentatives. Réessayer dans 15 minutes.')
    username=str(payload.get('username','')).strip();password=payload.get('password','')
    if not isinstance(password,str) or len(password)>1024:raise ValueError('Identifiants invalides.')
    if setup:
        if not re.fullmatch(r'[A-Za-z0-9_.@-]{3,80}',username) or len(password)<12:raise ValueError('Identifiant de 3 caractères minimum; mot de passe de 12 caractères minimum.')
        salt=secrets.token_hex(16);hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT count(*) FROM users').fetchone()[0]:c.rollback();raise ValueError('Le compte administrateur existe déjà.')
        c.execute('INSERT INTO users VALUES (?,?,?)',(username,salt,hashed))
    else:
        r=c.execute('SELECT salt,hash FROM users WHERE username=?',(username,)).fetchone()
        salt=r[0] if r else '0'*32
        hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()
        if not r or not hmac.compare_digest(hashed,r[1]):
            n=(attempt[0]+1) if attempt and attempt[1]>now else 1
            c.execute('INSERT OR REPLACE INTO attempts VALUES (?,?,?)',(address,n,now+900));c.commit()
            raise ValueError('Identifiant ou mot de passe incorrect.')
    token=secrets.token_urlsafe(32)
    c.execute('DELETE FROM attempts WHERE address=?',(address,));c.execute('DELETE FROM sessions WHERE expires<=?',(now,))
    c.execute('INSERT INTO sessions VALUES (?,?,?)',(digest(token),username,now+8*3600));c.commit()
    return token,username


def logout(c,headers):
    cookie=SimpleCookie();cookie.load(headers.get('Cookie',''))
    if cookie.get('cod_session'):c.execute('DELETE FROM sessions WHERE digest=?',(digest(cookie['cod_session'].value),));c.commit()


def text(p,key,limit=120):
    v=str(p.get(key,'')).strip()
    if not v or len(v)>limit:raise ValueError(f'Champ {key} requis (maximum {limit} caractères).')
    return v


def amount(p,key):
    from app import cents
    v=cents(p.get(key,'0'))
    if v<0:raise ValueError('Les montants doivent être positifs.')
    return v


def save(c,kind,p):
    from app import cents
    if kind=='country':
        code=text(p,'code').upper();name=text(p,'name')
        if not re.fullmatch('[A-Z]{2}',code):raise ValueError('Code pays ISO de 2 lettres attendu.')
        c.execute('INSERT INTO countries VALUES (?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name',(code,name))
    elif kind=='provider':
        sid=text(p,'id');name=text(p,'name')
        if not re.fullmatch('[a-z0-9-]{2,60}',sid):raise ValueError('Identifiant service : lettres minuscules, chiffres et tirets.')
        connector='ecom' if sid=='ecomfulfilment' else 'manual'
        c.execute('INSERT INTO providers VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name',(sid,name,connector))
    elif kind=='store':
        sid=text(p,'id');country=text(p,'country');provider=text(p,'provider');name=text(p,'name');domain=str(p.get('domain','')).strip().lower()
        if not re.fullmatch('[a-z0-9-]{2,60}',sid):raise ValueError('Identifiant boutique invalide.')
        if domain and not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com',domain):raise ValueError('Domaine myshopify.com attendu, sans chemin.')
        if not c.execute('SELECT 1 FROM countries WHERE code=?',(country,)).fetchone() or not c.execute('SELECT 1 FROM providers WHERE id=?',(provider,)).fetchone():raise ValueError('Pays ou service inconnu.')
        old=c.execute('SELECT provider FROM stores WHERE id=?',(sid,)).fetchone()
        if old and old[0]!=provider and c.execute('SELECT 1 FROM service WHERE store=?',(sid,)).fetchone():raise ValueError('Ce store possède déjà des statuts rattachés à un autre service. Une migration explicite est nécessaire.')
        c.execute('INSERT INTO stores VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,country=excluded.country,provider=excluded.provider,domain=excluded.domain',(sid,name,country,provider,domain))
    elif kind in ('spend','tariff','invoice'):
        country=text(p,'country');currency=text(p,'currency')
        if not c.execute('SELECT 1 FROM countries WHERE code=?',(country,)).fetchone():raise ValueError('Pays inconnu.')
        if not re.fullmatch('[A-Z]{3}',currency):raise ValueError('Devise ISO attendue.')
        start=date.fromisoformat(text(p,'from'));end=date.fromisoformat(text(p,'to'))
        if end<start:raise ValueError('Période inversée.')
        body={'country':country,'currency':currency,'from':start.isoformat(),'to':end.isoformat()}
        if kind=='spend':
            body.update(product=text(p,'product',300),store=str(p.get('store','')),amount=amount(p,'amount'))
            if body['store'] and body['store'] not in registry(c):raise ValueError('Store inconnu.')
            valid=[json.loads(r[0]) for r in c.execute('SELECT body FROM orders')]
            if not any(i['name']==body['product'] for o in valid for i in o['items']):raise ValueError('Produit inconnu. Choisir un produit importé.')
        else:
            body['provider']=text(p,'provider')
            if not c.execute('SELECT 1 FROM providers WHERE id=?',(body['provider'],)).fetchone():raise ValueError('Service inconnu.')
            if kind=='invoice':body.update(amount=amount(p,'amount'),reference=text(p,'reference'),category=text(p,'category'))
            else:
                for r in c.execute("SELECT body FROM ledger WHERE kind='tariff'"):
                    old=json.loads(r[0])
                    if all(old[k]==body[k] for k in ['country','currency','provider']) and old['from']<=body['to'] and old['to']>=body['from']:raise ValueError('Un tarif existe déjà sur cette période. Supprimer ou ajuster l’ancien tarif avant remplacement.')
                for key in ['dispatch','delivery','returned','telecom']:body[key]=amount(p,key)
        if kind=='invoice' and any(json.loads(r[0]).get('reference')==body['reference'] and json.loads(r[0]).get('provider')==body['provider'] for r in c.execute("SELECT body FROM ledger WHERE kind='invoice'")):raise ValueError('Facture déjà enregistrée pour ce service.')
        c.execute('INSERT INTO ledger VALUES (?,?,?)',(secrets.token_hex(12),kind,json.dumps(body,ensure_ascii=False)))
    else:raise ValueError('Type inconnu.')
    c.commit()
    return {'saved':True}
