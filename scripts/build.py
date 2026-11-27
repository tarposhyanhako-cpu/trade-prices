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


def short(s, n=110):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def sums(s, keys):
    g = s.groupby(keys, sort=False)
    out = g[["primaryValue", "vw", "kgw", "vq", "qq"]].sum()
    out["m"] = g["period"].nunique()
    out = out.reset_index()
    out["pkg"] = np.where(out["kgw"] > 0, out["vw"] / out["kgw"].where(out["kgw"] > 0), np.nan)
    out["pq"] = np.where(out["qq"] > 0, out["vq"] / out["qq"].where(out["qq"] > 0), np.nan)
    return out


def rec_of(r):
    f = lambda x: None if pd.isna(x) else round(float(x), 4)
    return {"v": round(float(r.primaryValue), 0), "kg": round(float(r.kgw), 0), "pkg": f(r.pkg),
            "q": round(float(r.qq), 0), "pq": f(r.pq), "m": int(r.m)}


def main():
    df = flag_outliers(load())
    df["vw"] = df["primaryValue"].where(df["okw"], 0.0)
    df["kgw"] = df["netWgt"].where(df["okw"], 0.0)
    df["vq"] = df["primaryValue"].where(df["okq"], 0.0)
    df["qq"] = df["qty"].where(df["okq"], 0.0)
    latest, first = df["period"].max(), df["period"].min()
    W = windows(latest, first)
    lab = {k: v[2] for k, v in W.items()}

    desc = df.dropna(subset=["cmdDesc"]).groupby("cmdCode")["cmdDesc"].last()
    pname = df.dropna(subset=["partnerDesc"]).groupby("partnerCode")["partnerDesc"].last()
    # Unit of each product: the most common unit in the last 36 months (older years if none)
    mode = lambda x: x.value_counts().index[0]
    recent = df[df["okq"] & (df["period"] >= W["36m"][0])]
    uq = df[df["okq"]].groupby(["cmdCode", "flowCode"])["qtyUnitAbbr"].agg(mode)
    uq.update(recent.groupby(["cmdCode", "flowCode"])["qtyUnitAbbr"].agg(mode))

    R = {}
    def node(hs, fl):
        r = R.setdefault(hs, {"hs": hs, "d": short(desc.get(hs, ""), 300), "f": {}})
        return r["f"].setdefault(fl, {"unit": uq.get((hs, fl)), "w": {}, "p": {}})

    long_rows = []
    for wk, (lo, hi, _) in W.items():
        s = df[(df["period"] >= lo) & (df["period"] <= hi)]
        if s.empty:
            continue
        for r in sums(s, ["cmdCode", "flowCode"]).itertuples(index=False):
            a = rec_of(r)
            node(r.cmdCode, r.flowCode)["w"][wk] = a
            long_rows.append([r.cmdCode, desc.get(r.cmdCode, ""), FLOWS[r.flowCode], "All partners", wk, a, uq.get((r.cmdCode, r.flowCode))])
        par = sums(s, ["cmdCode", "flowCode", "partnerCode"]).sort_values(
            ["cmdCode", "flowCode", "primaryValue"], ascending=[True, True, False])
        par["rank"] = par.groupby(["cmdCode", "flowCode"]).cumcount()
        for r in par.itertuples(index=False):
            a = rec_of(r); a["n"] = pname.get(r.partnerCode, str(r.partnerCode))
            if r.rank < TOP_PARTNERS:
                node(r.cmdCode, r.flowCode)["p"].setdefault(wk, []).append(a)
            if wk in ("12m", "36m"):
                long_rows.append([r.cmdCode, desc.get(r.cmdCode, ""), FLOWS[r.flowCode], a["n"], wk, a, uq.get((r.cmdCode, r.flowCode))])

    (OUT / "p").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "p").glob("*.json"):
        old.unlink()
    products = []
    for hs in sorted(R):
        rec = R[hs]
        (OUT / "p" / f"{hs}.json").write_text(json.dumps(rec, separators=(",", ":"), ensure_ascii=False))
        products.append([hs, short(desc.get(hs, "")), sorted(rec["f"].keys()),
                         {fl: rec["f"][fl]["w"].get("12m", {}).get("v", 0) for fl in rec["f"]}])

    meta = {"latest": latest, "first": first, "built": date.today().isoformat(),
            "windows": [[k, v[2], v[0], v[1]] for k, v in W.items()],
            "n_products": len(products), "outlier_factor": OUTLIER_FACTOR}
    (OUT / "products.json").write_text(json.dumps({"meta": meta, "products": products},
                                                  separators=(",", ":"), ensure_ascii=False))

    cols = ["HS6", "Product", "Flow", "Partner", "Period", "Value (USD)", "Net weight (kg)",
            "Average price (USD per kg)", "Quantity", "Unit", "Average price (USD per unit)", "Months with trade"]
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
