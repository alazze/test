import csv
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import ecom_api


def shopify(rows):
    fields = ['Name','Id','Created at','Total','Currency','Shipping Country','Financial Status','Fulfillment Status','Lineitem name','Lineitem quantity','Lineitem price','Lineitem discount','Email','Shipping Phone']
    f=io.StringIO(newline=''); w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows);return f.getvalue().encode()


def primary(id='1', name='#100', product='Watch "Limited", Gold'):
    return {'Name':name,'Id':id,'Created at':'2026-06-01 00:10:00 +0100','Total':'60.00','Currency':'EUR','Shipping Country':'ES','Financial Status':'pending','Fulfillment Status':'fulfilled','Lineitem name':product,'Lineitem quantity':'1','Lineitem price':'50','Email':'private@example.invalid','Shipping Phone':'SECRET_PHONE'}


def service(rows):
    f=io.StringIO();w=csv.DictWriter(f,fieldnames=['store','shopify_id','order_name',*app.FIELDS,'updated_at']);w.writeheader();w.writerows(rows);return f.getvalue().encode()


def state(**kw):
    return dict({'store':'orzein-spain','shopify_id':'1','confirmation_status':'confirmed','shipping_status':'shipped','delivery_status':'delivered','updated_at':'2026-10-01T00:00:00Z'},**kw)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.c=app.connect(Path(self.tmp.name)/'test.sqlite')
    def tearDown(self):
        self.c.close();self.tmp.cleanup()
    def test_multiline_order_reimport_and_no_pii(self):
        r=primary();r['Lineitem name']='Watch "Limited"\nGold'
        extra={'Name':'#100','Created at':r['Created at'],'Lineitem name':'Strap','Lineitem quantity':'2','Lineitem price':'5'}
        blob=shopify([r,extra]);res=app.import_shopify(self.c,blob,'orzein-spain')
        self.assertEqual(res['orders'],1);self.assertEqual(res['lineitems'],2)
        app.import_shopify(self.c,blob,'orzein-spain');o=app.snapshot(self.c)['orders'];self.assertEqual(len(o),1)
        self.assertEqual(o[0]['total'],6000);self.assertEqual(len(o[0]['items']),2);self.assertEqual(sum(i['gross'] for i in o[0]['items']),6000)
        self.assertEqual(o[0]['date'],'2026-06-01');self.assertNotIn('SECRET_PHONE',json.dumps(o));self.assertNotIn('private@example',json.dumps(o))
    def test_store_scoped_ids_and_matching(self):
        for s in ['orzein-spain','orzein-portugal']:app.import_shopify(self.c,shopify([primary()]),s)
        self.assertEqual(len(app.snapshot(self.c)['orders']),2)
        result=app.import_service(self.c,service([state()]));self.assertEqual(result['matched'],1)
        snap=app.snapshot(self.c)['orders'];self.assertEqual(sum(o['service'] is not None for o in snap),1)
    def test_unknown_status_unmatched_and_stale(self):
        app.import_shopify(self.c,shopify([primary()]),'orzein-spain')
        app.import_service(self.c,service([state()]))
        result=app.import_service(self.c,service([state(updated_at='2026-09-01T00:00:00Z',delivery_status='in_transit'),state(shopify_id='999')]))
        self.assertEqual(result['stale'],1);self.assertEqual(len(result['unmatched']),1)
        self.assertEqual(app.snapshot(self.c)['orders'][0]['service']['delivery_status'],'delivered')
        with self.assertRaises(ValueError):app.import_service(self.c,service([state(delivery_status='entregado')]))
    def test_conflicting_snapshot_is_atomic(self):
        app.import_shopify(self.c,shopify([primary()]),'orzein-spain')
        with self.assertRaises(ValueError):app.import_service(self.c,service([state(),state(delivery_status='in_transit')]))
        self.assertIsNone(app.snapshot(self.c)['orders'][0]['service'])
    def test_unmatched_id_does_not_fall_back_to_other_order(self):
        app.import_shopify(self.c,shopify([primary()]),'orzein-spain')
        result=app.import_service(self.c,service([state(shopify_id='999',order_name='#100')]))
        self.assertEqual(result['matched'],0)
    def test_shopify_fulfilled_never_sets_service_delivered(self):
        app.import_shopify(self.c,shopify([primary()]),'orzein-spain')
        self.assertIsNone(app.snapshot(self.c)['orders'][0]['service'])
    def test_standalone_script_injection_escaped(self):
        app.import_shopify(self.c,shopify([primary(product='</script><script>alert(1)</script>')]),'orzein-spain')
        path=app.standalone(self.c,Path(self.tmp.name)/'report.html');text=path.read_text()
        self.assertNotIn('</script><script>alert(1)',text);self.assertIn('\\u003c/script',text)


