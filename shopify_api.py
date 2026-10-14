"""Read-only Shopify GraphQL import, using securely configured store credentials."""
import argparse,csv,io,json,os,re
from datetime import date,datetime,time,timezone
from zoneinfo import ZoneInfo
from urllib.request import Request,build_opener
from urllib.error import HTTPError,URLError
import app
from ecom_api import NoRedirect

VERSION='2026-10'
QUERY='''query Orders($after:String,$filter:String!){orders(first:100,after:$after,query:$filter,sortKey:CREATED_AT){pageInfo{hasNextPage endCursor} nodes{id legacyResourceId name createdAt cancelledAt displayFinancialStatus displayFulfillmentStatus currencyCode totalPriceSet{shopMoney{amount}} totalRefundedSet{shopMoney{amount}} shippingAddress{countryCodeV2} lineItems(first:250){pageInfo{hasNextPage} nodes{name sku quantity originalUnitPriceSet{shopMoney{amount}}}}}}}'''


def graphql(domain,token,query,variables=None):
 if not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com',domain):raise ValueError('Domaine Shopify exact non configuré.')
 req=Request(f'https://{domain}/admin/api/{VERSION}/graphql.json',data=json.dumps({'query':query,'variables':variables or {}}).encode(),headers={'Content-Type':'application/json','X-Shopify-Access-Token':token},method='POST')
 try:
  with build_opener(NoRedirect()).open(req,timeout=45) as r:
   raw=r.read(20*1024*1024+1)
   if len(raw)>20*1024*1024:raise ValueError('Réponse Shopify trop volumineuse.')
   result=json.loads(raw)
 except (HTTPError,URLError,UnicodeError,json.JSONDecodeError):raise ValueError('Connexion Shopify refusée ou réponse invalide; aucune donnée ni clé journalisée.') from None
 if not isinstance(result,dict) or result.get('errors') or not isinstance(result.get('data'),dict):raise ValueError('Requête Shopify non validée; vérifier les scopes et la version API.')
 return result['data']


def collect(fetch,start,end,timezone_name='UTC'):
 start=date.fromisoformat(start);end=date.fromisoformat(end)
 if start>end:raise ValueError('Période inversée.')
 scopes=fetch('{currentAppInstallation{accessScopes{handle}}}')
 allowed={s['handle'] for s in scopes['currentAppInstallation']['accessScopes']}
 if 'read_orders' not in allowed and 'write_orders' not in allowed:raise ValueError('Permission read_orders manquante.')
 if (date.today()-start).days>60 and 'read_all_orders' not in allowed:raise ValueError('Historique de plus de 60 jours : read_all_orders requis. Ne pas considérer un import tronqué comme complet.')
 zone=ZoneInfo(timezone_name)
 lower=datetime.combine(start,time.min,zone).astimezone(timezone.utc).isoformat()
 upper=datetime.combine(end,time.max,zone).astimezone(timezone.utc).isoformat()
 after=None;seen=set();orders=[]
 for _ in range(1000):
  d=fetch(QUERY,{'after':after,'filter':f"created_at:>='{lower}' created_at:<='{upper}'"})['orders']
  for o in d['nodes']:
   if o['lineItems']['pageInfo']['hasNextPage']:raise ValueError('Une commande dépasse 250 lignes; pagination des articles nécessaire avant tout import.')
   orders.append(o)
  info=d['pageInfo']
  if not info['hasNextPage']:return orders
  after=info['endCursor']
  if not after or after in seen:raise ValueError('Pagination Shopify incohérente.')
  seen.add(after)
 raise ValueError('Limite de pagination Shopify atteinte; aucun import effectué.')


def as_csv(orders,timezone_name='UTC'):
 fields=['Name','Id','Created at','Cancelled at','Financial Status','Fulfillment Status','Currency','Shipping Country','Total','Refunded Amount','Lineitem name','Lineitem sku','Lineitem quantity','Lineitem price']
 stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
 for o in orders:
  for index,i in enumerate(o['lineItems']['nodes']):
   row={'Name':o['name'],'Lineitem name':i['name'],'Lineitem sku':i['sku'],'Lineitem quantity':i['quantity'],'Lineitem price':i['originalUnitPriceSet']['shopMoney']['amount'],'Created at':datetime.fromisoformat(o['createdAt'].replace('Z','+00:00')).astimezone(ZoneInfo(timezone_name)).isoformat()}
   if index==0:row.update({'Id':str(o['legacyResourceId']),'Cancelled at':o['cancelledAt'] or '', 'Financial Status':o['displayFinancialStatus'].lower(),'Fulfillment Status':'partial' if o['displayFulfillmentStatus']=='PARTIALLY_FULFILLED' else o['displayFulfillmentStatus'].lower(),'Currency':o['currencyCode'],'Shipping Country':(o['shippingAddress'] or {}).get('countryCodeV2',''),'Total':o['totalPriceSet']['shopMoney']['amount'],'Refunded Amount':o['totalRefundedSet']['shopMoney']['amount']})
   writer.writerow(row)
  if not o['lineItems']['nodes']:raise ValueError('Commande sans articles; mapping spécifique nécessaire avant import.')
 return stream.getvalue().encode()


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--store',required=True);p.add_argument('--from',dest='start',required=True);p.add_argument('--to',dest='end',required=True);a=p.parse_args()
 try:
  with app.connect() as c:
   store=c.execute('SELECT domain FROM stores WHERE id=?',(a.store,)).fetchone()
   if not store or not store[0]:raise ValueError('Domaine exact du store manquant dans les paramètres.')
   env='SHOPIFY_'+a.store.upper().replace('-','_')+'_TOKEN';token=os.environ.get(env)
   if not token:raise ValueError(f'Secret {env} non configuré pour le domaine du store.')
   fetch=lambda query,variables=None:graphql(store[0],token,query,variables)
   tz=fetch('{shop{ianaTimezone}}')['shop']['ianaTimezone']
   orders=collect(fetch,a.start,a.end,tz)
   if not orders:print(json.dumps({'orders':0,'imported':False}));return
   print(json.dumps(app.import_shopify(c,as_csv(orders,tz),a.store,'API Shopify')))
 except (ValueError,KeyError,TypeError):
  print('Synchronisation Shopify non validée. Vérifier domaine, secret, permissions, période, schéma et pagination. Aucun détail privé journalisé.');raise SystemExit(1)

if __name__=='__main__':main()
