"""Streamlit front-end for the PSX screener.  Run:  streamlit run app.py

Presentation only - it calls the engine and renders the result. A BUY/STRONG BUY
tier stays hidden behind a per-stock governance gate; the masked state and the
downloadable report never assert a verdict the engine didn't independently make.
"""
import io

import pandas as pd
import streamlit as st

from psxfa import analyze, write_xlsx, gate_status
from psxfa.metrics import cross_check
from psxfa.config import DEFAULT_RISKFREE

st.set_page_config(page_title="PSX Fundamental Analyzer", layout="wide")

COLOR = {"DANGER": "#e69797", "WARN": "#f2d57e", "INFO": "#d0d0d0",
         "PASS": "#a9d18e", "FAIL": "#e69797", "UNVERIFIED": "#f2d57e", "NA": "#d0d0d0"}


def badge(text, color):
    return (f"<span style='background:{color};color:#111;padding:2px 9px;"
            f"border-radius:6px;margin:2px;display:inline-block;font-size:0.82em'>"
            f"{text}</span>")


def gate_color(v):
    if v.startswith("PASS"):
        return COLOR["PASS"]
    if v.startswith("FAIL"):
        return COLOR["FAIL"]
    if v.startswith("N/A"):
        return COLOR["NA"]
    return COLOR["UNVERIFIED"]


def masked_tier(verdict):
    return verdict.startswith(("BUY", "STRONG BUY"))


st.title("PSX Fundamental Analyzer")
st.caption("Screening tool - **not** investment advice. A buy verdict is hidden "
           "until that stock's non-negotiable governance review is complete.")

c1, c2, c3 = st.columns([3, 1, 1])
tickers_raw = c1.text_input("PSX tickers (space or comma separated)", "LUCK EFERT MEBL")
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

results = st.session_state.get("results", {})
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

    g = gate_status(r)
    st.markdown("**Gates** &nbsp;&nbsp;"
                + " ".join(badge(f"{k}: {v}", gate_color(v)) for k, v in g.items()),
                unsafe_allow_html=True)

    names = r["pillar_names"]
    cols = st.columns(len(names) + 1)
    for i, p in enumerate(names):
        s = r["scores"].get(p)
        cols[i].metric(p, f"{s:.1f}/5" if s is not None else "n/a")
    comp = r["composite"]
    cols[-1].metric("Composite", f"{comp:.2f}/5" if comp is not None else "n/a",
                    help=f"Confidence: {r['confidence']}")

    if r["flags"]:
        st.markdown("**Flags** &nbsp;&nbsp;"
                    + " ".join(badge(f"{f.severity}: {f.name}", COLOR[f.severity])
                               for f in r["flags"]), unsafe_allow_html=True)
        with st.expander("Flag details"):
            for f in r["flags"]:
                st.markdown(f"- **[{f.severity}] {f.name}** - {f.explanation}")

    # Reveal is read from THIS ticker's checkbox state only, so one stock's
    # gate cannot unlock another's.
    checklist = r["checklist"]
    nonneg_keys = [f"gov::{ticker}::{gi}::{ii}"
                   for gi, (title, nn, items) in enumerate(checklist) if nn
                   for ii in range(len(items))]
    reveal = bool(nonneg_keys) and all(st.session_state.get(k, False) for k in nonneg_keys)
    verdict = r["verdict"]

    if masked_tier(verdict) and not reveal:
        done = sum(1 for k in nonneg_keys if st.session_state.get(k, False))
        st.warning(f"PENDING - complete governance review to reveal the verdict for "
                   f"**{ticker}**  ({done}/{len(nonneg_keys)} non-negotiable items checked)")
    elif masked_tier(verdict) and reveal:
        st.success(f"VERDICT (governance confirmed): **{verdict}**")
    elif verdict.startswith("AVOID"):
        st.error(f"VERDICT: **{verdict}**")
    else:
        st.info(f"VERDICT: **{verdict}**")

    st.markdown("**Human checklist** — judgment required (governance is the non-negotiable gate)")
    for gi, (title, nn, items) in enumerate(checklist):
        st.markdown(("🔒 " if nn else "") + f"**{title}**")
        for ii, item in enumerate(items):
            st.checkbox(item, key=f"gov::{ticker}::{gi}::{ii}")

    with st.expander("Audit — compute vs scstrade cross-check (ratios only, no verdict)"):
        df = pd.DataFrame([{
            "Section": sec, "Metric": me, "Computed": c, "scstrade": sv,
            "Delta_%": (round(abs(c * scale - sv) / abs(sv) * 100, 1)
                        if (c is not None and sv not in (None, 0)) else None),
            "Note": no,
        } for sec, me, c, sv, no, scale in cross_check(fin)])
        st.dataframe(df, width="stretch", hide_index=True)


for tk, (status, payload) in results.items():
    render_card(tk, status, payload)