class APITests(unittest.TestCase):
    def test_pagination_and_total(self):
        pages=[{'data':[{'id':1}], 'current_page':1,'last_page':2,'total':2},{'data':[{'id':2}],'current_page':2,'last_page':2,'total':2}]
        urls=[]
        def fetch(url):urls.append(url);return pages[len(urls)-1]
        self.assertEqual(len(ecom_api.paginate(fetch=fetch)),2);self.assertIn('page=2',urls[1])
    def test_no_silent_truncation(self):
        with self.assertRaises(ValueError):ecom_api.paginate(fetch=lambda u:{'data':[{}]})
        with self.assertRaises(ValueError):ecom_api.paginate(fetch=lambda u:{'data':[{}],'current_page':1,'last_page':1,'total':2})
    def test_credentials_never_sent_to_next_foreign_host(self):
        with self.assertRaises(ValueError):ecom_api.paginate(fetch=lambda u:{'data':[],'next_page_url':'https://other.invalid/api/leads'})
        with self.assertRaises(ValueError):ecom_api.validate_url('https://api.ecomfulfilment.eu/api/leads/delete')
    def test_schema_has_no_values(self):
        shape=ecom_api.structure({'data':[{'email':'private@example.invalid','token':'secret'}]})
        self.assertNotIn('private@example',json.dumps(shape));self.assertNotIn('secret',json.dumps(shape))


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        ecom_api._SESSION.update(token=None, expires=0)
    def tearDown(self):
        ecom_api._SESSION.update(token=None, expires=0)
    def test_official_login_and_memory_cache(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): return b'{"status":true,"token":"FAKE_TEST_TOKEN"}'
        class Opener:
            def __init__(self): self.requests=[]
            def open(self,req,**kw): self.requests.append(req);return Response()
        opener=Opener()
        with patch.dict(ecom_api.os.environ,{'ECOMFULFILMENT_EMAIL':'test@example.invalid','ECOMFULFILMENT_PASSWORD':'FAKE_PASSWORD'},clear=True), patch.object(ecom_api,'build_opener',return_value=opener):
            self.assertEqual(ecom_api.login_token(),'FAKE_TEST_TOKEN')
            self.assertEqual(ecom_api.login_token(),'FAKE_TEST_TOKEN')
            self.assertEqual(len(opener.requests),1)
            req=opener.requests[0];self.assertEqual(req.get_method(),'POST')
            self.assertEqual(ecom_api.urlsplit(req.full_url).hostname,'api.ecomfulfilment.eu')
            ecom_api._SESSION['expires']=0;ecom_api.login_token();self.assertEqual(len(opener.requests),2)
    def test_missing_credentials_do_not_make_network_request(self):
        with patch.dict(ecom_api.os.environ,{},clear=True),patch.object(ecom_api,'build_opener') as opener:
            with self.assertRaises(ValueError):ecom_api.login_token()
            opener.assert_not_called()
    def test_http200_auth_failure_does_not_look_like_data(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return b'{"status":false,"message":"Unauthorized"}'
        class Opener:
            def open(self,*a,**kw):return Response()
        with patch.dict(ecom_api.os.environ,{'ECOMFULFILMENT_TOKEN':'FAKE_TOKEN'},clear=True),patch.object(ecom_api,'build_opener',return_value=Opener()):
            with self.assertRaises(ValueError):ecom_api.get_json(ecom_api.BASE+'/api/leads')
    def test_country_preserved_in_pagination(self):
        urls=[]
        def fetch(url):
            urls.append(url);page=len(urls)
            return {'data':[{'id':page}],'current_page':page,'last_page':2,'total':2}
        self.assertEqual(len(ecom_api.paginate(fetch=fetch,country='PORTUGAL')),2)
        self.assertTrue(all('country=PORTUGAL' in u for u in urls))


if __name__=='__main__':unittest.main()
