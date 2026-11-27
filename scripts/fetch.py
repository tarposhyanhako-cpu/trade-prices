"""Download Armenia's monthly trade by 6-digit HS product and partner from UN Comtrade.

Stores one compact Parquet file per year in data/raw/. Months already stored are
skipped, except the most recent REFRESH_MONTHS, which are downloaded again because
countries revise recent data.

Needs the environment variable COMTRADE_API_KEY (free key from comtrade.un.org).
"""
import os, sys, time, json
from pathlib import Path
import requests
import pandas as pd

REPORTER = 51            # Armenia (M49 code)
START = "201601"         # first month to collect
REFRESH_MONTHS = 6       # re-download the latest months on every run
SLEEP = 1.2              # seconds between calls (free key: about 1 call per second)
MAX_RECORDS = 100000     # largest answer allowed for a free key
BASE = "https://comtradeapi.un.org/data/v1"
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
KEEP = ["period", "flowCode", "partnerCode", "partnerDesc", "cmdCode", "cmdDesc",
        "primaryValue", "netWgt", "qty", "qtyUnitAbbr", "isQtyEstimated", "isNetWgtEstimated"]

KEY = os.getenv("COMTRADE_API_KEY", "").strip()
if not KEY:
    sys.exit("COMTRADE_API_KEY is not set")
S = requests.Session()
S.headers["Ocp-Apim-Subscription-Key"] = KEY
calls = 0


def get(url, params, tries=6):
    global calls
    for t in range(tries):
        time.sleep(SLEEP)
        calls += 1
        r = S.get(url, params=params, timeout=180)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            wait = 20 * (t + 1)
            print(f"  HTTP {r.status_code}, waiting {wait}s", flush=True)
            time.sleep(wait)
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    raise RuntimeError("too many failed tries")


def available_months():
    """Months for which Armenia's monthly HS data exists in Comtrade."""
    try:
        js = get(f"{BASE}/getDA/C/M/HS", {"reporterCode": REPORTER})
        months = sorted({str(d["period"]) for d in js.get("data", []) if str(d.get("period", "")) >= START})
        if months:
            return months
    except Exception as e:
        print(f"Availability check failed ({e}); trying every month instead", flush=True)
    # Fallback: every month from START to two months ago; empty months are skipped later.
    import datetime as dt
    t = dt.date.today()
    end = (t.year * 12 + t.month - 1) - 2
    y, m = int(START[:4]), int(START[4:])
    out, k = [], y * 12 + m - 1
    while k <= end:
        out.append(f"{k // 12}{k % 12 + 1:02d}")
        k += 1
    return out


def fetch_month(period, flow):
    params = {"reporterCode": REPORTER, "period": period, "flowCode": flow, "cmdCode": "AG6",
              "partner2Code": 0, "customsCode": "C00", "motCode": 0,
              "includeDesc": "true", "maxRecords": MAX_RECORDS}
    js = get(f"{BASE}/get/C/M/HS", params)
    rows = js.get("data", []) or []
    if len(rows) >= MAX_RECORDS:
        print(f"  WARNING {period} {flow}: hit {MAX_RECORDS} rows, answer may be cut", flush=True)
    if not rows:
        return pd.DataFrame(columns=KEEP)
    df = pd.DataFrame(rows)
    for c in KEEP:
        if c not in df:
            df[c] = None
    return df[KEEP]


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    months = available_months()
    print(f"Comtrade has {len(months)} monthly periods for Armenia since {START}: "
          f"{months[0] if months else '-'} to {months[-1] if months else '-'}", flush=True)
    if not months:
        sys.exit("No monthly data found")

    stored = {}
    for f in RAW.glob("*.parquet"):
        d = pd.read_parquet(f, columns=["period"])
        stored[f.stem] = set(d["period"].astype(str))
    have = set().union(*stored.values()) if stored else set()
    refresh = set(months[-REFRESH_MONTHS:])
    todo = [m for m in months if m not in have or m in refresh]
    print(f"Months to download: {len(todo)} (about {2 * len(todo)} calls)", flush=True)

    new = {}
    for m in todo:
        parts = []
        for flow in ("X", "M"):
            d = fetch_month(m, flow)
            print(f"  {m} {flow}: {len(d)} rows", flush=True)
            parts.append(d)
        new.setdefault(m[:4], []).append(pd.concat(parts, ignore_index=True))

    for year, parts in new.items():
        f = RAW / f"{year}.parquet"
        add = pd.concat(parts, ignore_index=True)
        if f.exists():
            old = pd.read_parquet(f)
            old = old[~old["period"].astype(str).isin(set(add["period"].astype(str)))]
            add = pd.concat([old, add], ignore_index=True)
        add["period"] = add["period"].astype(str)
        add.to_parquet(f, index=False, compression="zstd")
        print(f"Saved {f.name}: {len(add)} rows", flush=True)

    (RAW.parent / "fetch_log.json").write_text(json.dumps(
        {"months_available": months, "downloaded": todo, "api_calls": calls}, indent=1))
    print(f"Done. API calls used: {calls}", flush=True)


if __name__ == "__main__":
    main()
