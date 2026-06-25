#!/usr/bin/env python3
"""
psx_verdict.py — PSX fundamental BUY / HOLD / AVOID screening tool.

    python psx_verdict.py LUCK MEBL HUBC SYS

WHAT IT DOES
------------
For each ticker it pulls the company's *raw* annual financial statements and
PSX's pre-computed ratios, COMPUTES the fundamental ratios itself from exact
line items, cross-checks them against the source's own ratios, and emits a
gated 3-pillar verdict (Quality / Safety / Valuation) plus a fully auditable
per-metric breakdown to console, CSV and XLSX.

DATA PROVENANCE  (verified in Stage 1/2 inspection — do not swap blindly)
------------------------------------------------------------------------
* Current price          -> psxdata.quote(sym)['price]            (PSX screener)
* Income statement (5y)  -> scstrade SS_CompanySnapShotYF.aspx/chart1   JSON grid
* Balance sheet    (5y)  -> scstrade SS_CompanySnapShotYF.aspx/chart3   JSON grid
* Pre-computed ratios(5y)-> scstrade SS_CompanySnapShotYR.aspx/chart    JSON grid
  (session flow: GET the page to seed ASP.NET_SessionId, then POST chart)

Every number is read from an EXPLICITLY LABELED row and an EXPLICITLY NAMED
period column. No value is ever grabbed "by proximity to a label" (that was the
previous tool's fatal bug — it returned debt/equity = 2021, a year). Magnitude
sanity bounds reject impossible values (e.g. a ratio of 2021) as missing.

Statement units: income & balance values are PKR thousands; EPS and price are
rupees-per-share. Ratios computed from same-unit line items, so units cancel.
"""
import sys
import json
import time
import argparse
import os
import requests
import pandas as pd

try:
    import psxdata
except Exception:                                    # pragma: no cover
    psxdata = None


# ===========================================================================
# SCORING CONFIG  —  TUNE EVERYTHING HERE
# Each metric: (weight, threshold, direction)   direction: "high" or "low" better
# Two sector profiles. Add more profiles + detection later (e.g. insurance).
# ===========================================================================
STANDARD_PROFILE = {
    "quality": {
        "ROE_%":             (3, 15.0, "high"),
        "NetMargin_%":       (2,  8.0, "high"),
        "OperatingMargin_%": (2, 10.0, "high"),
        "NP_Growth_%":       (2,  0.0, "high"),
        "OP_Growth_%":       (1,  0.0, "high"),
    },
    "safety": {
        "CurrentRatio":      (3,  1.5, "high"),
        "DebtToEquity":      (3,  1.0, "low"),   # = Total Liabilities / Equity
        "InterestCover":     (2,  3.0, "high"),
    },
    "valuation": {
        "PE":                (3, 20.0, "low"),
        "DividendYield_%":   (1,  3.0, "high"),
        "PayoutRatio_%":     (1, 70.0, "low"),
    },
}

# Banks are deposit-funded: no Sales / Operating Profit / Current Liability, and
# 10-18x liabilities-to-equity is normal, not dangerous. Different metric set.
BANK_PROFILE = {
    "quality": {
        "ROE_%":               (3, 15.0, "high"),
        "NetInterestMargin_%": (2,  4.0, "high"),
        "ROA_%":               (2,  1.5, "high"),
        "NP_Growth_%":         (2,  0.0, "high"),
    },
    "safety": {
        "CapitalAdequacy_%":   (3,  5.0, "high"),  # Equity / Total Assets
        "DebtToEquity":        (1, 20.0, "low"),   # bank-lenient
    },
    "valuation": {
        "PE":                  (3, 12.0, "low"),
        "DividendYield_%":     (1,  4.0, "high"),
        "PayoutRatio_%":       (1, 70.0, "low"),
    },
}

