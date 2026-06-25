"""Gated pillar scoring. Safety and Cash are hard gates; a fail means AVOID."""
from __future__ import annotations

from .config import (PILLARS, PILLAR_WEIGHTS, GATES, TIERS, VALUATION_EXPENSIVE,
                     BANK_PILLARS, BANK_WEIGHTS, BANK_GATES)
from .metrics import compute_metrics
from .flags import compute_flags

_TIER_ORDER = ["AVOID", "HOLD / WATCH", "BUY", "STRONG BUY"]
# Forensic flags that contradict a specific gate when that gate still "passes".
_CASH_FLAGS = {"Weak cash conversion"}
_SAFETY_FLAGS = {"High leverage", "High Debt/EBITDA", "Interest coverage critical"}


def _soft_cap(tier, has_warn, has_danger):
    """A top tier can't coexist with live warnings: WARN caps below STRONG BUY,
    DANGER caps below BUY."""
    if tier.startswith("AVOID"):
        return tier
    cap = len(_TIER_ORDER) - 1
    if has_warn:
        cap = min(cap, _TIER_ORDER.index("BUY"))
    if has_danger:
        cap = min(cap, _TIER_ORDER.index("HOLD / WATCH"))
    return _TIER_ORDER[min(_TIER_ORDER.index(tier), cap)]


def score_pillar(metrics, spec):
    earned = available = 0.0
    detail = []
    for name, (weight, threshold, direction) in spec.items():
        v = metrics.get(name)
        if v is None:
            detail.append(f"{name}: n/a")
            continue
        available += weight
        passed = (v >= threshold) if direction == "high" else (v <= threshold)
        if passed:
            earned += weight
        detail.append(f"{name}={v:.2f} {'PASS' if passed else 'FAIL'}({threshold:g})")
    score = (earned / available * 5.0) if available else None
    return score, available, detail


def _compose(scores, weights):
    num = den = 0.0
    for p, w in weights.items():
        if scores[p] is not None:
            num += w * scores[p]
            den += w
    composite = (num / den) if den else None
    coverage = den / sum(weights.values())
    return composite, coverage


def _tier(composite):
    if composite >= TIERS["STRONG BUY"]:
        return "STRONG BUY"
    if composite >= TIERS["BUY"]:
        return "BUY"
    if composite >= TIERS["HOLD / WATCH"]:
        return "HOLD / WATCH"
    return "AVOID (low composite)"


def evaluate(fin, riskfree):
    m = compute_metrics(fin)
    ey = m.get("EarningsYield_%")
    m["EarningsYield_vs_rf"] = (ey - riskfree) if ey is not None else None

    stage0 = []
    if GATES["require_positive_equity"] and not (m.get("equity") and m["equity"] > 0):
        stage0.append("negative or unknown equity")

    ic, de = m.get("InterestCover"), m.get("DebtToEquity")
    safety_fail = []
    if ic is not None and ic < GATES["min_interest_cover"]:
        safety_fail.append(f"InterestCover {ic:.2f} < {GATES['min_interest_cover']}")
    safety_unverified = ic is None

    review = []
    if de is not None and de > GATES["max_debt_to_equity"]:
        review.append(f"High leverage: TL/Equity {de:.2f} > {GATES['max_debt_to_equity']} "
                      f"- verify borrowings vs payables and any FX-debt exposure")
    if safety_unverified:
        review.append("Safety coverage unverified (no finance cost / EBIT line)")

    cum = m.get("cumOCF_to_NI")
    cash_fail = []
    cash_unverified = cum is None
    if not cash_unverified:
        if not m.get("ocf_positive"):
            cash_fail.append("OCF not positive")
        if cum < GATES["min_cum_ocf_to_ni"]:
            cash_fail.append(f"cumOCF/NI {cum:.2f} < {GATES['min_cum_ocf_to_ni']}")

    scores, details = {}, {}
    for name, spec in PILLARS.items():
        scores[name], _, details[name] = score_pillar(m, spec)
    composite, coverage = _compose(scores, PILLAR_WEIGHTS)

    flags = compute_flags(m, fin)
    live = [f for f in flags if f.severity in ("WARN", "DANGER")]
    has_warn = any(f.severity == "WARN" for f in live)
    has_danger = any(f.severity == "DANGER" for f in live)
    cash_soft = (not cash_fail and not cash_unverified
                 and any(f.name in _CASH_FLAGS for f in live))
    safety_soft = (not safety_fail and not safety_unverified
                   and any(f.name in _SAFETY_FLAGS for f in live))
    if cash_soft:
        cum = m.get("cumOCF_to_NI")
        review.insert(0, f"Cash quality: cumOCF/NI {cum:.2f} < 1 - reports profit but "
                         f"isn't fully collecting the cash; check receivables / circular debt")

    weak_valuation = (scores["Valuation"] is not None
                      and scores["Valuation"] < VALUATION_EXPENSIVE)
    if stage0:
        verdict = "AVOID (Stage 0: " + "; ".join(stage0) + ")"
    elif safety_fail:
        verdict = "AVOID (fails Safety gate: " + "; ".join(safety_fail) + ")"
    elif cash_fail:
        verdict = "AVOID (fails Cash gate: " + "; ".join(cash_fail) + ")"
    elif composite is None:
        verdict = "INSUFFICIENT DATA"
    else:
        tier = _tier(composite)
        verdict = _soft_cap(tier, has_warn, has_danger)
        if verdict != tier:
            review.insert(0, f"Top tier withheld ({tier} -> {verdict}): live forensic "
                             f"flag(s) - {', '.join(f.name for f in live)}")
        if verdict in ("BUY", "STRONG BUY") and weak_valuation:
            verdict += " (but looks expensive)"
    if verdict.startswith(("BUY", "STRONG BUY")):
        if safety_unverified:
            verdict = "HOLD / WATCH (safety unverified)"
        elif cash_unverified:
            verdict = "HOLD / WATCH (cash unverified)"

    return {
        "m": m, "scores": scores, "details": details, "flags": flags,
        "composite": composite, "coverage": coverage,
        "confidence": _confidence(coverage), "verdict": verdict,
        "stage0": stage0, "safety_fail": safety_fail, "cash_fail": cash_fail,
        "safety_unverified": safety_unverified, "cash_unverified": cash_unverified,
        "cash_soft": cash_soft, "safety_soft": safety_soft,
        "review_flags": review,
    }


