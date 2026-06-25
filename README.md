# PSX Fundamental Analyzer

A fundamental screener for the **Pakistan Stock Exchange (PSX)**. Give it a list
of tickers and it pulls each company's five years of financial statements,
computes the ratios, runs them through a gated scoring model, and returns a
**BUY / HOLD / AVOID** read — alongside the parts of the analysis a tool *can't*
do, surfaced as a checklist instead of a fake answer.

It runs as a CLI or as a small Streamlit app.

```bash
python -m psxfa LUCK EFERT MEBL --riskfree 11     # console + XLSX
streamlit run app.py                              # interactive UI
```

> Screening tool, **not** investment advice. Verdicts are a mechanical reading of
> past filings; always check the audit trail and complete the governance review
> before acting.

## How it works

The analysis is deliberately split into three tiers:

- **Compute** — everything numeric: profitability, liquidity, solvency,
  efficiency, cash, valuation, growth, plus Altman Z and a partial Beneish M.
  Every ratio is computed from raw line items and cross-checked against the data
  source's own figure; mismatches over 5% are flagged.
- **Flag** — auto-detectable forensics (high leverage, weak cash conversion,
  margin contraction, distress scores) raised as INFO / WARN / DANGER, never as
  an automatic pass or fail.
- **Human** — what no tool can judge (governance, related-party leakage, free
  float, circular-debt receivables, macro, moat) is printed as an unchecked
  checklist. **Governance is a non-negotiable gate: no BUY is issued until it is
  confirmed.**

Scoring uses five weighted pillars — Profitability, Safety, Cash, Growth,
Valuation. **Safety and Cash are hard gates**: fail either and the verdict is
AVOID regardless of how cheap or profitable the stock is. Banks are detected
automatically and routed to a separate metric set (NIM, ROA, capital adequacy,
P/B) with bank-appropriate thresholds.

## Data sources

| Data | Source |
| --- | --- |
| Income statement, balance sheet, ratios (5 yrs) | scstrade |
| Current price and sector | `psxdata` |

Statement values are in PKR thousands; per-share figures are in rupees. Some
metrics aren't available in the free data (trade receivables, a cash-flow
statement, bank CAR/NPL/CASA) — those become flagged approximations or human
checklist items rather than silent gaps.

## Install

Requires Python 3.11+.

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # Windows PowerShell
# source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt
```

## Usage

```bash
python -m psxfa OCTOPUS                       # one ticker
python -m psxfa LUCK MEBL HUBC SYS            # several
python -m psxfa LUCK --riskfree 20 --out run  # set the T-bill hurdle + output name
```

`--riskfree` is the T-bill / policy rate used for the earnings-yield comparison.
A bad ticker is reported and skipped without crashing the run.

The Streamlit app (`streamlit run app.py`) shows the same analysis with coloured
flag badges, an interactive governance checklist that unlocks the verdict
per-stock, an audit expander for the cross-check table, and an XLSX download.

## Output

`python -m psxfa ...` writes `psx_report.xlsx` with four sheets:

- **Verdicts** — one row per stock: verdict, pillar scores, gates, every ratio,
  both debt-to-equity definitions, Altman Z, partial Beneish, confidence, flags.
- **Flags** — colour-coded by severity.
- **Human_Checklist** — every item, non-negotiables highlighted.
- **CrossCheck** — computed vs source value with the delta per ratio.

## Project layout

```
psxfa/
  config.py    thresholds, weights, gates, sanity bounds  (tune here)
  data.py      fetch statements / ratios / price / sector
  metrics.py   ratio computation + the cross-check
  scoring.py   gated pillar scoring (standard + bank)
  flags.py     forensic flags, partial Beneish, human checklist
  report.py    orchestration, console output, XLSX export
app.py         Streamlit UI (presentation only)
```

All thresholds live in `psxfa/config.py`; the structure leaves room for
per-sector overrides.

## Limitations

- Annual data only (latest completed fiscal year).
- Bank capital is gated on Equity/Total-Assets as a proxy; real risk-weighted
  CAR, NPL, CASA and cost-to-income must be verified from the annual report.
- Relies on third-party endpoints that can change or rate-limit; network calls
  retry with backoff.
