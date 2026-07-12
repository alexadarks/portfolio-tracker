"""
"Smart money" — public SEC EDGAR data: insider trades (Form 4) for your own
holdings, and quarterly 13F holdings for a short curated list of well-known
investors (see famous_investors in config.yaml). No paid API — SEC EDGAR is
free and official. Form 4 is close to real-time; 13F is quarterly with a
~45-day lag by law, not live.

SEC requires a descriptive User-Agent identifying the app; it rate-limits by
IP (fair-access policy, not a hard key), so requests use modest concurrency
plus backoff. Edit SEC_UA below to identify your own instance if you plan to
run this a lot — SEC asks for a real contact in the User-Agent string.
"""
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

SEC_UA = "PortfolioTracker/1.0 (contact: set-your-email-in-smart_money.py)"
HEADERS = {"User-Agent": SEC_UA}

# one Session = TLS connection reuse across the ~30-50 SEC calls per refresh
_session = requests.Session()
_session.headers.update(HEADERS)

TX_CODE_LABELS = {
    "P": "Open-market purchase",
    "S": "Open-market sale",
    "A": "Award/grant",
    "M": "Option exercise",
    "F": "Tax withholding",
    "G": "Gift",
    "C": "Conversion",
}

_ticker_cik_cache = {"map": None, "fetched_at": 0}


def _sec_get(url, timeout=20, retries=3):
    for attempt in range(retries):
        try:
            r = _session.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        time.sleep(0.6 * (attempt + 1))
    return None


def _ticker_to_cik_map():
    now = time.time()
    if _ticker_cik_cache["map"] and (now - _ticker_cik_cache["fetched_at"]) < 86400:
        return _ticker_cik_cache["map"]
    r = _sec_get("https://www.sec.gov/files/company_tickers.json")
    m = {}
    if r is not None:
        for row in r.json().values():
            m[row["ticker"]] = str(row["cik_str"]).zfill(10)
    _ticker_cik_cache["map"] = m
    _ticker_cik_cache["fetched_at"] = now
    return m


def _parse_form4(xml_bytes, ticker):
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None
    owner_name = root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName", default="")
    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    title = None
    is_officer = False
    is_director = False
    if rel is not None:
        title = rel.findtext("officerTitle")
        is_officer = (rel.findtext("isOfficer") or "").lower() == "true"
        is_director = (rel.findtext("isDirector") or "").lower() == "true"

    txs = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        code = tx.findtext(".//transactionCoding/transactionCode")
        if code not in ("P", "S"):
            # skip everything that isn't a real open-market buy/sell — conversions,
            # awards, tax withholding, gifts are administrative noise, not a
            # discretionary trading decision
            continue
        date = tx.findtext(".//transactionDate/value")
        shares_s = tx.findtext(".//transactionAmounts/transactionShares/value")
        price_s = tx.findtext(".//transactionAmounts/transactionPricePerShare/value")
        ad = tx.findtext(".//transactionAmounts/transactionAcquiredDisposedCode/value")
        try:
            shares = float(shares_s) if shares_s else None
            price = float(price_s) if price_s else None
        except ValueError:
            shares, price = None, None
        txs.append({
            "code": code,
            "code_label": TX_CODE_LABELS.get(code, code),
            "date": date,
            "shares": shares,
            "price": price,
            "value_usd": (shares * price) if (shares and price) else None,
            "acquired": ad == "A",
        })

    if not txs:
        return None  # derivative-only filing (option grant/vesting) — low signal

    return {
        "ticker": ticker,
        "owner": owner_name.strip(),
        "title": title or ("Director" if is_director else None),
        "is_officer": is_officer,
        "transactions": txs,
    }


def _fetch_insider_for_ticker(ticker, cik, max_filings=2):
    out = []
    r = _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if r is None:
        return out
    recent = r.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    idxs = [i for i, f in enumerate(forms) if f == "4"][:max_filings]
    cik_nodash = str(int(cik))
    for i in idxs:
        acc = recent["accessionNumber"][i]
        primary_doc = recent["primaryDocument"][i]
        filing_date = recent["filingDate"][i]
        acc_nodash = acc.replace("-", "")
        raw_name = primary_doc.split("/")[-1]
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{acc_nodash}/{raw_name}"
        dr = _sec_get(doc_url)
        if dr is None:
            continue
        parsed = _parse_form4(dr.content, ticker)
        if parsed:
            parsed["filing_date"] = filing_date
            parsed["accession"] = acc
            out.append(parsed)
    return out