def evaluate_bank(fin, riskfree):
    m = compute_metrics(fin)
    ey = m.get("EarningsYield_%")
    m["EarningsYield_vs_rf"] = (ey - riskfree) if ey is not None else None

    stage0 = []
    if not (m.get("equity") and m["equity"] > 0):
        stage0.append("negative or unknown equity")

    cap = m.get("CapitalAdequacy_proxy_%")
    cap_fail = []
    cap_unverified = cap is None
    if cap is not None and cap < BANK_GATES["min_equity_to_assets"]:
        cap_fail.append(f"Equity/Assets {cap:.2f}% < {BANK_GATES['min_equity_to_assets']}%")

    scores, details = {}, {}
    for name, spec in BANK_PILLARS.items():
        scores[name], _, details[name] = score_pillar(m, spec)
    composite, coverage = _compose(scores, BANK_WEIGHTS)

    review = [f"Real CAR unavailable - capital gated on Equity/Assets {cap:.2f}% "
              f"(not risk-weighted); verify CAR >= 11.5%" if cap is not None
              else "Equity/Assets unavailable - capital unverified"]
    if m.get("ADR_%") is not None:
        review.append(f"ADR {m['ADR_%']:.1f}% (advances/deposits) - informational")
    if m.get("InvestmentToDeposit_%") is not None:
        review.append(f"Investment/Deposit {m['InvestmentToDeposit_%']:.0f}% "
                      f"- sovereign / rate-cycle exposure")

    flags = compute_flags(m, fin)
    live = [f for f in flags if f.severity in ("WARN", "DANGER")]
    has_warn = any(f.severity == "WARN" for f in live)
    has_danger = any(f.severity == "DANGER" for f in live)
    capital_soft = (not cap_fail and not cap_unverified
                    and any(f.name == "Thin capital (proxy)" for f in live))

    weak_valuation = (scores["Valuation"] is not None
                      and scores["Valuation"] < VALUATION_EXPENSIVE)
    if stage0:
        verdict = "AVOID (Stage 0: " + "; ".join(stage0) + ")"
    elif cap_fail:
        verdict = "AVOID (fails Capital gate: " + "; ".join(cap_fail) + ")"
    elif composite is None:
        verdict = "INSUFFICIENT DATA"
    else:
        tier = _tier(composite)
        verdict = _soft_cap(tier, has_warn, has_danger)
        if verdict != tier:
            review.insert(0, f"Top tier withheld ({tier} -> {verdict}): live forensic "
                             f"flag(s) - {', '.join(f.name for f in live)}")
        if verdict in ("BUY", "STRONG BUY") and weak_valuation:
            verdict += " (but looks expensive)"
    # CAR is the real capital test and it's human-verified, so never auto-confirm.
    if verdict.startswith(("BUY", "STRONG BUY")):
        verdict += " - pending CAR/NPL review"

    return {
        "m": m, "scores": scores, "details": details, "flags": flags,
        "composite": composite, "coverage": coverage,
        "confidence": _confidence(coverage), "verdict": verdict,
        "stage0": stage0, "cap_fail": cap_fail, "cap_unverified": cap_unverified,
        "capital_soft": capital_soft, "review_flags": review,
    }


def _confidence(coverage):
    return "High" if coverage > 0.8 else "Medium" if coverage > 0.5 else "Low"
