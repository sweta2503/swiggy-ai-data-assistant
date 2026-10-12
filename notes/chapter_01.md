# Chapter 01: Synthetic data and its defects

**What we build:** `make data` writes about 100 MB of Swiggy-style source files into
`data/raw/` in about 3 seconds: 150,000 orders over 12 months, 480k order lines,
payments, refunds, ratings, 25,000 customers (daily snapshots), 1,800 restaurants,
3,500 delivery partners and ~72,000 menu items. Same seed gives byte-identical files.

**Why it matters at work (and why generated data beats toy CSVs):** a clean CSV teaches nothing. Real sources arrive
duplicated, late, in different time zones and with five spellings of "delivered". We inject
nine defects on purpose and record every one in `data/raw/_defects.json`, so later chapters
can prove the pipeline fixed each of them.

| # | Defect | Where it shows up |
|---|--------|-------------------|
| 1 | ~1.5% duplicate order rows (some in the next day's file) | `orders/*.jsonl` |
| 2 | ~2% of orders arrive 1-2 days late | file date is after `order_ts` |
| 3 | Status spellings: `DLVRD`, `CANCELED`, `Delivered`... | `status` |
| 4 | Orders are UTC (`...Z`), payments are IST with no marker | `order_ts` vs `payment_ts` |
| 5 | Blank city on some customers, blank cuisine on 5 restaurants | snapshots, `restaurants.csv` |
| 6 | Negative or absurd `delivery_minutes` (-30, 9999) | `orders/*.jsonl` |
| 7 | Order items whose order does not exist | `order_items/*.csv` |
| 8 | 3% of customers change city, 8% change segment | customer snapshots |
| 9 | Real-looking PII (phone, email, address) | `customers/*.csv` |

**Stories hidden in the data** (so analytics questions have real answers), listed in
`_meta.json`: lunch and dinner peaks, weekend lift, two festival spikes, steady growth,
Mumbai deliveries slower in the monsoon, "Healthy" is the fastest-growing cuisine, and in
the latest full month a cluster of 12 Mumbai restaurants causes a refund spike with reason
`SPOILED_FOOD`.

**Commands:** `make data`, `make test-01`

**Common mistakes**
- Counting duplicate copies as extra late or messy rows. The recorded counts are per
  unique order. The tests dedupe first.
- Forgetting that the orders file is named by *UTC* date. An order at 02:00 IST belongs to
  the previous UTC day's file.
- Using today's date in tests. Tests pass a fixed `end_date` so results never drift.

**Design choice:** customer snapshot files hold only customers who are new or changed that
day (plus everyone on day one). That gives SCD2 every change with its date without writing
9 million rows.

**Interview line:** "I generated seeded data with known injected defects and a ledger of
them, so every pipeline test can assert exact counts instead of eyeballing."
