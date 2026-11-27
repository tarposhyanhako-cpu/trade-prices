"""Download yearly trade of ALL reporting countries for the products Armenia trades,
to compare Armenia's prices with the main exporters/importers and the region.

For each year and flow, Comtrade is asked for many 6-digit products at once,
all reporters, and a short list of partners: World plus Armenia's 20 main
partners. The free key allows about 500 calls a day, so the job stops at
MAX_CALLS and continues on the next run. State is kept in data/compare/.
"""
import os, sys, time, json
from pathlib import Path
import requests
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "compare"
BASE = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
MAX_CALLS = int(os.getenv("MAX_CALLS", "440"))
BATCH = int(os.getenv("BATCH", "60"))
YEARS = int(os.getenv("YEARS", "5"))
SLEEP = 1.3
MAX_RECORDS = 100000
KEEP = ["period", "flowCode", "reporterCode", "partnerCode", "cmdCode", "primaryValue", "netWgt"]

KEY = os.getenv("COMTRADE_API_KEY", "").strip()
if not KEY:
    sys.exit("COMTRADE_API_KEY is not set")
S = requests.Session()
S.headers["Ocp-Apim-Subscription-Key"] = KEY
calls = 0
log = []


def get(params, tries=5):
    global calls
    for t in range(tries):
        if calls >= MAX_CALLS:
            raise StopIteration
        time.sleep(SLEEP)
        calls += 1
        r = S.get(BASE, params=params, timeout=240)
        if r.status_code == 200:
            return r.json()
        log.append({"status": r.status_code, "text": r.text[:300], "params": {k: str(v)[:80] for k, v in params.items()}})
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(25 * (t + 1))
            continue
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    raise RuntimeError("too many failed tries")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    arm = pd.concat([pd.read_parquet(f, columns=["period", "flowCode", "partnerCode", "cmdCode", "primaryValue"])
                     for f in sorted(RAW.glob("*.parquet"))], ignore_index=True)
    arm["period"] = arm["period"].astype(str)
    arm["partnerCode"] = pd.to_numeric(arm["partnerCode"], errors="coerce")
    latest_year = int(arm["period"].max()[:4])
    years = list(range(latest_year - YEARS, latest_year))          # complete years only
    recent = arm[(arm["period"] >= f"{years[0]}01") & (arm["partnerCode"] > 0)]
    partners = recent.groupby("partnerCode")["primaryValue"].sum().nlargest(20).index.astype(int).tolist()
    partners = [0] + partners
    products = sorted(arm["cmdCode"].astype(str).str.zfill(6).unique())

    state_f = OUT / "state.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    if state.get("partners") != partners or state.get("years") != years:
        state = {"partners": partners, "years": years, "done": []}
    done = set(state["done"])

    jobs = []
    for y in sorted(years, reverse=True):          # newest year first
        for fl in ("X", "M"):
            for i in range(0, len(products), BATCH):
                jobs.append((y, fl, i))
    todo = [j for j in jobs if f"{j[0]}-{j[1]}-{j[2]}" not in done]
    print(f"Partners: {partners}\nYears: {years}\nJobs left: {len(todo)} of {len(jobs)}", flush=True)

    new = {}

    def fetch(y, fl, codes):
        js = get({"period": y, "flowCode": fl, "cmdCode": ",".join(codes), "partnerCode": ",".join(map(str, partners)),
                  "partner2Code": 0, "customsCode": "C00", "motCode": 0, "maxRecords": MAX_RECORDS})
        rows = js.get("data", []) or []
        if len(rows) >= MAX_RECORDS and len(codes) > 1:     # too many rows: split the batch
            h = len(codes) // 2
            return fetch(y, fl, codes[:h]) + fetch(y, fl, codes[h:])
        return rows

    try:
        for (y, fl, i) in todo:
            rows = fetch(y, fl, products[i:i + BATCH])
            if rows:
                d = pd.DataFrame(rows)
                for c in KEEP:
                    if c not in d:
                        d[c] = None
                new.setdefault((y, fl), []).append(d[KEEP])
            done.add(f"{y}-{fl}-{i}")
            print(f"  {y} {fl} batch {i // BATCH + 1}: {len(rows)} rows (calls {calls})", flush=True)
    except StopIteration:
        print(f"Reached the daily call limit ({MAX_CALLS}); will continue next run", flush=True)
    except Exception as e:
        log.append({"error": str(e)[:300]})
        print(f"Stopped: {e}", flush=True)

    for (y, fl), parts in new.items():
        f = OUT / f"{fl}_{y}.parquet"
        add = pd.concat(parts, ignore_index=True)
        if f.exists():
            add = pd.concat([pd.read_parquet(f), add], ignore_index=True)
        add["cmdCode"] = add["cmdCode"].astype(str).str.zfill(6)
        add = add.drop_duplicates(["period", "flowCode", "reporterCode", "partnerCode", "cmdCode"], keep="last")
        add.to_parquet(f, index=False, compression="zstd")
    state["done"] = sorted(done)
    state["complete"] = len(done) >= len(jobs)
    state_f.write_text(json.dumps(state))
    (OUT / "log.json").write_text(json.dumps({"calls": calls, "jobs_total": len(jobs), "jobs_done": len(done), "errors": log[-20:]}, indent=1))
    print(f"Done for now: {len(done)}/{len(jobs)} jobs, {calls} calls", flush=True)


if __name__ == "__main__":
    main()
