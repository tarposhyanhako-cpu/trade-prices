# Average export and import prices, Armenia

Average price of any 6-digit HS product that Armenia exports or imports, by partner country and period.

**Average price = total value ÷ total quantity over the period**, from monthly data Armenia reports to UN Comtrade.

## How it runs
- `scripts/fetch.py` downloads new months from the UN Comtrade API (key in the repo secret `COMTRADE_API_KEY`) into `data/raw/`.
- `scripts/build.py` calculates the averages into `site/data/`.
- `.github/workflows/update.yml` runs both on the 5th of every month and publishes `site/` on GitHub Pages. You can also run it by hand from the Actions tab ("Run workflow").

## Rules
- Price per kg uses net weight. Price per unit uses the product's own unit (litres, pieces…) when Comtrade gives one.
- Rows with a unit value more than 10 times above or below the product's median are left out of the price (usually recording errors).
- The latest 6 months are downloaded again on every run, because countries revise recent data.
