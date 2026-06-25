# PSX Fundamental Analyzer

A command-line tool that turns **Pakistan Stock Exchange (PSX)** company
fundamentals into **auditable BUY / HOLD / AVOID verdicts** using a gated
three‑pillar scoring model (Quality, Safety, Valuation).

You pass one or more tickers; it fetches each company's raw annual financial
statements, **computes the ratios itself from exact line items**, cross-checks
them against the data source's own pre-computed ratios, and prints a verdict
plus a fully auditable per-metric breakdown — also saved to CSV and XLSX.

```bash
python psx_verdict.py LUCK MEBL HUBC SYS
```

```
LUCK   STRONG BUY                  composite=91.5  Q=90.0 S=100.0 V=80.0   [standard, FY2025]
MEBL   STRONG BUY                  composite=90.0  Q=77.8 S=100.0 V=100.0  [bank,     FY2025]
HUBC   AVOID (weak balance sheet)  composite=64.6  Q=70.0 S=37.5  V=100.0  [standard, FY2025]
SYS    BUY (but looks expensive)   composite=84.0  Q=100.0 S=100.0 V=20.0  [standard, FY2025]
```

> **Disclaimer:** This is a fundamental *screening* tool, **not investment
> advice**. Verdicts are a mechanical reading of past filings. Always verify the
> `_detail` columns before acting on anything.

---

## Why it exists

A previous attempt scraped a ratio website with a loose regex ("grab the first
number after this label") and silently returned garbage — e.g. debt‑to‑equity =
`2021` (that was a *year*). This tool is built to never do that:

- Every value is read from an **explicitly labeled row and named period column**
  in structured JSON — never by proximity to a label.
- Every computed ratio is **cross-checked** against the source's own figure;
  mismatches over 5% are flagged in the output.
- **Magnitude sanity bounds** reject impossible values (a ratio of 2021, a
  negative revenue, P/E > 1000, …) and treat them as missing instead of passing
  them through.

## Data sources

| Data | Source |
| --- | --- |
| Current price | `psxdata.quote()` (PSX screener) |
| Income statement (5 yrs) | scstrade `SS_CompanySnapShotYF.aspx/chart1` (JSON) |
| Balance sheet (5 yrs) | scstrade `SS_CompanySnapShotYF.aspx/chart3` (JSON) |
| Pre-computed ratios (5 yrs, for cross-check) | scstrade `SS_CompanySnapShotYR.aspx/chart` (JSON) |

Statement values are in PKR thousands; EPS and price are rupees per share.
Ratios are computed from same-unit line items, so units cancel.

## The verdict model

Three pillars are scored. Each metric is `(weight, threshold, direction)`; a
pillar's score is `weights_passed / weights_with_data`, so **missing metrics
lower confidence — they don't fail the stock**.

**Quality (45%)** — ROE, net margin, operating margin, profit growth
**Safety (35%)** — current ratio, debt-to-equity (= Total Liabilities / Equity),
interest cover
**Valuation (20%)** — P/E, dividend yield, payout ratio

The decision is **sequential / gated**, not a flat point total:

1. Coverage < ~1 of 3 pillars → `INSUFFICIENT DATA`
2. Safety pillar < 0.40 → `AVOID (weak balance sheet)` — a fragile balance sheet
   overrides cheapness and profitability
3. Quality pillar < 0.40 → `AVOID (weak business quality)`
4. Otherwise composite = `0.45·Q + 0.35·S + 0.20·V` over available pillars:
   `≥0.75` → `STRONG BUY` · `≥0.60` → `BUY` · `≥0.45` → `HOLD / WATCH` ·
   else `AVOID`. A BUY whose valuation pillar < 0.30 is tagged
   `BUY (but looks expensive)`.

Confidence (High / Medium / Low) reflects how many of the three pillars had data.

### Sector profiles

Banks are deposit-funded and have a different statement shape (Net Interest
Income instead of Sales; no Operating Profit or Current Liability), so they are
auto-detected and scored with a **bank profile** (ROE, Net Interest Margin, ROA,
capital adequacy = Equity / Total Assets). All thresholds live in a clearly
marked config block at the top of `psx_verdict.py` for easy tuning, and the
structure supports adding more sector profiles later.

---

## Install

Requires **Python 3.11+** (`psxdata` needs 3.11+).

```bash
# clone, then from the project folder:
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

## Usage

```bash
python psx_verdict.py OCTOPUS                 # one ticker
python psx_verdict.py LUCK MEBL HUBC SYS      # many tickers
python psx_verdict.py LUCK --out my_results   # custom output basename
```

A bad ticker is reported per-symbol and does not crash the run.

## Output

Console prints the verdict, composite score, pillar scores, confidence and
coverage per symbol. Two files are written (default basename `verdict_output`):

- `verdict_output.csv`
- `verdict_output.xlsx`

Columns: `Symbol, Profile, FY, Verdict, Composite_%, Quality_%, Safety_%,
Valuation_%, Confidence, Coverage, Price`, every raw metric, plus three audit
columns — `_quality_detail`, `_safety_detail`, `_valuation_detail` — that show
each metric like:

```
ROE_%=19.83 PASS(thr 15) [scs 19.83 ok]
DebtToEquity=0.88 PASS(thr 1) [scs 87.96 ok]
InterestCover=3.84 PASS(thr 3) [scs 5.15 (alt-def)]
```

`[scs …]` is the source's own pre-computed value used for cross-checking;
`ok` = within 5%, `diffNN%!` = mismatch to investigate, `(alt-def)` = a known
definitional difference (e.g. scstrade's interest cover uses a broader EBIT).

## Limitations

- Data is annual (latest completed fiscal year). Quarterly/TTM endpoints exist
  and could be wired in for trailing-twelve-month growth.
- Bank thresholds are first-pass estimates — tune them in `BANK_PROFILE`.
- Relies on third-party endpoints (`psxdata`, scstrade); both can change or
  rate-limit. Network calls retry with backoff.
