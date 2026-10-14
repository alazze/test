# Orzein COD Analytics

Private local dashboard for Shopify cash-on-delivery orders, fulfilment status analysis, advertising spend and service costs. Spain, Portugal and Italy are supported in settings, with additional countries and services configurable.

## Download and run on your PC

1. Click **Code → Download ZIP** on GitHub and extract the entire archive.
2. Install **Python 3.10 or newer** from https://www.python.org/downloads/. On Windows, enable **Add Python to PATH**.
3. On Windows, double-click **START-WINDOWS.bat**. On macOS/Linux, run `sh START-MAC-LINUX.sh` in the extracted folder.
4. Your browser opens **http://localhost:8765**. Keep the startup window open.
5. On first launch, choose your administrator username and a password of at least 12 characters.

No runtime dependencies beyond Python's standard library. No default password. **This repository contains source code only: no customer orders, reports, invoices, advertising spend or real API credentials.** Import your existing Shopify CSV/ZIP exports in **Sources & imports** after signing in.

Alternative startup:

```sh
python app.py serve --port 8765
```

`localhost` means the computer running the program. The local HTTP server is not a hosted website. Do not expose it publicly; a production deployment requires HTTPS, Secure cookies, domain configuration and backups.

## Features

- Protected administrator account; salted PBKDF2 password hashing, eight-hour sessions and logout revocation.
- Country first in filters, plus store, service, product, currency, date range and minimum order age.
- Overview, product dashboard, order list and CSV product export.
- Country/service/store settings; association of stores with providers.
- Advertising spend by product/country/store/day or date range.
- Dated service tariffs and consolidated invoice ledger.
- Shopify CSV and ZIP import; continuation rows are grouped and reimports update orders without adding duplicates.
- Normalized service-state imports with store-scoped matching and timestamp checks.
- Prepared EcomFulfilment and Shopify API adapters, requiring secure credentials and actual-account validation.

Customer names, phone numbers, email addresses and street addresses are excluded from imported analytics data. Local state is retained in `data/analytics.sqlite`, ignored by Git. Back up that folder privately.

## Understanding metrics and costs

Shopify `fulfilled` is not proof of delivery, and payment `pending` is not collected revenue. Service confirmation and delivery rates stay pending until matched service data is available. Confirmation means confirmed / known confirmation states. Delivery means delivered / shipped orders with known delivery states. Partial coverage and recent cohorts can bias rates.

Products are grouped by exact name. A multi-product order appears in each product's order count. Estimated shared costs are allocated by article value using exact cent allocation without double counting. Article values are not collected revenue; Shopify API imports use original unit prices, while CSV imports use available line discounts.

Date-range advertising spend is prorated evenly over calendar days, an estimate of daily spend. If a spend entry has no store, filtered store/provider spend is allocated by its share of matching product orders. Enter individual days for actual daily figures.

Service tariffs cover dispatch, successful delivery, return and telecom per received order. They produce estimates, not actual call counts or invoices. Missing tariffs or states remain visibly unknown. Tariffs for one country/provider/currency cannot overlap. Invoice totals and tariff estimates are kept separate, never added together. Only invoices fully within the selected period are summed; overlapping invoices are flagged. There is no actual invoice allocation by product or store. The service confirmed that its API does not expose individual-order fees.

## API prerequisites

Never paste real passwords or keys into this repository, scripts, commands or issue comments. Configure credentials securely in the runtime. The cloud secret configuration is not copied to your PC.

### EcomFulfilment

Official docs: https://doc.ecomfulfilment.eu/ . Production API: https://api.ecomfulfilment.eu . Authentication is POST `/api/login`; subsequent data calls are read-only. Account credentials are read from `ECOMFULFILMENT_EMAIL` and `ECOMFULFILMENT_PASSWORD`, or an existing bearer token from `ECOMFULFILMENT_TOKEN`. Account-login tokens are kept only in memory and refreshed before their documented eight-hour expiration. Redirects are rejected and credentials/request URLs are not logged.

```sh
python ecom_api.py inspect --endpoint leads --country PORTUGAL
python ecom_api.py inspect --endpoint stores
```

Inspection prints field types only, not account data. Verify store identities, reference fields, status meanings and pagination before creating `data/service-mapping.json`. The mapping uses `store_field`, `stores`, `shopify_id_field` and/or `order_name_field`, `updated_at_field`, and each normalized status as `{ "field": "verified.field", "values": { "verified source status": "normalized_status" } }`.

```sh
python ecom_api.py sync --mapping data/service-mapping.json --country PORTUGAL
```

Only common Laravel pagination is currently supported. Unknown pagination, reference mismatches or invalid states are not silently treated as complete data. No actual-account synchronization or recurring schedule has been validated.

### Shopify

Save the exact myshopify.com domain in store settings. The token environment variable is `SHOPIFY_<INTERNAL_STORE_ID_UPPERCASE_WITH_UNDERSCORES>_TOKEN`. Read permission `read_orders` is required, plus `read_all_orders` for history older than 60 days. The GraphQL API adapter is pinned to 2026-10. Store timezone is fetched for date boundaries. Overlarge line-item lists and broken pagination abort rather than silently truncate.

```sh
python shopify_api.py --store orzein-spain --from 2026-06-01 --to 2026-10-14
```

These adapters are prepared and tested with synthetic responses; exact schema and authorization must still be verified against each real account. Saving domains or adding services does not authenticate them. New providers use normalized imports until a connector is implemented for their documentation.

## Normalized service CSV

```csv
store,shopify_id,order_name,confirmation_status,shipping_status,delivery_status,updated_at
```

Provide `shopify_id` or exact `order_name`, a configured store ID and an ISO timestamp with timezone.

- Confirmation: confirmed, cancelled, pending, unreachable, unknown.
- Shipping: shipped, not_shipped, unknown.
- Delivery: delivered, in_transit, refused, returned, failed, pending, unknown.

Do not infer these states from Shopify fulfillment. Validate their meaning from service data/history. Older snapshots are ignored and unmatched references are reported.

## Tests

```sh
python -m unittest discover -s tests -v
```

Optional browser integration check requires Playwright and Chromium, but these are not runtime dependencies:

```sh
python tests/browser_check.py
```

All test credentials and data are synthetic; the browser check creates a temporary database and does not change your real data. The repository does not contain private report exports or uploaded documentation with credentials.
