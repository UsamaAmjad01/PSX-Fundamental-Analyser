"""Fetching PSX financials from scstrade (statements + ratios) and psxdata (price)."""
from __future__ import annotations
import json
import time

import requests

try:
    import psxdata
except Exception:
    psxdata = None

_BASE = "https://scstrade.com/stockscreening/"
_HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36")}


def _retry(fn, *args, tries=4, base_delay=1.5, **kwargs):
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
    url = f"{_BASE}{page}.aspx/{endpoint}"
    headers = {**_HEADERS, "Content-Type": "application/json; charset=utf-8",
               "Referer": f"{_BASE}{page}.aspx?symbol={sym}",
               "X-Requested-With": "XMLHttpRequest"}
    r = _retry(requests.post, url, data=json.dumps({"sym": sym}),
               headers=headers, timeout=30)
    r.raise_for_status()
    return r.json().get("d") or []


def _rows_to_map(rows):
    out = {}
    for row in rows:
        label = row.get("QYear")
        if label is not None:
            out[label] = {k: v for k, v in row.items() if k != "QYear"}
    return out


def fetch_income(sym):
    return _rows_to_map(_post_grid("SS_CompanySnapShotYF", "chart1", sym))


def fetch_distributions(sym):
    return _rows_to_map(_post_grid("SS_CompanySnapShotYF", "chart2", sym))


def fetch_balance(sym):
    return _rows_to_map(_post_grid("SS_CompanySnapShotYF", "chart3", sym))


def fetch_ratios(sym):
    """scstrade's pre-computed yearly ratios. The page seeds a session cookie on
    GET, then returns the data on a POST with an empty body."""
    s = requests.Session()
    s.headers.update(_HEADERS)
    url = f"{_BASE}SS_CompanySnapShotYR.aspx?symbol={sym}"
    _retry(s.get, url, timeout=30)
    r = _retry(s.post, f"{_BASE}SS_CompanySnapShotYR.aspx/chart",
               data=json.dumps({}),
               headers={"Content-Type": "application/json; charset=utf-8",
                        "Referer": url}, timeout=30)
    r.raise_for_status()
    payload = r.json().get("d")
    if not payload:
        return {}
    tables = json.loads(payload) if isinstance(payload, str) else payload
    flat = {}
    for rows in tables.values():
        for row in rows:
            name = row.get("QYear", "").split(" - ")[0].strip().lower()
            flat[name] = {k: v for k, v in row.items() if k != "QYear"}
    return flat


def fetch_price(sym):
    if psxdata is None:
        return None
    try:
        q = psxdata.quote(sym)
        if q is None or getattr(q, "empty", True):
            return None
        return num(q.iloc[0].get("price"))
    except Exception:
        return None


_SECTOR_MAP = {}


def fetch_sector(sym):
    """PSX sector name (e.g. 'CEMENT') via psxdata, or None if unavailable."""
    if psxdata is None:
        return None
    try:
        q = psxdata.quote(sym)
        if q is None or getattr(q, "empty", True):
            return None
        code = q.iloc[0].get("sector")
        if code is None:
            return None
        if not _SECTOR_MAP:
            for _, r in psxdata.sectors().iterrows():
                _SECTOR_MAP[str(int(r["sector_code"]))] = r["sector_name"]
        return _SECTOR_MAP.get(str(int(float(code))))
    except Exception:
        return None


def num(s):
    """Parse a scstrade cell to float, handling commas, %, and bn/mn suffixes."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace(",", "").replace("%", "")
    if t in ("", "-", "N/A", "NA", "nan", "None"):
        return None
    mult, low = 1.0, t.lower()
    for suffix, factor in ((" bn", 1e9), (" b", 1e9), (" mn", 1e6), (" m", 1e6)):
        if low.endswith(suffix):
            t, mult = t[:-len(suffix)].strip(), factor
            break
    try:
        return float(t) * mult
    except ValueError:
        return None


def year_cols(line_map):
    cols = set()
    for years in line_map.values():
        cols.update(years.keys())
    return sorted((c for c in cols if c[1:].isdigit()),
                  key=lambda c: int(c[1:]), reverse=True)


def li(line_map, label, year):
    return num(line_map.get(label, {}).get(year))


def scs(ratios, name, year):
    rec = ratios.get(name.lower())
    return num(rec.get(year)) if rec else None


def is_bank(income):
    labels = set(income.keys())
    return "Net Interest Income" in labels and "Sales" not in labels


def build_financials(sym):
    """Fetch every statement for a ticker and pick the latest reported year."""
    sym = sym.upper()
    income = fetch_income(sym)
    if not income:
        raise ValueError(f"no income statement from scstrade for {sym}")

    cols = year_cols(income)
    pat = income.get("Profit After Tax", {})
    latest = next((c for c in cols if num(pat.get(c)) not in (None, 0.0)),
                  cols[0] if cols else None)
    idx = cols.index(latest) if latest in cols else 0
    prev = cols[idx + 1] if idx + 1 < len(cols) else None

    return {
        "sym": sym,
        "income": income,
        "balance": fetch_balance(sym),
        "distrib": fetch_distributions(sym),
        "ratios": fetch_ratios(sym),
        "price": fetch_price(sym),
        "sector": fetch_sector(sym),
        "years": cols,
        "latest": latest,
        "prev": prev,
        "is_bank": is_bank(income),
    }
