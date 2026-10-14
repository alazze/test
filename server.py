"""Authenticated local HTTP application."""
import json
from urllib.parse import urlsplit
from decimal import InvalidOperation
import app,business


class SecureHandler(app.Handler):
    def do_GET(self):
        if self.headers.get('Host','') not in [f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}']:
            return self.send(403,{'error':'Hôte interdit.'})
        path=urlsplit(self.path).path
        with app.connect() as c:
            if path=='/api/bootstrap':
                return self.send(200,{'needs_setup':not bool(c.execute('SELECT 1 FROM users').fetchone()),'user':business.user(c,self.headers)})
            if path not in ['/','/health'] and not business.user(c,self.headers):
                return self.send(401,{'error':'Connexion requise.'})
        super().do_GET()

    def do_POST(self):
        host=self.headers.get('Host','');origin=self.headers.get('Origin')
        if host not in [f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'] or origin and origin!=f'http://{host}' or self.headers.get('Sec-Fetch-Site')=='cross-site' or self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.send(403,{'error':'Requête de même origine requise.'})
        path=urlsplit(self.path).path
        if path.startswith('/api/import/'):
            with app.connect() as c:
                if not business.user(c,self.headers):return self.send(401,{'error':'Connexion requise.'})
            return super().do_POST()
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=100000:raise ValueError('Requête invalide.')
            p=json.loads(self.rfile.read(length))
            if not isinstance(p,dict):raise ValueError('Objet JSON attendu.')
            with app.connect() as c:
                if path in ['/api/login','/api/setup']:
                    token,name=business.authenticate(c,p,path=='/api/setup',self.client_address[0])
                    self.session_cookie=f'cod_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'
                    return self.send(200,{'user':name})
                if not business.user(c,self.headers):return self.send(401,{'error':'Connexion requise.'})
                if path=='/api/logout':
                    business.logout(c,self.headers);self.session_cookie='cod_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'
                    return self.send(200,{'logged_out':True})
                if path=='/api/ledger/delete':
                    c.execute('DELETE FROM ledger WHERE id=?',(str(p.get('id','')),));c.commit();return self.send(200,{'deleted':True})
                if path.startswith('/api/settings/') or path.startswith('/api/ledger/'):
                    return self.send(200,business.save(c,path.rsplit('/',1)[-1],p))
                self.send(404,{'error':'Endpoint inconnu.'})
        except ValueError as e:self.send(400,{'error':str(e)})
        except (KeyError,TypeError,InvalidOperation):self.send(400,{'error':'Enregistrement refusé. Vérifier les champs, les périodes et les doublons.'})
