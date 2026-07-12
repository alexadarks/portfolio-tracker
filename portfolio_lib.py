"""
Shared data layer for Portfolio Tracker.

Reads a self-contained positions store (data/positions.json, one JSON array —
no external CLI or private repo required) plus config.yaml (brokers, local
currency, watchlist, famous investors), fetches live prices via yfinance, and
produces a single "snapshot" dict consumed by app.py (the Flask dashboard).

Adding/removing a position is either editing data/positions.json by hand,
using add_position.py, or (if you use Claude Code) asking Claude to parse a
broker screenshot into the same schema — no code changes required either way.
"""
import datetime as dt
import json
import threading
import time
from pathlib import Path

import requests
import yaml
import yfinance as yf

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
CONFIG_EXAMPLE_PATH = BASE_DIR / "config.example.yaml"
POSITIONS_PATH = BASE_DIR / "data" / "positions.json"
POSITIONS_EXAMPLE_PATH = BASE_DIR / "data" / "positions.example.json"

# FX tickers on Yahoo Finance come in two flavors:
#   - "local base" majors like CLP=X, MXN=X, JPY=X directly give local-per-USD
#   - "USD base" majors (EUR, GBP, AUD, NZD) are quoted as {CODE}USD=X, i.e.
#     USD-per-CODE, so we need to invert (1 / price) to get local-per-USD
USD_BASE_CURRENCIES = {"EUR", "GBP", "AUD", "NZD"}


def _config_path_in_use():
    """Real config if present, else fall back to the example (so the app never
    crashes for a first-time user who hasn't copied it yet)."""
    return CONFIG_PATH if CONFIG_PATH.exists() else CONFIG_EXAMPLE_PATH


def _positions_path_in_use():
    return POSITIONS_PATH if POSITIONS_PATH.exists() else POSITIONS_EXAMPLE_PATH


def using_example_data():
    return not CONFIG_PATH.exists() or not POSITIONS_PATH.exists()


def load_config():
    path = _config_path_in_use()
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def load_positions():
    """Load the flat positions list. Only ACTIVE positions (quantity > 0) are
    returned; a position with quantity 0 is treated as fully closed."""
    path = _positions_path_in_use()
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    positions = []
    for p in raw:
        qty = p.get("quantity") or 0
        if qty <= 0:
            continue
        positions.append({
            "ticker": p["ticker"].upper(),
            "name": p.get("name") or p["ticker"].upper(),
            "broker": p.get("broker") or "Unassigned",
            "shares": qty,
            "cost_basis_price": p.get("cost_price"),
            "entry_date": (p.get("entry_date") or "")[:10] or None,
            "realized_usd": p.get("realized_usd", 0.0) or 0.0,
            "dividends_usd": p.get("dividends_usd", 0.0) or 0.0,
        })
    return positions


def save_positions(positions):
    """Write back the full raw positions list (used by add_position.py)."""
    POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2, ensure_ascii=False)


def load_raw_positions_file():
    path = _positions_path_in_use()
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def broker_for_ticker(ticker, positions):
    for p in positions:
        if p["ticker"] == ticker:
            return p["broker"]
    return "Unassigned"


