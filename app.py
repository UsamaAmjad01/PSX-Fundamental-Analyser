"""Streamlit front-end for the PSX screener.  Run:  streamlit run app.py

Presentation only - it calls the engine and renders the result. A BUY/STRONG BUY
tier stays hidden behind a per-stock governance gate, and the downloadable report
never asserts a verdict the engine didn't independently make.
"""
import io

import pandas as pd
import streamlit as st

from psxfa import analyze, write_xlsx, gate_status
from psxfa.metrics import cross_check
from psxfa.config import DEFAULT_RISKFREE

st.set_page_config(page_title="PSX Fundamental Analyzer", layout="wide")

# Bumped whenever the cached result shape changes, so a stale session is ignored
# rather than rendered with mismatched code.
_SCHEMA = 1

# Plain-English translations of the engine's flag names, for non-expert readers.
PLAIN = {
    "High leverage": "it carries a lot of debt relative to its own money",
    "Weak cash conversion": "it reports profit but isn't fully collecting the cash",
    "Interest coverage critical": "its profits barely cover the interest on its debt",
    "Altman Z distress": "a bankruptcy-risk screen puts it in the danger zone",
    "Altman Z grey zone": "a bankruptcy-risk screen reads borderline",
    "Altman Z safe": "low statistical bankruptcy risk",
    "Margin contraction": "its profit margins have been shrinking",
    "High Debt/EBITDA": "its debt is large compared with yearly earnings",
    "Beneish M (partial)": "an earnings-quality screen flags it for a closer look",
    "Earnings declining": "its profit is lower than last year",
    "Investment-heavy balance sheet": "most of its assets are parked in government securities",
    "Thin capital (proxy)": "it has a thin capital cushion for a bank",
}
SEV_ICON = {"DANGER": "🔴", "WARN": "🟠", "INFO": "⚪"}
TONE_ICON = {"strong": "🟢", "mixed": "🟡", "weak": "🔴"}


def masked_tier(verdict):
    return verdict.startswith(("BUY", "STRONG BUY"))


def avoid_reason(g):
    if g["Cash"].startswith("FAIL"):
        return "it isn't turning its reported profit into cash"
    if g["Safety/Capital"].startswith("FAIL"):
        return "it can't comfortably carry its debt / capital"
    if g["Stage0"].startswith("FAIL"):
        return "its accounts raise a basic red flag"
    return "the fundamentals are too weak across the board"


def plain_summary(r):
    sym = r["fin"]["sym"]
    n = sum(len(items) for _, nn, items in r["checklist"] if nn)
    verdict, comp = r["verdict"], r["composite"]
    live = [f for f in r["flags"] if f.severity in ("WARN", "DANGER")]
    if verdict.startswith(("AVOID", "INSUFFICIENT")):
        tone = "weak"
        body = avoid_reason(gate_status(r)) if verdict.startswith("AVOID") else "there isn't enough data to judge it"
        lead = f"On the numbers, **{sym}** looks weak — {body}."
    elif comp is not None and comp >= 4.0 and not live:
        tone = "strong"
        lead = (f"On the numbers, **{sym}** looks strong — good returns, a sound "
                f"balance sheet, and cash that backs up the profit.")
    else:
        tone = "mixed"
        concern = PLAIN.get(live[0].name, live[0].name) if live else "the fundamentals are only average"
        lead = f"On the numbers, **{sym}** is a mixed picture — {concern}."
    lead += f" You still need to confirm **{n}** non-negotiable check(s) below before this is a real verdict."
    return tone, lead


st.title("PSX Fundamental Analyzer")
st.caption("Screening tool - **not** investment advice. A buy verdict stays hidden "
           "until you complete that stock's non-negotiable governance review.")

c1, c2, c3 = st.columns([3, 1, 1])
tickers_raw = c1.text_input("PSX tickers (space or comma separated)", "OGDC EFERT MEBL")
riskfree = c2.number_input("Risk-free (T-bill %)", min_value=0.0, max_value=50.0,
                           value=float(DEFAULT_RISKFREE), step=0.5)
analyze_clicked = c3.button("Analyze", type="primary", width="stretch")

if analyze_clicked:
    tickers = [t.strip().upper() for t in tickers_raw.replace(",", " ").split() if t.strip()]
    out = {}
    with st.spinner("Fetching statements and scoring..."):
        for t in tickers:
            try:
                out[t] = ("ok", analyze(t, riskfree))
            except Exception as e:
                out[t] = ("err", str(e))
    st.session_state["results"] = out
    st.session_state["_schema"] = _SCHEMA

