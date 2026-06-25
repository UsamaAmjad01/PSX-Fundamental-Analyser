"""Tunable thresholds for the scoring engine. Recalibrate here, not in the logic."""

# Non-bank pillars. Each metric: (weight, threshold, "high" or "low" is better).
# A pillar's score (0-5) is the weighted fraction of available metrics that pass.
PILLARS = {
    "Profitability": {
        "ROE_%":             (3, 15.0, "high"),
        "ROCE_%":            (2, 12.0, "high"),
        "NetMargin_%":       (1,  8.0, "high"),
        "OperatingMargin_%": (1, 10.0, "high"),
        "EBITDAMargin_%":    (1, 15.0, "high"),
    },
    "Safety": {
        "InterestCover":     (3,  3.0, "high"),
        "DebtToEquity":      (3,  1.0, "low"),
        "DebtToEBITDA":      (2,  3.0, "low"),
    },
    "Cash": {
        "cumOCF_to_NI":      (3,  1.0, "high"),
        "OCF_to_NI":         (2,  1.0, "high"),
        "FCF_margin_%":      (1,  0.0, "high"),
    },
    "Growth": {
        "RevenueCAGR_%":     (2,  0.0, "high"),
        "EPS_CAGR_%":        (2,  0.0, "high"),
        "BVPS_CAGR_%":       (1,  0.0, "high"),
        "MarginTrend_pp":    (1,  0.0, "high"),
    },
    "Valuation": {
        "PE":                  (2, 20.0, "low"),
        "EarningsYield_vs_rf": (2,  0.0, "high"),
        "PB":                  (1,  3.0, "low"),
        "DividendYield_%":     (1,  0.0, "high"),
    },
}
PILLAR_WEIGHTS = {"Profitability": 0.25, "Safety": 0.25, "Cash": 0.20,
                  "Growth": 0.15, "Valuation": 0.15}

GATES = {
    "require_positive_equity": True,
    "min_interest_cover": 1.5,    # hard safety gate; below this fails outright
    "max_debt_to_equity": 1.0,    # soft: scoring threshold + review flag only
    "min_cum_ocf_to_ni": 0.8,     # cash gate over the 5-year window
}

TIERS = {"STRONG BUY": 4.0, "BUY": 3.0, "HOLD / WATCH": 2.0}
VALUATION_EXPENSIVE = 2.0
DEFAULT_RISKFREE = 11.0

# Banks are deposit-funded; standard leverage/margin ratios don't apply. The
# capital gate uses Equity/Total-Assets because risk-weighted CAR isn't in the
# free data and is left to the human checklist.
BANK_PILLARS = {
    "Profitability": {
        "ROE_%":               (3, 15.0, "high"),
        "ROA_%":               (2,  1.5, "high"),
        "NetInterestMargin_%": (2,  4.0, "high"),
    },
    "Capital": {
        "CapitalAdequacy_proxy_%": (3, 5.0, "high"),
    },
    "Growth": {
        "EarningGrowth_%":     (2,  0.0, "high"),
        "AssetGrowth_%":       (1,  0.0, "high"),
        "BVPS_CAGR_%":         (1,  0.0, "high"),
    },
    "Valuation": {
        "PB":                  (3,  2.0, "low"),
        "DividendYield_%":     (1,  0.0, "high"),
        "EarningsYield_vs_rf": (1,  0.0, "high"),
    },
}
BANK_WEIGHTS = {"Profitability": 0.35, "Capital": 0.25, "Growth": 0.15, "Valuation": 0.25}
BANK_GATES = {"min_equity_to_assets": 5.0}

# Computed ratios outside these bounds are dropped as bad data.
SANITY_BOUNDS = {
    "GrossMargin_%": (-1000, 1000), "OperatingMargin_%": (-1000, 1000),
    "NetMargin_%": (-1000, 1000), "EBITDAMargin_%": (-1000, 1000),
    "ROE_%": (-1000, 1000), "ROA_%": (-1000, 1000), "ROCE_%": (-1000, 1000),
    "CurrentRatio": (0, 1000), "QuickRatio": (0, 1000),
    "DebtToEquity": (0, 1000), "LT_DebtToEquity": (0, 1000),
    "DebtToEBITDA": (-1000, 1000), "InterestCover": (-10000, 100000),
    "PE": (0, 1000), "EarningsYield_%": (-1000, 1000),
    "DividendYield_%": (0, 100), "PB": (0, 1000), "PayoutRatio_%": (0, 1000),
}
BANK_SANITY_BOUNDS = {
    "NetInterestMargin_%": (0, 100), "CapitalAdequacy_proxy_%": (0, 100),
    "ADR_%": (0, 1000), "InvestmentToDeposit_%": (0, 2000),
}

# Sector-name keywords that escalate flag severity / checklist prominence.
LEVERAGE_SENSITIVE_SECTORS = ("textile", "automobile", "auto", "cement",
                              "engineering", "steel", "sugar", "glass", "paper")
RECEIVABLES_HEAVY_SECTORS = ("fertilizer", "power", "oil", "gas", "refinery",
                             "marketing", "electric")