def fetch_insider_trades(tickers, max_filings_per_ticker=2, max_workers=4):
    """Recent Form 4 (insider buy/sell) filings for your own held tickers."""
    cik_map = _ticker_to_cik_map()
    pairs = [(tk, cik_map[tk]) for tk in tickers if tk in cik_map]

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_insider_for_ticker, tk, cik, max_filings_per_ticker) for tk, cik in pairs]
        for f in futures:
            try:
                results.extend(f.result())
            except Exception:
                pass

    results.sort(key=lambda r: r.get("filing_date") or "", reverse=True)
    return results


def _normalize_issuer(name):
    name = re.sub(r"\b(CORP|CORPORATION|INC|CO|THE|LTD|LLC|CL A|CL B|CLASS A|CLASS B|COS?)\b\.?", "", name.upper())
    return re.sub(r"[^A-Z0-9 ]", "", name).strip()


def _match_ticker(issuer_name, ticker_names):
    """Best-effort match of a 13F issuer name (no ticker in the filing) to a ticker you already track."""
    norm_issuer = _normalize_issuer(issuer_name)
    if not norm_issuer:
        return None
    for tk, nm in ticker_names.items():
        norm_nm = _normalize_issuer(nm)
        first_word = norm_nm.split(" ")[0] if norm_nm else ""
        if first_word and len(first_word) > 2 and first_word in norm_issuer:
            return tk
    return None


def _fetch_13f_for_investor(investor, ticker_names, top_n=8):
    cik = investor["cik"]
    r = _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    if r is None:
        return None
    recent = r.json().get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    idxs = [i for i, f in enumerate(forms) if "13F" in f and f != "13F-NT"]
    if not idxs:
        return None
    i = idxs[0]
    acc = recent["accessionNumber"][i]
    filing_date = recent["filingDate"][i]
    cik_nodash = str(int(cik))
    acc_nodash = acc.replace("-", "")

    idx_r = _sec_get(f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{acc_nodash}/index.json")
    if idx_r is None:
        return None
    doc_names = [item["name"] for item in idx_r.json().get("directory", {}).get("item", [])]
    table_doc = next((n for n in doc_names if n.endswith(".xml") and "primary_doc" not in n.lower()), None)
    if not table_doc:
        return None

    table_r = _sec_get(f"https://www.sec.gov/Archives/edgar/data/{cik_nodash}/{acc_nodash}/{table_doc}")
    if table_r is None:
        return None
    try:
        root = ET.fromstring(table_r.content)
    except Exception:
        return None

    tag = root.tag
    ns_uri = tag[tag.find("{") + 1:tag.find("}")] if "{" in tag else ""
    ns = {"n": ns_uri} if ns_uri else {}
    path = ".//n:infoTable" if ns else ".//infoTable"

    by_issuer = {}
    for entry in root.findall(path, ns):
        def gt(tag_name):
            el = entry.find(f"n:{tag_name}" if ns else tag_name, ns)
            return el.text if el is not None else None
        name = gt("nameOfIssuer") or "?"
        value = gt("value")
        shares_el = entry.find(".//n:sshPrnamt" if ns else ".//sshPrnamt", ns)
        shares = shares_el.text if shares_el is not None else None
        try:
            value = float(value) if value else 0.0
            shares = float(shares) if shares else 0.0
        except ValueError:
            value, shares = 0.0, 0.0
        agg = by_issuer.setdefault(name, {"issuer": name, "value": 0.0, "shares": 0.0})
        agg["value"] += value
        agg["shares"] += shares

    holdings = sorted(by_issuer.values(), key=lambda h: -h["value"])[:top_n]
    for h in holdings:
        h["value_usd"] = h["value"]  # this XML schema reports value already in whole USD
        h["matched_ticker"] = _match_ticker(h["issuer"], ticker_names)

    return {
        "name": investor["name"],
        "fund": investor["fund"],
        "style": investor.get("style", ""),
        "filing_date": filing_date,
        "holdings": holdings,
    }


def fetch_famous_investor_holdings(investors, ticker_names, max_workers=4):
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_13f_for_investor, inv, ticker_names) for inv in investors]
        out = []
        for f in futures:
            try:
                r = f.result()
                if r:
                    out.append(r)
            except Exception:
                pass
    return out


def build_smart_money(held_tickers, config):
    """held_tickers: list of tickers you currently hold.
    config: the loaded config.yaml dict (uses ticker_names, watchlist, famous_investors)."""
    all_names = dict(config.get("ticker_names", {}))
    for w in config.get("watchlist", []):
        all_names.setdefault(w["ticker"], w.get("name", w["ticker"]))
    investors = config.get("famous_investors", [])
    insider_trades = fetch_insider_trades(held_tickers)
    famous = fetch_famous_investor_holdings(investors, all_names)
    return {
        "generated_at": time.time(),
        "insider_trades": insider_trades,
        "famous_investors": famous,
    }
