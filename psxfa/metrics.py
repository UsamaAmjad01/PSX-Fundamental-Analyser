"""Ratios computed from raw statements, plus a cross-check against scstrade's own."""
from __future__ import annotations

from .config import SANITY_BOUNDS, BANK_SANITY_BOUNDS
from .data import li, scs, num, year_cols


def _div(a, b):
    if a is None or b in (None, 0, 0.0):
        return None
    return a / b


def _pct(x):
    return x * 100 if x is not None else None


def _series(line_map, label, cols):
    """(year, value) pairs for a label, newest first, numeric only."""
    out = []
    for c in cols:
        v = num(line_map.get(label, {}).get(c))
        if v is not None:
            out.append((int(c[1:]), v))
    return out


def _cagr(series):
    if len(series) < 2:
        return None
    (end_y, end_v), (beg_y, beg_v) = series[0], series[-1]
    n = end_y - beg_y
    if n <= 0 or not beg_v or beg_v <= 0 or not end_v or end_v <= 0:
        return None
    return ((end_v / beg_v) ** (1.0 / n) - 1.0) * 100.0


def compute_metrics(fin):
    """Flat dict of ratios for the scoring engine. Missing inputs yield None.

    ROE/ROA use average equity/assets, interest cover is EBIT/finance cost, and
    inventory turnover is COGS/average inventory (these differ from scstrade's
    own definitions, which the cross-check accounts for)."""
    inc, bal, rat = fin["income"], fin["balance"], fin["ratios"]
    y, py, cols = fin["latest"], fin["prev"], fin["years"]
    price = fin["price"]

    def g(line_map, label):
        return li(line_map, label, y)

    sales, cogs, gross = g(inc, "Sales"), g(inc, "Cost Of Sales"), g(inc, "Gross Profit")
    ebit = g(inc, "Operating Profit (EBIT)") or g(inc, "Operating Profit")
    ebitda, dep = g(inc, "EBITDA"), g(inc, "Depreciation")
    fin_cost, pat, eps = g(inc, "Finance Cost"), g(inc, "Profit After Tax"), g(inc, "EPS")
    ca, inv = g(bal, "Current Asset"), g(bal, "Inventory")
    fixed_asset, assets = g(bal, "Fixed Asset"), g(bal, "Total Assets")
    cl = g(bal, "Current Laibility") or g(bal, "Current Liability")
    fixed_liab = g(bal, "Fixed Laibility") or g(bal, "Fixed Liability")
    tot_liab, paid_up, equity = (g(bal, "Total Liabilities"),
                                 g(bal, "Paid Up Capital"), g(bal, "Total Equity"))

    eq_prev = li(bal, "Total Equity", py) if py else None
    as_prev = li(bal, "Total Assets", py) if py else None
    inv_prev = li(bal, "Inventory", py) if py else None
    avg_eq = (equity + eq_prev) / 2 if equity and eq_prev else equity
    avg_as = (assets + as_prev) / 2 if assets and as_prev else assets
    avg_inv = (inv + inv_prev) / 2 if inv and inv_prev else inv

    cfps = scs(rat, "cash flow per share", y)
    dps = scs(rat, "dividend", y)
    bvps = scs(rat, "book value", y)

    pat_series = _series(inc, "Profit After Tax", cols)

    # Cumulative operating cash flow vs net income across all available years.
    # OCF per year is reconstructed as cash-flow-per-share x shares.
    cum_ocf = cum_ni = 0.0
    cfps_map = rat.get("cash flow per share", {})
    have_cash_series = False
    for c in cols:
        pat_y, eps_y, cfps_y = (num(li(inc, "Profit After Tax", c)),
                                num(li(inc, "EPS", c)), num(cfps_map.get(c)))
        if pat_y and eps_y and cfps_y is not None:
            cum_ocf += cfps_y * (pat_y / eps_y)
            cum_ni += pat_y
            have_cash_series = True

    nm_latest = _div(pat, sales)
    nm_old = None
    if len(pat_series) >= 2:
        oy = pat_series[-1][0]
        nm_old = _div(li(inc, "Profit After Tax", f"Q{oy}"), li(inc, "Sales", f"Q{oy}"))
    margin_trend = (_pct(nm_latest) - _pct(nm_old)) if (nm_latest and nm_old) else None

    shares = _div(pat, eps)
    ocf = cfps * shares if (cfps and shares) else None
    capex = None
    if fixed_asset is not None and dep is not None and py:
        fa_prev = li(bal, "Fixed Asset", py)
        if fa_prev is not None:
            capex = (fixed_asset - fa_prev) + dep
    fcf = (ocf - capex) if (ocf is not None and capex is not None) else None

    m = {
        "profile": "bank" if fin["is_bank"] else "standard",
        "year": int(y[1:]) if y else None, "price": price, "eps": eps,
        "GrossMargin_%": _pct(_div(gross, sales)),
        "OperatingMargin_%": _pct(_div(ebit, sales)),
        "NetMargin_%": _pct(_div(pat, sales)),
        "EBITDAMargin_%": _pct(_div(ebitda, sales)),
        "ROE_%": _pct(_div(pat, avg_eq)),
        "ROA_%": _pct(_div(pat, avg_as)),
        "ROCE_%": _pct(_div(ebit, (assets - cl) if assets and cl else None)),
        "CurrentRatio": _div(ca, cl),
        "QuickRatio": _div((ca - inv) if ca and inv is not None else None, cl),
        "DebtToEquity": _div(tot_liab, equity),
        "LT_DebtToEquity": _div(fixed_liab, equity),
        "DebtToEBITDA": _div(tot_liab, ebitda),
        "InterestCover": _div(ebit, fin_cost),
        "FinancialLeverage": _div(assets, equity),
        "AssetTurnover": _div(sales, assets),
        "InventoryTurnover": _div(cogs, avg_inv),
        "OCF": ocf,
        "OCF_to_NI": _div(cfps, eps),
        "cumOCF_to_NI": _div(cum_ocf, cum_ni) if have_cash_series and cum_ni else None,
        "FCF_approx": fcf,
        "FCF_margin_%": _pct(_div(fcf, sales)) if fcf is not None else None,
        "Capex_approx": capex,
        "PE": _div(price, eps),
        "EarningsYield_%": _pct(_div(eps, price)),
        "DividendYield_%": _pct(_div(dps, price)),
        "PB": _div(price, bvps),
        "PayoutRatio_%": _pct(_div(dps, eps)),
        "DividendCover": _div(eps, dps),
        "RevenueCAGR_%": _cagr(_series(inc, "Sales", cols)),
        "EPS_CAGR_%": _cagr(_series(inc, "EPS", cols)),
        "BVPS_CAGR_%": _cagr(_series(rat, "book value", cols)),
        "MarginTrend_pp": margin_trend,
        "equity": equity,
        "ocf_positive": ocf is not None and ocf > 0,
        "years_count": len(pat_series),
    }

    if fin["is_bank"]:
        advances, deposits = g(bal, "Advances"), g(bal, "Deposits")
        investments = g(bal, "Investments")
        m["NetInterestMargin_%"] = scs(rat, "net interest margin", y)
        m["CapitalAdequacy_proxy_%"] = _pct(_div(equity, assets))
        m["ADR_%"] = _pct(_div(advances, deposits))
        m["InvestmentToDeposit_%"] = _pct(_div(investments, deposits))
        m["AssetGrowth_%"] = scs(rat, "assets growth", y)
        m["EarningGrowth_%"] = scs(rat, "earning growth", y)
        for k, (lo, hi) in BANK_SANITY_BOUNDS.items():
            if m.get(k) is not None and not (lo <= m[k] <= hi):
                m[k] = None

    m["PEG"] = (_div(m["PE"], m["EPS_CAGR_%"])
                if (m["PE"] and m["EPS_CAGR_%"] and m["EPS_CAGR_%"] > 0) else None)

    re_proxy = (equity - paid_up) if equity and paid_up else None
    mktcap = price * shares if price and shares else None
    if assets and all(v is not None for v in
                      (ca, cl, re_proxy, ebit, mktcap, tot_liab, sales)):
        m["AltmanZ"] = (1.2 * (ca - cl) / assets + 1.4 * re_proxy / assets
                        + 3.3 * ebit / assets + 0.6 * mktcap / tot_liab
                        + 1.0 * sales / assets)
    else:
        m["AltmanZ"] = None

    for k, (lo, hi) in SANITY_BOUNDS.items():
        if m.get(k) is not None and not (lo <= m[k] <= hi):
            m[k] = None
    return m