PILLAR_WEIGHTS = {"quality": 0.45, "safety": 0.35, "valuation": 0.20}
GATE_SAFETY = 0.40           # below this -> AVOID (weak balance sheet)
GATE_QUALITY = 0.40          # below this -> AVOID (weak business quality)
GATE_VALUATION_EXPENSIVE = 0.30   # BUY but flagged expensive below this
DECIDE = {"strong_buy": 0.75, "buy": 0.60, "hold": 0.45}
MIN_COVERAGE = 0.34          # need >= ~1 of 3 pillars to give any verdict
XCHECK_TOL = 0.05            # computed-vs-source mismatch flag threshold (5%)

# Magnitude sanity bounds (lo, hi). Values outside -> treated as MISSING.
# These also catch year-contamination (e.g. a "ratio" of 2021 > 1000 is rejected).
SANITY = {
    "ROE_%":               (-1000, 1000),
    "ROA_%":               (-1000, 1000),
    "NetMargin_%":         (-1000, 1000),
    "OperatingMargin_%":   (-1000, 1000),
    "NetInterestMargin_%": (-1000, 1000),
    "NP_Growth_%":         (-100000, 100000),
    "OP_Growth_%":         (-100000, 100000),
    "CurrentRatio":        (0, 1000),
    "DebtToEquity":        (0, 1000),
    "InterestCover":       (-1000, 100000),
    "CapitalAdequacy_%":   (0, 100),
    "PE":                  (0, 1000),       # negative/zero EPS handled separately
    "DividendYield_%":     (0, 100),
    "PayoutRatio_%":       (0, 1000),
}


# ===========================================================================
# scstrade JSON-grid client
# ===========================================================================
_SCS = "https://scstrade.com/stockscreening/"
_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
}
_YR_TABLE_NAMES = {
    "table1": "Solvency", "table2": "Dividends", "table3": "Equity",
    "table4": "Earnings", "table5": "Sales", "table6": "Liquidity",
    "table7": "EnterpriseValue", "table8": "Profitability",
    "table9": "Cash", "table10": "Price",
}


def _retry(fn, *args, tries=4, base_delay=1.5, **kwargs):
    """Call fn with simple exponential backoff (handles DNS/connection blips)."""
    last = None
    for i in range(tries):
        try:
            return fn(*args, **kwargs)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last = e
            if i < tries - 1:
                time.sleep(base_delay * (i + 1))
    raise last


def _post_grid(page, endpoint, sym):
    """POST a {"sym":...} JSON-grid endpoint, return list[dict] (the 'd' array)."""
    url = f"{_SCS}{page}.aspx/{endpoint}"
    hdrs = {**_HDRS, "Content-Type": "application/json; charset=utf-8",
            "Referer": f"{_SCS}{page}.aspx?symbol={sym}",
            "X-Requested-With": "XMLHttpRequest"}
    r = _retry(requests.post, url, data=json.dumps({"sym": sym}),
               headers=hdrs, timeout=30)
    r.raise_for_status()
    return r.json().get("d") or []


def fetch_income(sym):
    """Annual income statement, 5 yrs. {label: {year:str_value}}."""
    return _rows_to_map(_post_grid("SS_CompanySnapShotYF", "chart1", sym))


def fetch_balance(sym):
    """Annual balance sheet, 5 yrs. {label: {year:str_value}}."""
    return _rows_to_map(_post_grid("SS_CompanySnapShotYF", "chart3", sym))


def fetch_ratios(sym):
    """scstrade pre-computed yearly ratios. Needs the GET->POST session flow.

    Returns {normalized_ratio_label: {year:value}} flattened across all tables.
    """
    s = requests.Session()
    s.headers.update(_HDRS)
    url = f"{_SCS}SS_CompanySnapShotYR.aspx?symbol={sym}"
    _retry(s.get, url, timeout=30)                    # seeds ASP.NET_SessionId
    r = _retry(s.post, f"{_SCS}SS_CompanySnapShotYR.aspx/chart",
               data=json.dumps({}),
               headers={"Content-Type": "application/json; charset=utf-8",
                        "Referer": url}, timeout=30)
    r.raise_for_status()
    payload = r.json().get("d")
    if not payload:
        return {}
    tables = json.loads(payload) if isinstance(payload, str) else payload
    flat = {}
    for tk, rows in tables.items():
        for row in rows:
            label = row.get("QYear", "")
            name = label.split(" - ")[0].strip()      # drop " - Solvency" suffix
            years = {k: v for k, v in row.items() if k != "QYear"}
            flat[name.lower()] = years
    return flat


