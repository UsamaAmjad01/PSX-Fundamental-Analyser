"""Auto-detected forensic flags and the human-judgment checklist."""
from __future__ import annotations
from collections import namedtuple

from .config import LEVERAGE_SENSITIVE_SECTORS, RECEIVABLES_HEAVY_SECTORS
from .data import num

Flag = namedtuple("Flag", "name severity value explanation")
SEV_RANK = {"INFO": 0, "WARN": 1, "DANGER": 2}


def _line(line_map, label, year):
    return num(line_map.get(label, {}).get(year))


def beneish_partial(fin, ocf):
    """Beneish M-score without the receivables (DSRI) term, which scstrade can't
    supply. Indicative only; SG&A is proxied as gross profit minus EBIT."""
    inc, bal, y, py = fin["income"], fin["balance"], fin["latest"], fin["prev"]
    if not py:
        return None
    s, sp = _line(inc, "Sales", y), _line(inc, "Sales", py)
    gp, gpp = _line(inc, "Gross Profit", y), _line(inc, "Gross Profit", py)
    eb = _line(inc, "Operating Profit (EBIT)", y) or _line(inc, "Operating Profit", y)
    ebp = _line(inc, "Operating Profit (EBIT)", py) or _line(inc, "Operating Profit", py)
    ta, tap = _line(bal, "Total Assets", y), _line(bal, "Total Assets", py)
    ca, cap = _line(bal, "Current Asset", y), _line(bal, "Current Asset", py)
    ppe, ppep = _line(bal, "Fixed Asset", y), _line(bal, "Fixed Asset", py)
    dep, depp = _line(inc, "Depreciation", y), _line(inc, "Depreciation", py)
    tl, tlp = _line(bal, "Total Liabilities", y), _line(bal, "Total Liabilities", py)
    pat = _line(inc, "Profit After Tax", y)
    if any(v is None for v in (s, sp, gp, gpp, ta, tap, ca, cap, ppe, ppep, tl, tlp)):
        return None
    if 0 in (sp, tap, s):
        return None

    dsri = 1.0  # receivables term omitted, held neutral
    gmi = (gpp / sp) / (gp / s) if (gp and s) else 1.0
    sgi = s / sp
    aqi_p = 1 - (cap + ppep) / tap
    aqi = ((1 - (ca + ppe) / ta) / aqi_p) if aqi_p else 1.0
    if dep and depp and ppe and ppep:
        dr_c, dr_p = dep / (dep + ppe), depp / (depp + ppep)
        depi = (dr_p / dr_c) if dr_c else 1.0
    else:
        depi = 1.0
    lvgi = (tl / ta) / (tlp / tap)
    if None not in (gp, eb, gpp, ebp):
        sga, sgap = gp - eb, gpp - ebp
        sgai = (sga / s) / (sgap / sp) if (sgap and sp) else 1.0
    else:
        sgai = 1.0
    tata = ((pat - ocf) / ta) if (pat is not None and ocf is not None and ta) else 0.0

    return round(-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
                 + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi, 2)