def fetch_prices_batch(tickers, histories=None):
    """
    Fetch current price + previous close for many tickers in parallel (fast path).
    If `histories` (from fetch_daily_history) is provided, previous close comes
    from it — skipping a whole redundant yf.download of 5d daily bars.
    """
    results = {tk: None for tk in tickers}
    if not tickers:
        return results

    try:
        intraday = yf.download(
            tickers=tickers, period="1d", interval="1m",
            group_by="ticker", threads=True, progress=False, auto_adjust=False,
        )
    except Exception:
        intraday = None

    daily = None
    if histories is None:
        try:
            daily = yf.download(
                tickers=tickers, period="5d", interval="1d",
                group_by="ticker", threads=True, progress=False, auto_adjust=False,
            )
        except Exception:
            daily = None

    for tk in tickers:
        price = None
        as_of = None
        prev_close = None
        try:
            if intraday is not None and not intraday.empty:
                series = intraday[tk] if len(tickers) > 1 else intraday
                series = series.dropna(subset=["Close"])
                if not series.empty:
                    price = float(series["Close"].iloc[-1])
                    as_of = str(series.index[-1])
        except Exception:
            pass
        if histories is not None:
            hseries = histories.get(tk)
            if hseries is not None and len(hseries):
                last_row_date = hseries.index[-1].strftime("%Y-%m-%d")
                price_date = as_of[:10] if as_of else None
                if price_date and price_date > last_row_date:
                    prev_close = float(hseries.iloc[-1])
                elif len(hseries) >= 2:
                    prev_close = float(hseries.iloc[-2])
                else:
                    prev_close = float(hseries.iloc[-1])
                if price is None:
                    price = float(hseries.iloc[-1])
                    as_of = str(hseries.index[-1])
        else:
            try:
                if daily is not None and not daily.empty:
                    dseries = daily[tk] if len(tickers) > 1 else daily
                    dseries = dseries.dropna(subset=["Close"])
                    if len(dseries) >= 1:
                        prev_close = float(dseries["Close"].iloc[-2]) if len(dseries) >= 2 else float(dseries["Close"].iloc[-1])
                        if price is None:
                            price = float(dseries["Close"].iloc[-1])
                            as_of = str(dseries.index[-1])
            except Exception:
                pass

        if price is None:
            results[tk] = {"error": "no data"}
        else:
            results[tk] = {"price": price, "as_of": as_of, "prev_close": prev_close}

    return results


_history_cache_lock = threading.Lock()
_history_cache = {"key": None, "data": None, "fetched_at": 0.0}
HISTORY_CACHE_TTL = 1800  # daily closes only change once a day; 30 min is plenty


def fetch_daily_history(tickers, period="6mo"):
    """One batched download of daily closes; returns {ticker: pandas Series indexed by date}."""
    out = {}
    if not tickers:
        return out

    key = (tuple(sorted(tickers)), period)
    now = time.time()
    with _history_cache_lock:
        if _history_cache["key"] == key and (now - _history_cache["fetched_at"]) < HISTORY_CACHE_TTL:
            return _history_cache["data"]

    try:
        hist = yf.download(
            tickers=tickers, period=period, interval="1d",
            group_by="ticker", threads=True, progress=False, auto_adjust=False,
        )
    except Exception:
        return out
    if hist is None or hist.empty:
        return out
    for tk in tickers:
        try:
            series = hist[tk]["Close"] if len(tickers) > 1 else hist["Close"]
            series = series.dropna()
            if not series.empty:
                out[tk] = series
        except Exception:
            pass

    with _history_cache_lock:
        _history_cache.update({"key": key, "data": out, "fetched_at": now})
    return out


def _ref_close(series, cutoff_date):
    """Last close on or before cutoff_date (a 'YYYY-MM-DD' string). None if series starts later."""
    try:
        eligible = series[series.index.strftime("%Y-%m-%d") <= cutoff_date]
        if len(eligible):
            return float(eligible.iloc[-1])
    except Exception:
        pass
    return None


def compute_period_pnl(position, series, price, days_back):
    """
    Money gained/lost by THIS position over the last `days_back` calendar days.
    Entry-aware: if the position was bought inside the window, the reference is
    the actual purchase price, so a deposit never looks like a gain.
    """
    if price is None:
        return None
    cutoff = (dt.date.today() - dt.timedelta(days=days_back)).isoformat()
    entry_date = position.get("entry_date")
    cost_price = position.get("cost_basis_price") or position.get("cost_price")
    if entry_date and entry_date > cutoff:
        ref = cost_price
    else:
        ref = _ref_close(series, cutoff) if series is not None else None
        if ref is None:
            ref = cost_price
    if ref is None:
        return None
    return (price - ref) * position["shares"]


