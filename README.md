# P&G Croatia — Retail Price & Assortment Monitor

A daily, fully automated dashboard tracking P&G vs. competitor pricing and
likely out-of-stock products across **Konzum, Kaufland, Spar, Lidl and dm**,
built from each retailer's legally-mandated public daily price list
(Odluka o objavi cjenika, NN 75/2025).

**How it runs, once set up: nothing to run.** A free scheduled job on
GitHub's infrastructure crawls all five retailers every morning, and a free
static page (GitHub Pages) shows the result. Your manager bookmarks one URL.
No Excel, no macros, no installs, no laptop that has to stay on — and it
keeps running after you leave P&G, as long as the GitHub repo exists.

## What the dashboard shows

Filter by retailer (or all four) and category at the top; every section follows it.

- **Same P&G product, different retailer** — identical barcodes priced at each
  retailer, and how much more each retailer typically charges than the cheapest.
- **Category shelf** — every SKU in a category as a dot on a price-per-unit
  scale, grouped by brand.
- **Share of assortment** — P&G's share of the tracked SKUs per category.
- **Distribution (TDP)** — products × stores listing them, by brand and retailer.
- **Missing products** — P&G SKUs gone from a store's list 3+ days, filterable
  by brand, with a flag when the product was on promotion just before it vanished.
- **Store check** — one store's P&G portfolio vs. the chain's core range.
- **On promotion today** and the **SKU explorer** (with EAN codes throughout).

## What "likely out of stock" means

There is no warehouse inventory feed here — only the public price list. A
product is flagged when it's **missing from a store's published price list
for 3+ consecutive publication days**. That's a proxy, not a certainty: a
retailer could also have simply delisted the product. Treat flags as "worth
checking," not as fact.

## One-time setup (about 15–20 minutes)

You'll need a GitHub account — a free personal one is fine, or your team's
if P&G already uses GitHub Enterprise. Nothing here needs IT approval or a
paid tier.

1. **Create the repo.** On github.com, click "New repository." Name it
   something like `pg-croatia-price-monitor`. Set it to **Private** (keeps
   it off public search, though note the published dashboard page itself
   will be reachable by anyone with its exact link — see "A note on
   privacy" below). Don't initialize with a README (we already have one).

2. **Push this code.** From this folder:
   ```bash
   git init
   git add .
   git commit -m "Initial commit: P&G price/assortment monitor"
   git branch -M main
   git remote add origin https://github.com/<your-username>/pg-croatia-price-monitor.git
   git push -u origin main
   ```

3. **Enable GitHub Pages.** In the repo: Settings → Pages → under "Build
   and deployment," set **Source: GitHub Actions**. (Not "Deploy from a
   branch" — the workflow here deploys via Actions directly.)

4. **Enable Actions** if prompted (Settings → Actions → General → allow
   all actions). Public/private free repos both get free Actions minutes;
   one crawl run takes a couple of minutes, well within the free monthly
   allowance for a solo daily job.

5. **Trigger the first run manually** so you don't wait for tomorrow's
   schedule: repo → Actions tab → "Daily price & assortment crawl" →
   "Run workflow." Watch it go green (takes ~2–5 minutes).

6. **Get the dashboard link.** Settings → Pages will now show "Your site
   is live at `https://<your-username>.github.io/pg-croatia-price-monitor/`".
   That's the link for your manager. Bookmark it, or turn it into a company
   short link if you like.

That's it — from here it runs itself every day at 05:00 and 08:00 UTC
(06:00–07:00 and 09:00–10:00 Croatian time, adjusted automatically for
daylight saving since the cron times are UTC). Two runs a day because not
every retailer reliably publishes by 8am local as required; the second run
catches stragglers. Edit `.github/workflows/daily.yml` if you want to
change the schedule.

## The "missing 3+ days" flag needs a few days of history first

The very first few days after setup, flags will be sparse or absent —
there isn't enough history yet to know something has been missing for 3
consecutive days. This corrects itself automatically as daily snapshots
accumulate in `data/history/`. Nothing to do here, just expect an
initially-quiet dashboard.

## A note on privacy / licensing

- The dashboard's URL is not indexed or advertised, but GitHub Pages pages
  are not access-controlled even on a private repo — anyone with the exact
  link can view it. It contains public retail price data (not anything
  confidential), so this is low risk, but don't post the link somewhere
  public.
- The four retailer crawlers in `crawler_vendor/` are adapted from the
  open-source [`cijene-api`](https://github.com/senko/cijene-api) project
  (AGPL-3.0, license included as `crawler_vendor/LICENSE-AGPL-3.0`). That's
  fine for this internal use; if this project is ever redistributed or
  turned into a service other companies use, the AGPL's source-sharing
  terms would apply — flag that to legal if it comes up.

## Extending brand coverage

`config.py` has a `PG_BRANDS` set. If a report shows a P&G product not
being tracked, it's almost always a brand spelling variant missing from
that set — add it there.

## dm and BIPA

dm publishes one **national** price list per day (an Excel file on dm.hr)
rather than one per store, so dm appears as a single "store": its prices,
promotions and the same-product comparison work like any retailer's, but the
store-level views (TDP, missing products per store, store check) don't apply.

BIPA could not be added: as of October 2026 it doesn't publish a daily price
list anywhere we could find (not on bipa.hr, and its online catalogue shows no
prices or barcodes). If BIPA starts publishing, a crawler can be added the
same way as dm.

## Adding another retailer later

Add a new file to `crawler_vendor/` following the pattern in `konzum.py` /
`kaufland.py` / `spar.py` / `lidl.py` (a class named `<Name>Crawler`
implementing `get_all_products(date) -> list[Store]`), then add its lowercase
name to `RETAILERS` in `config.py`. The `cijene-api` project this was
adapted from already supports ~25 more Croatian chains (Plodine, Tommy,
Studenac, dm, Eurospin, etc.) if you want to pull in more crawlers rather
than write new ones.

## Running locally (for testing/debugging only — not needed day-to-day)

```bash
pip install -r requirements.txt
python scrape_and_export.py --date 2026-09-28          # all 4 retailers
python scrape_and_export.py --retailers konzum         # just one
python tests/test_pipeline_smoke.py                    # offline logic test
python tests/test_export_integration.py                # offline wiring test
```

Note: retailer websites are sometimes unreachable from restrictive
corporate/dev networks (this was true from the sandbox this tool was built
in). GitHub's own Actions runners have normal internet access, so the
scheduled job is unaffected even if your own laptop can't reach these
sites directly.
