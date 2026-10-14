"""Optional Chromium integration checks against an isolated copy of the database."""
import sys,tempfile,sqlite3,threading,json,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app,business
from server import SecureHandler
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright,expect

with tempfile.TemporaryDirectory() as temp:
 app.DB=Path(temp)/'fixture.sqlite'
 import csv,io
 with app.connect() as c:
  fields=['Name','Id','Created at','Total','Currency','Shipping Country','Financial Status','Fulfillment Status','Lineitem name','Lineitem quantity','Lineitem price']
  for store,country,n in [('orzein-spain','ES',60),('orzein-portugal','PT',40)]:
   stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
   for i in range(n):
    writer.writerow({'Name':f'#FIXTURE-{i}','Id':str(i+1),'Created at':'2026-06-15T12:00:00Z','Total':'50','Currency':'EUR','Shipping Country':country,'Financial Status':'pending','Fulfillment Status':'fulfilled','Lineitem name':'OZ IMPERIAL CHRONO' if i==0 else f'Fixture product {i}','Lineitem quantity':'1','Lineitem price':'50'})
   app.import_shopify(c,stream.getvalue().encode(),store,'Synthetic test data')
 server=ThreadingHTTPServer(('127.0.0.1',0),SecureHandler);threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
 try:
  urllib.request.urlopen(base+'/api/data');raise AssertionError('Unauthenticated data exposed')
 except urllib.error.HTTPError as e:assert e.code==401
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(base,wait_until='networkidle');expect(page.locator('#auth-title')).to_contain_text('Créer')
  page.fill('#username','fixture-admin');page.fill('#password','Fixture-Password-1234');page.click('#auth-button')
  expect(page.locator('#app-main')).to_be_visible();assert page.locator('#cards .value').nth(0).inner_text().replace('\u202f','')=='100'
  assert page.locator('#filters label').first.inner_text().startswith('Pays')
  page.select_option('#country','IT');assert page.locator('#cards .value').nth(0).inner_text()=='0';page.select_option('#country','ES')
  page.click('[data-tab="productdash"]');assert page.locator('#products tr').count()>30
  page.select_option('#product-filter','OZ IMPERIAL CHRONO');assert page.locator('#products tr').count()==1
  page.select_option('#product-filter','');page.click('[data-tab="expenses"]')
  form=page.locator('form[data-endpoint="/api/ledger/spend"]');form.locator('[name=product]').select_option('OZ IMPERIAL CHRONO');form.locator('[name=country]').select_option('ES');form.locator('[name=from]').fill('2026-06-01');form.locator('[name=to]').fill('2026-06-30');form.locator('[name=amount]').fill('300');form.locator('button').click();expect(form.locator('.save-status')).to_have_text('Enregistré.')
  page.click('[data-tab="overview"]');assert '300,00' in page.locator('#business-summary .value').nth(0).inner_text()
  page.fill('#from','2026-06-10');page.fill('#to','2026-06-19');page.locator('#to').dispatch_event('change');assert '100,00' in page.locator('#business-summary .value').nth(0).inner_text()
  page.fill('#from','2026-06-01');page.fill('#to','2026-10-13');page.locator('#to').dispatch_event('change')
  page.click('[data-tab="settings"]');tariff=page.locator('form[data-endpoint="/api/ledger/tariff"]');tariff.locator('[name=country]').select_option('ES');tariff.locator('[name=from]').fill('2026-06-01');tariff.locator('[name=to]').fill('2026-12-31')
  for key,value in [('dispatch','3'),('delivery','2'),('returned','4'),('telecom','.50')]:tariff.locator(f'[name={key}]').fill(value)
  tariff.locator('button').click();expect(tariff.locator('.save-status')).to_have_text('Enregistré.')
  country=page.locator('form[data-endpoint="/api/settings/country"]');country.locator('[name=code]').fill('DE');country.locator('[name=name]').fill('Allemagne');country.locator('button').click();expect(country.locator('.save-status')).to_have_text('Enregistré.');assert page.locator('#country option[value=DE]').count()==1
  page.click('[data-tab="overview"]');page.select_option('#country','');page.screenshot(path=str(app.ROOT/'data/test-dashboard-desktop.png'),full_page=True)
  page.click('[data-tab="productdash"]');page.screenshot(path=str(app.ROOT/'data/test-products-desktop.png'),full_page=True)
  # Exact allocation across multiple products, including odd cent amounts.
  allocations=page.evaluate("""() => {const o={items:[{name:'A',gross:1},{name:'B',gross:1},{name:'C',gross:1}]};return ['A','B','C'].map(n=>allocated(o,n,5))}""");assert sum(allocations)==5
  page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.click('#logout');expect(page.locator('#auth-screen')).to_be_visible();assert page.locator('#app-main').is_hidden()
  assert not errors,errors;browser.close()
 server.shutdown();server.server_close()
print('Browser checks passed: auth protection, first account, logout, country/product filters, spend period prorating, tariff/country persistence, exact cent allocation and mobile width. All writes were isolated from the user database.')
