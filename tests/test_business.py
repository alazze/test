import unittest,tempfile,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app,business

class BusinessTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.c=app.connect(Path(self.tmp.name)/'db.sqlite')
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def test_account_password_hash_and_revoked_session(self):
  payload={'username':'fixture-admin','password':'Fixture-Password-1234'}
  token,user=business.authenticate(self.c,payload,True)
  row=self.c.execute('SELECT * FROM users').fetchone();self.assertNotIn(payload['password'],str(row));self.assertEqual(business.user(self.c,{'Cookie':'cod_session='+token}),'fixture-admin')
  with self.assertRaises(ValueError):business.authenticate(self.c,payload,True)
  business.logout(self.c,{'Cookie':'cod_session='+token});self.assertIsNone(business.user(self.c,{'Cookie':'cod_session='+token}))
  with self.assertRaises(ValueError):business.authenticate(self.c,{'username':'fixture-admin','password':'wrong'})
 def test_invoice_double_entry_and_tariff_overlap_rejected(self):
  p={'country':'ES','currency':'EUR','from':'2026-06-01','to':'2026-06-30','provider':'ecomfulfilment','reference':'fixture-1','category':'service','amount':'123.45'}
  business.save(self.c,'invoice',p)
  with self.assertRaises(ValueError):business.save(self.c,'invoice',p)
  p.update(dispatch='2',delivery='3',returned='4',telecom='.50');business.save(self.c,'tariff',p)
  with self.assertRaises(ValueError):business.save(self.c,'tariff',p)
  self.assertEqual(len(business.metadata(self.c)['ledger']),2)
 def test_new_country_service_and_store_import(self):
  business.save(self.c,'country',{'code':'DE','name':'Allemagne'})
  business.save(self.c,'provider',{'id':'fixture-service','name':'Service fixture'})
  business.save(self.c,'store',{'id':'fixture-store','name':'Fixture Store','country':'DE','provider':'fixture-service','domain':'fixture.myshopify.com'})
  self.assertIn('fixture-store',business.registry(self.c));self.assertEqual(len(business.metadata(self.c)['countries']),4)
 def test_negative_fee_and_inverted_dates_rejected(self):
  p={'country':'ES','currency':'EUR','from':'2026-06-01','to':'2026-06-30','provider':'ecomfulfilment','dispatch':'-2','delivery':'3','returned':'4','telecom':'.50'}
  with self.assertRaises(ValueError):business.save(self.c,'tariff',p)
  p.update(dispatch='2',to='2026-05-01')
  with self.assertRaises(ValueError):business.save(self.c,'tariff',p)

if __name__=='__main__':unittest.main()
