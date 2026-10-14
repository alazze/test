import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import shopify_api

class ShopifyTests(unittest.TestCase):
 def test_historical_permission_blocks_partial_history(self):
  calls=[]
  def fetch(q,v=None):calls.append(q);return {'currentAppInstallation':{'accessScopes':[{'handle':'read_orders'}]}}
  with self.assertRaises(ValueError):shopify_api.collect(fetch,'2026-06-01','2026-10-13')
  self.assertEqual(len(calls),1)
 def test_graphql_cursor_loop_refused(self):
  def fetch(q,v=None):
   if v is None:return {'currentAppInstallation':{'accessScopes':[{'handle':'read_orders'},{'handle':'read_all_orders'}]}}
   return {'orders':{'nodes':[],'pageInfo':{'hasNextPage':True,'endCursor':'same'}}}
  with self.assertRaises(ValueError):shopify_api.collect(fetch,'2026-06-01','2026-10-13')
 def test_no_silent_lineitem_truncation(self):
  def fetch(q,v=None):
   if v is None:return {'currentAppInstallation':{'accessScopes':[{'handle':'read_orders'},{'handle':'read_all_orders'}]}}
   return {'orders':{'nodes':[{'lineItems':{'pageInfo':{'hasNextPage':True}}}],'pageInfo':{'hasNextPage':False}}}
  with self.assertRaises(ValueError):shopify_api.collect(fetch,'2026-06-01','2026-10-13')

if __name__=='__main__':unittest.main()