def build_pnl_series(positions, histories, live_prices):
    """
    Daily cumulative unrealized P&L of the current holdings, from the earliest
    entry date to today.
    """
    all_dates = set()
    for tk, series in histories.items():
        all_dates.update(series.index.strftime("%Y-%m-%d"))
    entry_dates = [p["entry_date"] for p in positions if p["entry_date"]]
    if not all_dates or not entry_dates:
        return []
    start = min(entry_dates)
    dates = sorted(d for d in all_dates if d >= start)

    closes = {tk: dict(zip(s.index.strftime("%Y-%m-%d"), s.values)) for tk, s in histories.items()}
    filled = {}
    for tk, cmap in closes.items():
        carry = None
        fmap = {}
        for d in dates:
            if d in cmap:
                carry = float(cmap[d])
            fmap[d] = carry
        filled[tk] = fmap

    out = []
    for d in dates:
        total = 0.0
        for p in positions:
            tk = p["ticker"]
            if not p["entry_date"] or p["entry_date"] > d or not p["cost_basis_price"]:
                continue
            close = filled.get(tk, {}).get(d)
            if close is None:
                continue
            total += (close - p["cost_basis_price"]) * p["shares"]
        out.append({"date": d, "pnl": round(total, 2)})

    if out:
        total = 0.0
        complete = True
        for p in positions:
            lp = live_prices.get(p["ticker"]) or {}
            if lp.get("price") is None or not p["cost_basis_price"]:
                complete = False
                break
            total += (lp["price"] - p["cost_basis_price"]) * p["shares"]
        if complete:
            today = dt.date.today().isoformat()
            if out[-1]["date"] == today:
                out[-1]["pnl"] = round(total, 2)
            else:
                out.append({"date": today, "pnl": round(total, 2)})
    return out


def search_tickers(query, limit=8):
    """Look up ticker symbols by name/symbol fragment via Yahoo's public search endpoint."""
    query = (query or "").strip()
    if not query:
        return []
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": limit, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    out = []
    for q in data.get("quotes", []):
        if q.get("quoteType") not in ("EQUITY", "ETF"):
            continue
        symbol = q.get("symbol")
        if not symbol:
            continue
        out.append({
            "ticker": symbol,
            "name": q.get("shortname") or q.get("longname") or symbol,
            "type": q.get("quoteType"),
        })
    return out


def _held_tickers(positions):
    return {p["ticker"] for p in positions}


def add_to_watchlist(ticker, name):
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"ok": False, "error": "Missing ticker."}
    config = load_config()
    positions = load_positions()
    if ticker in _held_tickers(positions):
        return {"ok": False, "error": f"You already hold {ticker}."}
    watchlist = config.setdefault("watchlist", [])
    if any(w["ticker"] == ticker for w in watchlist):
        return {"ok": False, "error": f"{ticker} is already on your watchlist."}
    watchlist.append({
        "ticker": ticker,
        "name": name or ticker,
        "reason": "Added manually from the ticker search.",
        "related": [],
    })
    save_config(config)
    return {"ok": True, "watchlist": watchlist}


def remove_from_watchlist(ticker):
    ticker = (ticker or "").strip().upper()
    config = load_config()
    watchlist = config.get("watchlist", [])
    new_list = [w for w in watchlist if w["ticker"] != ticker]
    if len(new_list) == len(watchlist):
        return {"ok": False, "error": f"{ticker} wasn't on the watchlist."}
    config["watchlist"] = new_list
    save_config(config)
    return {"ok": True, "watchlist": new_list}


def _fx_ticker_for(code):
    code = (code or "").upper()
    if code in USD_BASE_CURRENCIES:
        return f"{code}USD=X", True  # (ticker, needs_invert)
    return f"{code}=X", False


