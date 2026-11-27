"""Prepare the page data from the raw Comtrade files.

The page computes average prices itself (total value / total quantity), so it can
apply the user's filters: period, countries, extreme prices, small shipments.
For that, each product gets one small file with its monthly rows by partner.

Outputs in site/data/:
  products.json      list of products, names in English and Armenian, meta data
  partners.json      partner countries: Armenian and English names, capital location
  p/<hs6>.json       monthly rows: [month index, partner code, value USD, net kg, quantity]
  armenia_trade_prices.xlsx / .csv   simple averages for the last 12 and 36 months
"""
import json, re, shutil
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
REF = ROOT / "data" / "ref"
OUT = ROOT / "site" / "data"
FLOWS = {"X": "Export", "M": "Import"}


def load():
    df = pd.concat([pd.read_parquet(f) for f in sorted(RAW.glob("*.parquet"))], ignore_index=True)
    df["period"] = df["period"].astype(str)
    for c in ("primaryValue", "netWgt", "qty"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["partnerCode"] = pd.to_numeric(df["partnerCode"], errors="coerce").fillna(-1).astype(int)
    df = df[(df["partnerCode"] > 0) & df["flowCode"].isin(FLOWS) & (df["primaryValue"] > 0)].copy()
    df["cmdCode"] = df["cmdCode"].astype(str).str.zfill(6)
    df["qtyUnitAbbr"] = df["qtyUnitAbbr"].fillna("N/A")
    return df


def month_index(p, first):
    return (int(p[:4]) - int(first[:4])) * 12 + int(p[4:]) - int(first[4:])


def short(s, n=160):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def main():
    df = load()
    first, latest = df["period"].min(), df["period"].max()
    months = sorted(df["period"].unique())
    df["mi"] = df["period"].map(lambda p: month_index(p, first))
    # quantity counts only when it is a real unit (litres, pieces...), not kg or missing
    df["q"] = np.where(~df["qtyUnitAbbr"].isin(["N/A", "kg"]) & (df["qty"] > 0), df["qty"], 0)

    hy = {}
    if (REF / "hs6_hy.json").exists():
        hy = json.loads((REF / "hs6_hy.json").read_text())
    desc = df.dropna(subset=["cmdDesc"]).groupby("cmdCode")["cmdDesc"].last()

    if (OUT / "p").exists():
        shutil.rmtree(OUT / "p")
    (OUT / "p").mkdir(parents=True)

    last12 = month_index(latest, first) - 11
    products = []
    for hs, g in df.groupby("cmdCode", sort=True):
        rec = {"hs": hs, "en": short(desc.get(hs, ""), 400), "hy": hy.get(hs), "f": {}}
        v12 = {}
        for fl, gf in g.groupby("flowCode"):
            units = gf.loc[gf["q"] > 0, "qtyUnitAbbr"]
            unit = units.value_counts().index[0] if len(units) else None
            gf = gf.sort_values(["mi", "partnerCode"])
            rows = np.column_stack([gf["mi"].to_numpy(), gf["partnerCode"].to_numpy(),
                                    gf["primaryValue"].round().to_numpy(), gf["netWgt"].round(1).to_numpy(),
                                    np.where(gf["qtyUnitAbbr"] == unit, gf["q"], 0).round(1)])
            num = lambda x: int(x) if float(x).is_integer() else float(x)
            rec["f"][fl] = {"u": unit, "r": [[int(a), int(b), int(c), num(d), num(e)] for a, b, c, d, e in rows]}
            v12[fl] = int(gf.loc[gf["mi"] >= last12, "primaryValue"].sum())
        (OUT / "p" / f"{hs}.json").write_text(json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
        products.append([hs, short(desc.get(hs, "")), hy.get(hs), sorted(rec["f"]), v12])

    meta = {"first": first, "latest": latest, "months": months, "built": date.today().isoformat(),
            "n_products": len(products), "names_hy": bool(hy)}
    (OUT / "products.json").write_text(json.dumps({"meta": meta, "products": products},
                                                  separators=(",", ":"), ensure_ascii=False))
    shutil.copy(REF / "partners.json", OUT / "partners.json")

    # Simple download: average price over the last 12 and 36 months, all partners together
    lab = {12: "Last 12 months", 36: "Last 36 months"}
    rows = []
    for n in (12, 36):
        s = df[df["mi"] >= month_index(latest, first) - (n - 1)]
        a = s.groupby(["cmdCode", "flowCode"]).agg(v=("primaryValue", "sum"), kg=("netWgt", "sum"), q=("q", "sum")).reset_index()
        for r in a.itertuples(index=False):
            rows.append([r.cmdCode, hy.get(r.cmdCode) or "", desc.get(r.cmdCode, ""), FLOWS[r.flowCode], lab[n],
                         round(r.v), round(r.kg), round(r.v / r.kg, 4) if r.kg > 0 else None])
    t = pd.DataFrame(rows, columns=["HS6", "Ապրանք", "Product", "Flow", "Period", "Value (USD)",
                                    "Net weight (kg)", "Average price (USD per kg)"])
    t.to_csv(OUT / "armenia_trade_prices.csv", index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(OUT / "armenia_trade_prices.xlsx", engine="openpyxl") as xw:
        pd.DataFrame({"About": [
            "Average export and import prices of Armenia by 6-digit HS product.",
            "Source: UN Comtrade, monthly data reported by Armenia.",
            "Average price = total value / total net weight over the period, all partner countries together, no filters.",
            f"Data {first[:4]}-{first[4:]} to {latest[:4]}-{latest[4:]}. Built {meta['built']}."]}).to_excel(xw, sheet_name="About", index=False)
        for n in (12, 36):
            t[t["Period"] == lab[n]].to_excel(xw, sheet_name=lab[n], index=False)
    print(f"Built {len(products)} products, {first} to {latest}")


if __name__ == "__main__":
    main()