def fetch_price(sym):
    """Current price from psxdata screener quote, or None."""
    if psxdata is None:
        return None
    try:
        q = psxdata.quote(sym)
        if q is None or getattr(q, "empty", True):
            return None
        return num(q.iloc[0].get("price"))
    except Exception:
        return None


def _rows_to_map(rows):
    out = {}
    for row in rows:
        label = row.get("QYear")
        if label is None:
            continue
        out[label] = {k: v for k, v in row.items() if k != "QYear"}
    return out


# ===========================================================================
# parsing / lookup helpers
# ===========================================================================
def num(s):
    """Coerce a scstrade cell to float. Handles commas, %, ' bn'/' m', blanks."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace(",", "").replace("%", "")
    if t in ("", "-", "N/A", "NA", "nan", "None"):
        return None
    mult = 1.0
    low = t.lower()
    for suf, m in ((" bn", 1e9), (" b", 1e9), (" mn", 1e6), (" m", 1e6)):
        if low.endswith(suf):
            t = t[: -len(suf)].strip()
            mult = m
            break
    try:
        return float(t) * mult
    except ValueError:
        return None


def year_columns(line_map):
    """Year column keys present (e.g. 'Q2025'), sorted newest-first."""
    cols = set()
    for years in line_map.values():
        cols.update(years.keys())
    yc = [c for c in cols if c[1:].isdigit()]
    return sorted(yc, key=lambda c: int(c[1:]), reverse=True)


def latest_prev_years(income):
    """Pick (latest, previous) year columns: latest = newest col with non-zero
    Profit After Tax; previous = the next older column."""
    cols = year_columns(income)
    pat = income.get("Profit After Tax", {})
    latest = None
    for c in cols:
        if num(pat.get(c)) not in (None, 0.0):
            latest = c
            break
    if latest is None and cols:
        latest = cols[0]
    if latest is None:
        return None, None
    idx = cols.index(latest)
    prev = cols[idx + 1] if idx + 1 < len(cols) else None
    return latest, prev


def li(line_map, label, year):
    """Line item value for an exact label + year column."""
    return num(line_map.get(label, {}).get(year))


def scs_ratio(ratios, name, year):
    """scstrade pre-computed ratio by (case-insensitive exact) name + year."""
    rec = ratios.get(name.lower())
    return num(rec.get(year)) if rec else None


# ===========================================================================
# metric computation
# ===========================================================================
def _safe_div(a, b):
    if a is None or b in (None, 0, 0.0):
        return None
    return a / b


def _growth(curr, prev):
    if curr is None or prev in (None, 0, 0.0):
        return None
    return (curr - prev) / abs(prev) * 100.0


def is_bank(income):
    """Bank/DFI schema: has Net Interest Income, lacks Sales/Operating Profit."""
    labels = set(income.keys())
    return ("Net Interest Income" in labels) and ("Sales" not in labels)


def compute_metrics(fin):
    """Return (profile_name, profile, metrics, xcheck) for one company.

    metrics: {metric_name: value or None}
    xcheck:  {metric_name: (scstrade_value or None, flag_str)}  audit trail
    """
    inc, bal, rat = fin["income"], fin["balance"], fin["ratios"]
    yr, py = fin["latest_year"], fin["prev_year"]
    price = fin["price"]
    bank = fin["is_bank"]

    eps = li(inc, "EPS", yr)
    equity = li(bal, "Total Equity", yr)
    assets = li(bal, "Total Assets", yr)
    tot_liab = li(bal, "Total Liabilities", yr)
    pat = li(inc, "Profit After Tax", yr)
    pat_prev = li(inc, "Profit After Tax", py) if py else None
    dps = scs_ratio(rat, "Dividend", yr)              # rupees/share (reconciled)

    metrics, xcheck = {}, {}

    def record(name, computed, scs_name=None, definition_differs=False,
               scs_scale=1.0):
        """Store a metric, apply sanity bounds, cross-check vs scstrade.

        scs_scale: factor to put our value on scstrade's scale for the
        comparison only (e.g. we keep D/E as a ratio 0.88 but scstrade reports
        it as a percent 87.96, so scs_scale=100). Does not affect the stored
        metric or its threshold.
        """
        val = computed
        if val is not None and name in SANITY:
            lo, hi = SANITY[name]
            if not (lo <= val <= hi):
                val = None                            # impossible magnitude -> drop
        scs = scs_ratio(rat, scs_name, yr) if scs_name else None
        flag = ""
        if val is not None and scs is not None and scs != 0:
            diff = abs(val * scs_scale - scs) / abs(scs)
            if definition_differs:
                flag = f"[scs {scs:g} (alt-def)]"
            elif diff <= XCHECK_TOL:
                flag = f"[scs {scs:g} ok]"
            else:
                flag = f"[scs {scs:g} diff{diff*100:.0f}%!]"
        elif val is None and computed is not None:
            flag = "[rejected: out-of-range]"
        metrics[name] = val
        xcheck[name] = (scs, flag)

    if bank:
        record("ROE_%", _safe_div(pat, equity) and _safe_div(pat, equity) * 100,
               "Return On Equity")
        # NIM not derivable from given lines -> sourced from scstrade directly.
        nim = scs_ratio(rat, "Net Interest Margin", yr)
        metrics["NetInterestMargin_%"] = nim if (nim is None or 0 <= nim <= 100) else None
        xcheck["NetInterestMargin_%"] = (nim, "[source: scstrade]")
        record("ROA_%", _safe_div(pat, assets) and _safe_div(pat, assets) * 100,
               "Return On Assets")
        record("NP_Growth_%", _growth(pat, pat_prev), "Earning Growth")
        record("CapitalAdequacy_%",
               _safe_div(equity, assets) and _safe_div(equity, assets) * 100,
               "Equity To Assets Ratio")
        record("DebtToEquity", _safe_div(tot_liab, equity),
               "Total Debt To Equity", scs_scale=100)
    else:
        sales = li(inc, "Sales", yr)
        op = li(inc, "Operating Profit (EBIT)", yr) or li(inc, "Operating Profit", yr)
        op_prev = (li(inc, "Operating Profit (EBIT)", py)
                   or li(inc, "Operating Profit", py)) if py else None
        finance = li(inc, "Finance Cost", yr)
        ca = li(bal, "Current Asset", yr)
        cl = li(bal, "Current Liability", yr)

        record("ROE_%", _safe_div(pat, equity) and _safe_div(pat, equity) * 100,
               "Return On Equity")
        record("NetMargin_%", _safe_div(pat, sales) and _safe_div(pat, sales) * 100,
               "Net Profit Margin")
        record("OperatingMargin_%", _safe_div(op, sales) and _safe_div(op, sales) * 100,
               "Operating Profit Margin")
        record("NP_Growth_%", _growth(pat, pat_prev), "Earning Growth")
        record("OP_Growth_%", _growth(op, op_prev))
        record("CurrentRatio", _safe_div(ca, cl), "Current Ratio")
        record("DebtToEquity", _safe_div(tot_liab, equity),
               "Total Debt To Equity", scs_scale=100)
        # scstrade's Interest Cover uses (PBT+FinanceCost)/FinanceCost; ours is the
        # brief's OperatingProfit/FinanceCost -> definitional diff, not an error.
        record("InterestCover", _safe_div(op, finance), "Interest Cover",
               definition_differs=True)

    # Valuation (both profiles)
    if eps is not None and eps > 0:
        record("PE", _safe_div(price, eps))
    else:
        metrics["PE"] = None
        xcheck["PE"] = (None, "[EPS<=0: n/a]" if eps is not None else "[no EPS]")
    record("DividendYield_%", _safe_div(dps, price) and _safe_div(dps, price) * 100)
    payout = scs_ratio(rat, "Payout Ratio", yr)
    if payout is None and dps is not None and eps not in (None, 0):
        payout = dps / eps * 100
    record("PayoutRatio_%", payout)

    profile = BANK_PROFILE if bank else STANDARD_PROFILE
    return ("bank" if bank else "standard"), profile, metrics, xcheck


# ===========================================================================
# verdict engine  (gated, sequential — NOT a flat point total)
# ===========================================================================
def score_pillar(metrics, spec):
    earned = 0.0
    available = 0.0
    detail = []
    for name, (weight, thr, direction) in spec.items():
        val = metrics.get(name)
        if val is None:
            detail.append(f"{name}: n/a")
            continue
        available += weight
        passed = (val >= thr) if direction == "high" else (val <= thr)
        if passed:
            earned += weight
        detail.append(f"{name}={val:.2f} {'PASS' if passed else 'FAIL'}(thr {thr:g})")
    ratio = (earned / available) if available else None
    return ratio, detail


def decide(metrics, profile):
    qR, qD = score_pillar(metrics, profile["quality"])
    sR, sD = score_pillar(metrics, profile["safety"])
    vR, vD = score_pillar(metrics, profile["valuation"])

    have = [r for r in (qR, sR, vR) if r is not None]
    coverage = len(have) / 3.0

    parts = wsum = 0.0
    for r, w in ((qR, PILLAR_WEIGHTS["quality"]),
                 (sR, PILLAR_WEIGHTS["safety"]),
                 (vR, PILLAR_WEIGHTS["valuation"])):
        if r is not None:
            parts += w * r
            wsum += w
    composite = (parts / wsum) if wsum else None

    s_fail = sR is not None and sR < GATE_SAFETY
    q_fail = qR is not None and qR < GATE_QUALITY
    v_weak = vR is not None and vR < GATE_VALUATION_EXPENSIVE

    if composite is None or coverage < MIN_COVERAGE:
        verdict = "INSUFFICIENT DATA"
    elif s_fail:
        verdict = "AVOID (weak balance sheet)"
    elif q_fail:
        verdict = "AVOID (weak business quality)"
    elif composite >= DECIDE["strong_buy"] and not v_weak:
        verdict = "STRONG BUY"
    elif composite >= DECIDE["buy"]:
        verdict = "BUY"
    elif composite >= DECIDE["hold"]:
        verdict = "HOLD / WATCH"
    else:
        verdict = "AVOID"
    if verdict in ("BUY", "STRONG BUY") and v_weak:
        verdict = "BUY (but looks expensive)"

    conf = "High" if coverage > 0.99 else "Medium" if coverage > 0.5 else "Low"
    return {
        "verdict": verdict,
        "composite": composite,
        "quality": qR, "safety": sR, "valuation": vR,
        "coverage": f"{len(have)}/3 pillars",
        "confidence": conf,
        "detail": {"quality": qD, "safety": sD, "valuation": vD},
    }


# ===========================================================================
# per-symbol orchestration
# ===========================================================================
def analyze(sym):
    sym = sym.strip().upper()
    income = fetch_income(sym)
    if not income:
        raise ValueError("no income statement returned (unknown ticker or no filings)")
    balance = fetch_balance(sym)
    ratios = fetch_ratios(sym)
    price = fetch_price(sym)
    latest, prev = latest_prev_years(income)
    fin = {"income": income, "balance": balance, "ratios": ratios,
           "price": price, "latest_year": latest, "prev_year": prev,
           "is_bank": is_bank(income)}
    profile_name, profile, metrics, xcheck = compute_metrics(fin)
    result = decide(metrics, profile)

    # weave scstrade cross-check flags into the per-pillar detail strings
    def annotate(detail_list):
        out = []
        for d in detail_list:
            name = d.split("=")[0].split(":")[0].strip()
            flag = xcheck.get(name, (None, ""))[1]
            out.append(f"{d} {flag}".strip())
        return out

    row = {
        "Symbol": sym,
        "Profile": profile_name,
        "FY": latest[1:] if latest else None,
        "Verdict": result["verdict"],
        "Composite_%": _pct(result["composite"]),
        "Quality_%": _pct(result["quality"]),
        "Safety_%": _pct(result["safety"]),
        "Valuation_%": _pct(result["valuation"]),
        "Confidence": result["confidence"],
        "Coverage": result["coverage"],
        "Price": price,
    }
    for k, v in metrics.items():
        row[k] = round(v, 2) if isinstance(v, float) else v
    row["_quality_detail"] = "; ".join(annotate(result["detail"]["quality"]))
    row["_safety_detail"] = "; ".join(annotate(result["detail"]["safety"]))
    row["_valuation_detail"] = "; ".join(annotate(result["detail"]["valuation"]))
    return row


def _pct(x):
    return round(x * 100, 1) if x is not None else None


# ===========================================================================
# main
# ===========================================================================
FRONT_COLS = ["Symbol", "Profile", "FY", "Verdict", "Composite_%", "Quality_%",
              "Safety_%", "Valuation_%", "Confidence", "Coverage", "Price",
              "ROE_%", "ROA_%", "NetMargin_%", "OperatingMargin_%",
              "NetInterestMargin_%", "NP_Growth_%", "OP_Growth_%",
              "CurrentRatio", "DebtToEquity", "InterestCover", "CapitalAdequacy_%",
              "PE", "DividendYield_%", "PayoutRatio_%",
              "_quality_detail", "_safety_detail", "_valuation_detail"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="PSX fundamental BUY/HOLD/AVOID screener")
    ap.add_argument("symbols", nargs="+", help="PSX tickers, e.g. LUCK MEBL HUBC SYS")
    ap.add_argument("--out", default="verdict_output", help="output basename")
    args = ap.parse_args(argv)

    rows = []
    for sym in args.symbols:
        sym = sym.strip().upper()
        print(f"Analyzing {sym} ...", flush=True)
        try:
            row = analyze(sym)
            rows.append(row)
            print(f"  -> {row['Verdict']:28s} composite={row['Composite_%']}  "
                  f"Q={row['Quality_%']} S={row['Safety_%']} V={row['Valuation_%']}  "
                  f"conf={row['Confidence']} ({row['Coverage']}) "
                  f"[{row['Profile']} profile, FY{row['FY']}]")
        except Exception as e:
            print(f"  [ERROR] {sym}: {e}")
            rows.append({"Symbol": sym, "Verdict": "ERROR", "Coverage": str(e)})

    df = pd.DataFrame(rows)
    cols = [c for c in FRONT_COLS if c in df.columns] + \
           [c for c in df.columns if c not in FRONT_COLS]
    df = df[cols]
    df.to_csv(f"{args.out}.csv", index=False, encoding="utf-8-sig")
    try:
        df.to_excel(f"{args.out}.xlsx", index=False)
    except Exception as e:
        print(f"  [warn] could not write xlsx: {e}")
    print(f"\nSaved {len(df)} rows -> {os.path.abspath(args.out + '.csv')}"
          f" and .xlsx")
    print("\nNOTE: fundamental screening only - NOT investment advice. Verdicts are "
          "a mechanical\n      reading of past filings; verify the _detail columns "
          "before acting on anything.")
    return df


if __name__ == "__main__":
    main()