def fetch_fx_usd_local(local_currency):
    """
    Local-currency-per-USD rate, or None on any failure (the dashboard falls
    back to USD-only display, never crashes).
    """
    code = (local_currency or "").upper()
    if not code or code == "USD":
        return None
    ticker, needs_invert = _fx_ticker_for(code)
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1d")
        if hist.empty:
            return None
        rate = float(hist["Close"].iloc[-1])
        return (1.0 / rate) if needs_invert else rate
    except Exception:
        return None


def _consensus_from_recs(ticker_obj):
    """Analyst consensus from the current-month recommendation counts."""
    try:
        recs = ticker_obj.recommendations
        if recs is None or recs.empty:
            return None
        row = recs.iloc[0]
        counts = {
            "strong_buy": int(row.get("strongBuy", 0)),
            "buy": int(row.get("buy", 0)),
            "hold": int(row.get("hold", 0)),
            "sell": int(row.get("sell", 0)) + int(row.get("strongSell", 0)),
        }
        total = sum(counts.values())
        if total == 0:
            return None
        buys = counts["strong_buy"] + counts["buy"]
        if buys / total >= 0.75:
            label = "Strong buy"
        elif buys / total >= 0.5:
            label = "Buy"
        elif counts["sell"] / total >= 0.4:
            label = "Sell"
        else:
            label = "Hold"
        return {"label": label, "counts": counts, "total": total}
    except Exception:
        return None


def _normalize_sector_key(name):
    if not name:
        return None
    return name.strip().lower().replace(" ", "_").replace("&", "and")


def _fetch_ticker_composition(tk, ticker_obj):
    out = {"kind": "unknown", "weights": {}, "top_holdings": [], "dominant": None}
    info = {}
    for attempt in range(3):
        try:
            info = ticker_obj.info or {}
            if info:
                break
        except Exception:
            pass
        time.sleep(0.6 * (attempt + 1))
    qtype = info.get("quoteType")
    if qtype == "ETF":
        out["kind"] = "etf"
        for attempt in range(3):
            try:
                fd = ticker_obj.funds_data
                raw = fd.sector_weightings or {}
                weights = {k: float(v) for k, v in raw.items() if v}
                th = fd.top_holdings
                if weights or (th is not None and not th.empty):
                    out["weights"] = weights
                    if weights:
                        out["dominant"] = max(weights, key=weights.get)
                    if th is not None and not th.empty:
                        for sym, row in th.head(5).iterrows():
                            out["top_holdings"].append({
                                "symbol": sym,
                                "name": row.get("Name"),
                                "pct": float(row.get("Holding Percent", 0)),
                            })
                    break
            except Exception:
                pass
            time.sleep(0.6 * (attempt + 1))
    else:
        sector = info.get("sector")
        key = _normalize_sector_key(sector)
        if key:
            out["kind"] = "equity"
            out["weights"] = {key: 1.0}
            out["dominant"] = key
    return out


def _fetch_ticker_insight(tk):
    out = {"ticker": tk, "news": [], "consensus": None, "target_mean": None,
           "composition": {"kind": "unknown", "weights": {}, "top_holdings": [], "dominant": None}}
    try:
        t = yf.Ticker(tk)
    except Exception:
        return out
    try:
        for item in (t.news or [])[:5]:
            c = item.get("content") or item
            url = ((c.get("canonicalUrl") or {}).get("url")
                   or (c.get("clickThroughUrl") or {}).get("url"))
            title = c.get("title")
            if not title or not url:
                continue
            out["news"].append({
                "id": c.get("id") or url,
                "title": title,
                "summary": (c.get("summary") or "")[:280],
                "url": url,
                "provider": (c.get("provider") or {}).get("displayName", ""),
                "pub_date": c.get("pubDate") or c.get("displayTime"),
                "ticker": tk,
            })
    except Exception:
        pass
    try:
        targets = t.analyst_price_targets
        if targets and targets.get("mean"):
            out["target_mean"] = float(targets["mean"])
    except Exception:
        pass
    out["consensus"] = _consensus_from_recs(t)
    out["composition"] = _fetch_ticker_composition(tk, t)
    return out


