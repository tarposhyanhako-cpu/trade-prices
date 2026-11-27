"""Turn the raw Comtrade files into average prices for the web page and downloads.

Average price for a period = total value / total quantity over that period.
Price per kg uses net weight. Price per unit uses the product's own unit (litres,
pieces, ...) when Comtrade gives one.

Rows with an extreme unit value (more than 10 times above or below the median for
that product and flow) are left out of the price, because they are usually
recording errors. They stay in the trade value.
"""
import json, re
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "site" / "data"
TOP_PARTNERS = 15
OUTLIER_FACTOR = 10.0
FLOWS = {"X": "Export", "M": "Import"}


def load():
    df = pd.concat([pd.read_parquet(f) for f in sorted(RAW.glob("*.parquet"))], ignore_index=True)
    df["period"] = df["period"].astype(str)
    for c in ("primaryValue", "netWgt", "qty"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["partnerCode"] = pd.to_numeric(df["partnerCode"], errors="coerce").fillna(-1).astype(int)
    df = df[(df["partnerCode"] != 0) & df["flowCode"].isin(FLOWS) & (df["primaryValue"] > 0)]
    df["cmdCode"] = df["cmdCode"].astype(str).str.zfill(6)
    df["qtyUnitAbbr"] = df["qtyUnitAbbr"].fillna("N/A")
    return df


def flag_outliers(df):
    w = df["netWgt"] > 0
    df["uv"] = np.where(w, df["primaryValue"] / df["netWgt"].where(w), np.nan)
    med = df.groupby(["cmdCode", "flowCode"])["uv"].transform("median")
    df["okw"] = w & (df["uv"] <= med * OUTLIER_FACTOR) & (df["uv"] >= med / OUTLIER_FACTOR)
    uq = (df["qty"] > 0) & ~df["qtyUnitAbbr"].isin(["N/A", "kg"])
    df["uvq"] = np.where(uq, df["primaryValue"] / df["qty"].where(uq), np.nan)
    medq = df.groupby(["cmdCode", "flowCode"])["uvq"].transform("median")
    df["okq"] = uq & (df["uvq"] <= medq * OUTLIER_FACTOR) & (df["uvq"] >= medq / OUTLIER_FACTOR)
    return df


def month_add(p, k):
    y, m = int(p[:4]), int(p[4:])
    t = y * 12 + (m - 1) + k
    return f"{t // 12}{t % 12 + 1:02d}"


def windows(latest, first):
    w = {}
    for n in (3, 12, 36):
        w[f"{n}m"] = (month_add(latest, -(n - 1)), latest, f"Last {n} months")
    for y in range(int(latest[:4]), int(first[:4]) - 1, -1):
        lo, hi = f"{y}01", f"{y}12"
        label = str(y) if hi <= latest else f"{y} (Jan to {date(2000, int(latest[4:]), 1):%b})"
        w[str(y)] = (lo, min(hi, latest), label)
    return w


def agg(g):
    v = g["primaryValue"].sum()
    vw = g.loc[g["okw"], "primaryValue"].sum(); kg = g.loc[g["okw"], "netWgt"].sum()
    vq = g.loc[g["okq"], "primaryValue"].sum(); q = g.loc[g["okq"], "qty"].sum()
    return {"v": round(float(v), 0),
            "kg": round(float(kg), 0),
            "pkg": round(float(vw / kg), 4) if kg > 0 else None,
            "q": round(float(q), 0),
            "pq": round(float(vq / q), 4) if q > 0 else None,
            "m": int(g["period"].nunique())}


def short(s, n=110):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def main():
    df = flag_outliers(load())
    latest, first = df["period"].max(), df["period"].min()
    W = windows(latest, first)
    (OUT / "p").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "p").glob("*.json"):
        old.unlink()

    products, long_rows = [], []
    for hs, g in df.groupby("cmdCode", sort=True):
        desc = g["cmdDesc"].dropna().iloc[-1] if g["cmdDesc"].notna().any() else ""
        rec = {"hs": hs, "d": short(desc, 300), "f": {}}
        for fl, gf in g.groupby("flowCode"):
            unit = gf.loc[gf["okq"], "qtyUnitAbbr"].mode()
            unit = unit.iloc[0] if len(unit) else None
            fr = {"unit": unit, "w": {}, "p": {}}
            for wk, (lo, hi, _) in W.items():
                gw = gf[(gf["period"] >= lo) & (gf["period"] <= hi)]
                if gw.empty:
                    continue
                tot = agg(gw)
                fr["w"][wk] = tot
                long_rows.append([hs, desc, FLOWS[fl], "All partners", wk, tot, unit])
                parts = []
                for (pc, pn), gp in gw.groupby(["partnerCode", "partnerDesc"]):
                    a = agg(gp); a["n"] = pn
                    parts.append(a)
                    if wk in ("12m", "36m"):
                        long_rows.append([hs, desc, FLOWS[fl], pn, wk, a, unit])
                parts.sort(key=lambda a: -a["v"])
                fr["p"][wk] = parts[:TOP_PARTNERS]
            rec["f"][fl] = fr
        (OUT / "p" / f"{hs}.json").write_text(json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
        products.append([hs, short(desc), sorted(rec["f"].keys()),
                         {fl: rec["f"][fl]["w"].get("12m", {}).get("v", 0) for fl in rec["f"]}])

    meta = {"latest": latest, "first": first, "built": date.today().isoformat(),
            "windows": [[k, v[2], v[0], v[1]] for k, v in W.items()],
            "n_products": len(products), "outlier_factor": OUTLIER_FACTOR}
    (OUT / "products.json").write_text(json.dumps({"meta": meta, "products": products},
                                                  separators=(",", ":"), ensure_ascii=False))

    cols = ["HS6", "Product", "Flow", "Partner", "Period", "Value (USD)", "Net weight (kg)",
            "Average price (USD per kg)", "Quantity", "Unit", "Average price (USD per unit)", "Months with trade"]
    lab = {k: v[2] for k, v in W.items()}
    rows = [[hs, d, fl, pn, lab[wk], a["v"], a["kg"], a["pkg"], a["q"], u if a["pq"] else None, a["pq"], a["m"]]
            for hs, d, fl, pn, wk, a, u in long_rows]
    t = pd.DataFrame(rows, columns=cols)
    t.to_csv(OUT / "armenia_trade_prices.csv", index=False)
    with pd.ExcelWriter(OUT / "armenia_trade_prices.xlsx", engine="openpyxl") as xw:
        pd.DataFrame({"About": [
            "Average export and import prices of Armenia, by 6-digit HS product and partner country.",
            "Source: UN Comtrade, monthly data reported by Armenia.",
            "Average price = total value / total quantity over the period.",
            f"Rows with a unit value more than {OUTLIER_FACTOR:.0f} times above or below the product median are left out of the price.",
            f"Data up to {latest[:4]}-{latest[4:]}. Built {meta['built']}."]}).to_excel(xw, sheet_name="About", index=False)
        for wk in ("12m", "36m"):
            t[t["Period"] == lab[wk]].to_excel(xw, sheet_name=lab[wk], index=False)
    print(f"Built {len(products)} products, data {first} to {latest}, {len(t)} download rows")


if __name__ == "__main__":
    main()
