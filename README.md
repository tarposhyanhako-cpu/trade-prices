# Average export and import prices, Armenia

Average price of any 6-digit HS product that Armenia exports or imports, by partner country and period,
and a comparison with the main exporters/importers of that product and the region.

**Average price = total value ÷ total quantity over the period.** Source: UN Comtrade.

Everything runs in **R**:

| Script | What it does |
|---|---|
| `R/fetch.R` | Downloads Armenia's monthly trade (all 6-digit products, all partners, exports and imports, from 2016) into `data/raw/<year>.csv.gz`. Only new months, plus the last 6 months again because data gets revised. |
| `R/fetch_compare.R` | Downloads yearly trade of all reporting countries for the products Armenia trades (last 5 complete years, partners: World + Armenia's 20 main partners) into `data/compare/`. Stops at the free daily limit and continues the next day. |
| `R/build.R` | Prepares the page data in `site/data/` (one file per product) and the Excel/CSV downloads. The page computes prices with the user's filters. |

Packages: `httr2`, `jsonlite`, `data.table`, `openxlsx`, `R.utils`.

## Automation (GitHub Actions)
- `update.yml`: on the 5th of every month runs `fetch.R` and `build.R` and publishes `site/` on GitHub Pages.
- `compare.yml`: every night runs `fetch_compare.R` until the comparison data is complete, then rebuilds.
- The UN Comtrade key is the repository secret `COMTRADE_API_KEY`.

## Run it yourself
```r
Sys.setenv(COMTRADE_API_KEY = "your key")
source("R/fetch.R"); source("R/fetch_compare.R"); source("R/build.R")
```

## Rules
- Price per kg uses net weight. Price per unit uses the product's own unit (litres, pieces…) when Comtrade gives one.
- Users can remove extreme prices (monthly records more than 3, 5 or 10 times above or below the median) and small shipments.
- Country names in Armenian: Armstat where available, otherwise Unicode CLDR.