results = st.session_state.get("results", {})
if st.session_state.get("_schema") != _SCHEMA:
    results = {}      # cached from an older version - ignore it
if not results:
    st.info("Enter one or more PSX tickers and click **Analyze**.")
    st.stop()

ok_results = [p for status, p in results.values() if status == "ok"]
if ok_results:
    buf = io.BytesIO()
    write_xlsx(ok_results, buf)
    st.download_button("Download full report (XLSX)", data=buf.getvalue(),
                       file_name="psx_report.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("The report carries the engine's quant tier with final status PENDING; "
               "it does not assert a BUY.")


def render_card(ticker, status, payload):
    st.divider()
    if status == "err":
        st.error(f"**{ticker}** - could not analyze: {payload}")
        return
    r, m, fin = payload, payload["m"], payload["fin"]
    prof = "bank" if r["profile"] == "bank" else "non-bank"
    price = f"Rs {m['price']}" if m.get("price") is not None else "price n/a"
    st.subheader(f"{ticker}  ·  {fin.get('sector')}  ·  {prof}  ·  FY{m['year']}  ·  {price}")

    # 1) plain-English lead
    tone, lead = plain_summary(r)
    st.markdown(f"#### {TONE_ICON[tone]} {lead}")

    # governance state for THIS ticker (read before the widgets are drawn)
    checklist = r["checklist"]
    nonneg = [f"gov::{ticker}::{gi}::{ii}"
              for gi, (title, nn, items) in enumerate(checklist) if nn
              for ii in range(len(items))]
    states = [st.session_state.get(k, "Not checked") for k in nonneg]
    gov_concern = "Concern" in states
    gov_clear = bool(states) and all(s == "OK" for s in states)
    verdict = r["verdict"]

    # 2) verdict box (gated)
    if gov_concern:
        st.error("VERDICT: **AVOID — governance concern** (you flagged a non-negotiable item)")
    elif masked_tier(verdict) and not gov_clear:
        st.warning(f"**PENDING** — mark every non-negotiable item OK to reveal the verdict "
                   f"({sum(s == 'OK' for s in states)}/{len(nonneg)} done)")
    elif masked_tier(verdict):
        st.success(f"VERDICT (governance confirmed): **{verdict}**")
    elif verdict.startswith("AVOID"):
        st.error(f"VERDICT: **{verdict}**")
    else:
        st.info(f"VERDICT: **{verdict}**")

    # 3) flags, plain-language, always visible
    if r["flags"]:
        st.markdown("**What to watch**")
        for f in r["flags"]:
            st.markdown(f"{SEV_ICON[f.severity]} {PLAIN.get(f.name, f.name)}")
            st.caption(f"{f.name}: {f.explanation}")
    else:
        st.markdown("✅ No red flags on the numbers.")

    if r.get("review_flags"):
        with st.expander("Review notes", expanded=True):
            for note in r["review_flags"]:
                st.markdown(f"- {note}")

    # 4) checklist - non-negotiables are a 3-way choice that gates the verdict
    st.markdown("**Checks you must do** — the tool cannot judge these")
    for gi, (title, nn, items) in enumerate(checklist):
        st.markdown(("🔒 " if nn else "") + f"**{title}**")
        for ii, (item, hint) in enumerate(items):
            key = f"gov::{ticker}::{gi}::{ii}"
            if nn:
                st.radio(item, ["Not checked", "OK", "Concern"], key=key, horizontal=True)
            else:
                st.checkbox(item, key=key)
            st.caption(f"How to check: {hint}")

    # 5) details + advanced, collapsed
    with st.expander("See the details (pillar scores and gates)"):
        g = gate_status(r)
        st.write({k: v for k, v in g.items()})
        cols = st.columns(len(r["pillar_names"]) + 1)
        for i, p in enumerate(r["pillar_names"]):
            s = r["scores"].get(p)
            cols[i].metric(p, f"{s:.1f}/5" if s is not None else "n/a")
        comp = r["composite"]
        cols[-1].metric("Composite", f"{comp:.2f}/5" if comp is not None else "n/a",
                        help=f"Confidence: {r['confidence']}")

    with st.expander("For advanced users — data cross-check"):
        df = pd.DataFrame([{
            "Section": sec, "Metric": me, "Computed": c, "scstrade": sv,
            "Delta_%": (round(abs(c * scale - sv) / abs(sv) * 100, 1)
                        if (c is not None and sv not in (None, 0)) else None),
            "Note": no,
        } for sec, me, c, sv, no, scale in cross_check(fin)])
        st.dataframe(df, width="stretch", hide_index=True)


for tk, (status, payload) in results.items():
    render_card(tk, status, payload)