def compute_flags(m, fin):
    flags = []
    sector = (fin.get("sector") or "")
    sec = sector.lower()

    if fin["is_bank"]:
        eg = m.get("EarningGrowth_%")
        if eg is not None and eg < 0:
            flags.append(Flag("Earnings declining", "WARN", round(eg, 1),
                              f"earnings growth {eg:.1f}% < 0"))
        idr = m.get("InvestmentToDeposit_%")
        if idr is not None and idr > 60:
            flags.append(Flag("Investment-heavy balance sheet", "INFO", round(idr, 0),
                              f"investments {idr:.0f}% of deposits - government-securities "
                              f"and rate-cycle exposure"))
        cap = m.get("CapitalAdequacy_proxy_%")
        if cap is not None and cap < 5:
            flags.append(Flag("Thin capital (proxy)", "DANGER", round(cap, 2),
                              f"Equity/Assets {cap:.2f}% < 5% (verify real CAR)"))
        return sorted(flags, key=lambda f: -SEV_RANK[f.severity])

    de = m.get("DebtToEquity")
    if de is not None and de > 1.0:
        sev = "DANGER" if de > 2.5 else "WARN"
        note = f"Total-Liabilities/Equity {de:.2f} > 1.0"
        if sev == "WARN" and any(k in sec for k in LEVERAGE_SENSITIVE_SECTORS):
            sev, note = "DANGER", note + f" (leverage-sensitive sector: {sector})"
        flags.append(Flag("High leverage", sev, round(de, 2), note))

    conv, label = m.get("cumOCF_to_NI"), "cum 5y OCF/NI"
    if conv is None:
        conv, label = m.get("OCF_to_NI"), "OCF/NI"
    if conv is not None and conv < 1.0:
        sev = "DANGER" if conv < 0.5 else "WARN"
        extra = " (circular-debt risk)" if any(k in sec for k in RECEIVABLES_HEAVY_SECTORS) else ""
        flags.append(Flag("Weak cash conversion", sev, round(conv, 2),
                          f"{label} {conv:.2f} < 1 - profits not converting to cash{extra}"))

    ic = m.get("InterestCover")
    if ic is not None and ic < 1.5:
        flags.append(Flag("Interest coverage critical", "DANGER", round(ic, 2),
                          f"EBIT/finance {ic:.2f} < 1.5 - also fails the Safety gate"))

    z = m.get("AltmanZ")
    if z is not None:
        if z < 1.8:
            flags.append(Flag("Altman Z distress", "DANGER", round(z, 2),
                              f"Z {z:.2f} < 1.8 (distress zone; retained earnings proxied)"))
        elif z < 2.99:
            flags.append(Flag("Altman Z grey zone", "WARN", round(z, 2),
                              f"Z {z:.2f} in the 1.8-2.99 grey zone"))
        else:
            flags.append(Flag("Altman Z safe", "INFO", round(z, 2),
                              f"Z {z:.2f} > 2.99 (safe zone)"))

    mt = m.get("MarginTrend_pp")
    if mt is not None and mt < 0:
        flags.append(Flag("Margin contraction", "WARN", round(mt, 1),
                          f"net margin trend {mt:+.1f}pp over available years"))

    dte = m.get("DebtToEBITDA")
    if dte is not None and dte > 4:
        flags.append(Flag("High Debt/EBITDA", "WARN", round(dte, 2),
                          f"Total-Liab/EBITDA {dte:.2f} > 4"))

    bm = beneish_partial(fin, m.get("OCF"))
    if bm is not None:
        sev = "WARN" if bm > -2.22 else "INFO"
        verdict = "above -2.22, worth scrutiny" if bm > -2.22 else "below -2.22"
        flags.append(Flag("Beneish M (partial)", sev, bm,
                          f"partial M={bm} ({verdict}); receivables term omitted - indicative only"))

    return sorted(flags, key=lambda f: -SEV_RANK[f.severity])


def human_checklist(m, fin):
    """Groups the tool can't judge. Returns (title, non_negotiable, [items])."""
    sector = fin.get("sector") or "unknown sector"
    sec = sector.lower()
    groups = [("GOVERNANCE & MANAGEMENT  [NON-NEGOTIABLE GATE]", True, [
        "Sponsor / group reputation and record with minority shareholders",
        "Related-party transactions - magnitude and terms",
        "History of minority squeeze or delisting attempts at low prices",
        "Board independence and audit-committee quality",
        "Dividend track record as a proxy for fair minority treatment",
    ])]

    if fin["is_bank"]:
        groups.append(("BANK REGULATORY - verify from annual report", True, [
            "CAR >= ~11.5% incl. buffers (proxy Equity/Assets shown; not risk-weighted)",
            "NPL / infection ratio and trend",
            "Coverage ratio (provisions / NPLs)",
            "Cost-to-income ratio (<50% efficient)",
            "CASA ratio (higher = cheaper funding)",
            "Current effective tax rate and ADR-tax status",
        ]))

    groups.append(("STAGE 0 - QUALITATIVE RED FLAGS", False, [
        "Auditor qualification / emphasis-of-matter / going-concern note",
        "Free float < 10-15% (confirm on PSX)",
        "Sponsor shares pledged",
        "Defaulter / Non-Compliant / DEL status",
        "Frequent change of auditors or directors",
    ]))

    rec_items = [
        "Receivables growing faster than sales",
        "DSO / cash-conversion-cycle trend (no receivables line in the data)",
    ]
    title = "RECEIVABLES / CIRCULAR DEBT"
    if any(k in sec for k in RECEIVABLES_HEAVY_SECTORS):
        title += f"  [HIGH PRIORITY - {sector}]"
        rec_items.append("Reconcile reported profit vs cash actually collected "
                         "from government / DISCOs / CPPA")
    groups.append((title, False, rec_items))

    groups.append((f"MACRO / SECTOR  [{sector}]", False, [
        "SBP policy rate / KIBOR direction vs this firm's leverage",
        "PKR/USD and FX-linked debt or imported-input exposure",
        "IMF programme status and FX reserves",
        "Finance Act tax / duty changes affecting this sector",
        "Relevant commodity input prices",
    ]))
    groups.append(("MOAT & CATALYST", False, [
        "Durable competitive advantage vs peers",
        "Identifiable re-rating catalyst in the next 6-18 months",
    ]))
    return groups