def cross_check(fin):
    """Compute the ratios independently and pair each with scstrade's own value.

    Returns rows of (section, metric, computed, scstrade, note, scale). `scale`
    puts the computed value on scstrade's units for comparison (e.g. D/E is a
    ratio for us but a percent for them)."""
    inc, bal, rat = fin["income"], fin["balance"], fin["ratios"]
    y = fin["latest"]
    price = fin["price"]

    sales = li(inc, "Sales", y)
    cogs = li(inc, "Cost Of Sales", y)
    gross = li(inc, "Gross Profit", y)
    ebit = li(inc, "Operating Profit (EBIT)", y) or li(inc, "Operating Profit", y)
    ebitda = li(inc, "EBITDA", y)
    fin_cost = li(inc, "Finance Cost", y)
    pat = li(inc, "Profit After Tax", y)
    eps = li(inc, "EPS", y)
    ca = li(bal, "Current Asset", y)
    inv = li(bal, "Inventory", y)
    cash = li(bal, "Cash", y)
    fixed_asset = li(bal, "Fixed Asset", y)
    assets = li(bal, "Total Assets", y)
    cl = li(bal, "Current Laibility", y) or li(bal, "Current Liability", y)
    fixed_liab = li(bal, "Fixed Laibility", y) or li(bal, "Fixed Liability", y)
    tot_liab = li(bal, "Total Liabilities", y)
    equity = li(bal, "Total Equity", y)
    cfps = scs(rat, "cash flow per share", y)
    dps = scs(rat, "dividend", y)
    book = scs(rat, "book value", y)

    rows = []

    def add(section, metric, computed, scs_name=None, note="", scale=1.0):
        rows.append([section, metric, computed,
                     scs(rat, scs_name, y) if scs_name else None, note, scale])

    add("Profitability", "GrossMargin_%", _pct(_div(gross, sales)), "gross profit margin")
    add("Profitability", "OperatingMargin_%", _pct(_div(ebit, sales)), "operating profit margin")
    add("Profitability", "NetMargin_%", _pct(_div(pat, sales)), "net profit margin")
    add("Profitability", "EBITDAMargin_%", _pct(_div(ebitda, sales)), "ebitda margin")
    add("Profitability", "ROE_%", _pct(_div(pat, equity)), "return on equity", "ending equity")
    add("Profitability", "ROA_%", _pct(_div(pat, assets)), "return on assets", "ending assets")
    add("Profitability", "ROCE_%",
        _pct(_div(ebit, (assets - cl) if assets and cl else None)),
        "return on capital employed")

    add("Liquidity", "CurrentRatio", _div(ca, cl), "current ratio")
    add("Liquidity", "QuickRatio",
        _div((ca - inv) if ca and inv is not None else None, cl), "quick ratio")
    add("Liquidity", "CashRatio", _div(cash, cl))

    add("Solvency", "DebtToEquity", _div(tot_liab, equity),
        "total debt to equity", "total liabilities / equity", 100)
    add("Solvency", "LT_DebtToEquity", _div(fixed_liab, equity),
        "long term debt to equity", "fixed liabilities / equity", 100)
    add("Solvency", "DebtToEBITDA", _div(tot_liab, ebitda))
    add("Solvency", "InterestCover", _div(ebit, fin_cost),
        "interest cover", "scstrade uses (PBT+finance)/finance")
    add("Solvency", "FinancialLeverage", _div(assets, equity))

    add("Efficiency", "AssetTurnover", _div(sales, assets), "asset turnover", "", 100)
    add("Efficiency", "FixedAssetTurnover", _div(sales, fixed_asset))
    add("Efficiency", "InventoryTurnover", _div(cogs, inv),
        "inventory turnover", "scstrade uses sales/inventory")

    add("Cash", "OCF_to_NetIncome", _div(cfps, eps), note="cash-flow-per-share / EPS")

    add("Valuation", "PE", _div(price, eps), note="current price / EPS")
    add("Valuation", "EarningsYield_%", _pct(_div(eps, price)))
    add("Valuation", "DividendYield_%", _pct(_div(dps, price)))
    add("Valuation", "PB", _div(price, book))

    add("Dividends", "PayoutRatio_%", _pct(_div(dps, eps)), "payout ratio")
    add("Dividends", "DividendCover", _div(eps, dps), "dividend cover")
    return rows