def build_insights():
    """Slower, secondary payload: live news + analyst consensus for held tickers,
    plus the same (with prices) for the config watchlist. Cache this on a
    longer TTL than the price snapshot in app.py."""
    from concurrent.futures import ThreadPoolExecutor

    config = load_config()
    positions = load_positions()
    held = [p["ticker"] for p in positions]
    watchlist = config.get("watchlist", [])
    watch_tickers = [w["ticker"] for w in watchlist]
    all_tickers = held + [tk for tk in watch_tickers if tk not in held]

    with ThreadPoolExecutor(max_workers=4) as pool:
        insights = list(pool.map(_fetch_ticker_insight, all_tickers))
    by_ticker = {i["ticker"]: i for i in insights}

    watch_prices = fetch_prices_batch(watch_tickers)

    held_set = set(held)
    holdings_view = {}
    for tk in held:
        ins = by_ticker.get(tk, {})
        comp = ins.get("composition") or {}
        overlaps = [h["symbol"] for h in comp.get("top_holdings", []) if h["symbol"] in held_set and h["symbol"] != tk]
        holdings_view[tk] = {
            "consensus": ins.get("consensus"),
            "target_mean": ins.get("target_mean"),
            "composition": comp,
            "overlaps": overlaps,
        }

    watch_view = []
    for w in watchlist:
        tk = w["ticker"]
        ins = by_ticker.get(tk, {})
        p = watch_prices.get(tk) or {}
        price = p.get("price")
        prev = p.get("prev_close")
        upside = None
        if price and ins.get("target_mean"):
            upside = (ins["target_mean"] - price) / price * 100
        watch_view.append({
            "ticker": tk,
            "name": w.get("name", tk),
            "reason": w.get("reason", ""),
            "related": w.get("related", []),
            "price": price,
            "day_pct": ((price - prev) / prev * 100) if (price and prev) else None,
            "consensus": ins.get("consensus"),
            "target_mean": ins.get("target_mean"),
            "upside_pct": upside,
            "composition": ins.get("composition"),
        })

    news, seen = [], set()
    for tk in all_tickers:
        for n in by_ticker.get(tk, {}).get("news", []):
            if n["id"] in seen:
                continue
            seen.add(n["id"])
            n["owned"] = tk in held
            news.append(n)
    news.sort(key=lambda n: n.get("pub_date") or "", reverse=True)

    return {
        "generated_at": time.time(),
        "holdings": holdings_view,
        "watchlist": watch_view,
        "news": news[:24],
    }


def build_snapshot():
    config = load_config()
    positions = load_positions()
    tickers = [p["ticker"] for p in positions]
    local_currency = config.get("local_currency", "USD")

    histories = fetch_daily_history(tickers)
    prices = fetch_prices_batch(tickers, histories=histories)
    fx = fetch_fx_usd_local(local_currency)

    position_views = []
    by_broker = {}
    grand_invested = 0.0
    grand_unrealized = 0.0
    grand_today_usd = 0.0
    grand_week_usd = 0.0
    grand_month_usd = 0.0
    grand_realized = 0.0
    grand_dividends = 0.0

    for p in positions:
        tk = p["ticker"]
        pr = prices.get(tk) or {}
        price = pr.get("price")
        broker = p["broker"]
        shares = p["shares"]
        cost_price = p["cost_basis_price"]
        cost_basis = shares * cost_price if cost_price else None
        realized_usd = p["realized_usd"]
        dividends_usd = p["dividends_usd"]

        value = price * shares if price is not None else None
        unrealized = (value - cost_basis) if (value is not None and cost_basis is not None) else None
        unrealized_pct = (unrealized / cost_basis * 100) if (unrealized is not None and cost_basis) else None

        day_change_pct = None
        day_change_usd = None
        if price is not None and pr.get("prev_close"):
            day_change_pct = (price - pr["prev_close"]) / pr["prev_close"] * 100
            day_change_usd = (price - pr["prev_close"]) * shares

        series = histories.get(tk)
        week_change_usd = compute_period_pnl(p, series, price, 7)
        month_change_usd = compute_period_pnl(p, series, price, 30)
        sparkline = [round(float(v), 4) for v in series.tail(30).tolist()] if series is not None else []
        if price is not None and (not sparkline or sparkline[-1] != round(price, 4)):
            sparkline.append(round(price, 4))

        pos_view = {
            "ticker": tk,
            "name": p["name"],
            "broker": broker,
            "entry_date": p["entry_date"],
            "shares": shares,
            "cost_price": cost_price,
            "price": price,
            "price_error": pr.get("error"),
            "as_of": pr.get("as_of"),
            "value_usd": value,
            "unrealized_usd": unrealized,
            "unrealized_pct": unrealized_pct,
            "day_change_pct": day_change_pct,
            "day_change_usd": day_change_usd,
            "week_change_usd": week_change_usd,
            "month_change_usd": month_change_usd,
            "sparkline": sparkline,
            "realized_usd": realized_usd,
            "dividends_usd": dividends_usd,
        }
        position_views.append(pos_view)

        by_broker.setdefault(broker, {
            "invested_usd": 0.0, "unrealized_usd": 0.0, "cash_usd": 0.0,
            "today_usd": 0.0, "realized_usd": 0.0, "dividends_usd": 0.0,
        })
        if value is not None:
            by_broker[broker]["invested_usd"] += value
            grand_invested += value
        if unrealized is not None:
            by_broker[broker]["unrealized_usd"] += unrealized
            grand_unrealized += unrealized
        if day_change_usd is not None:
            by_broker[broker]["today_usd"] += day_change_usd
            grand_today_usd += day_change_usd
        if week_change_usd is not None:
            grand_week_usd += week_change_usd
        if month_change_usd is not None:
            grand_month_usd += month_change_usd
        by_broker[broker]["realized_usd"] += realized_usd
        by_broker[broker]["dividends_usd"] += dividends_usd
        grand_realized += realized_usd
        grand_dividends += dividends_usd

    for broker, info in config.get("cash_balances", {}).items():
        by_broker.setdefault(broker, {
            "invested_usd": 0.0, "unrealized_usd": 0.0, "cash_usd": 0.0,
            "today_usd": 0.0, "realized_usd": 0.0, "dividends_usd": 0.0,
        })
        by_broker[broker]["cash_usd"] = info.get("usd", 0.0)

    grand_cash = sum(b.get("usd", 0.0) for b in config.get("cash_balances", {}).values())
    grand_total = grand_invested + grand_cash

    position_views.sort(key=lambda p: (p["value_usd"] is None, -(p["value_usd"] or 0)))

    pnl_series = build_pnl_series(positions, histories, prices)

    return {
        "generated_at": time.time(),
        "local_currency": local_currency,
        "fx_usd_local": fx,
        "using_example_data": using_example_data(),
        "positions": position_views,
        "by_broker": by_broker,
        "brokers_config": config.get("brokers", {}),
        "pnl_series": pnl_series,
        "totals": {
            "invested_usd": grand_invested,
            "unrealized_usd": grand_unrealized,
            "cash_usd": grand_cash,
            "total_usd": grand_total,
            "today_usd": grand_today_usd,
            "week_usd": grand_week_usd,
            "month_usd": grand_month_usd,
            "realized_usd": grand_realized,
            "dividends_usd": grand_dividends,
            "net_result_usd": grand_unrealized + grand_realized,
        },
        "config": config,
    }
