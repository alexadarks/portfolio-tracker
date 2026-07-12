import threading
import time
from pathlib import Path

from flask import Flask, Response, jsonify, request

import portfolio_lib
import smart_money

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__, static_folder=str(BASE_DIR))

_cache_lock = threading.Lock()
_cache = {"data": None, "fetched_at": 0}
CACHE_TTL_SECONDS = 45  # frontend polls every 60s

_insights_lock = threading.Lock()
_insights_cache = {"data": None, "fetched_at": 0}
INSIGHTS_TTL_SECONDS = 900  # news/analysts move slowly; don't hammer Yahoo

_smart_money_lock = threading.Lock()
_smart_money_cache = {"data": None, "fetched_at": 0}
SMART_MONEY_TTL_SECONDS = 24 * 3600  # Form 4 trickles in daily at most; 13F is quarterly


def _cached_endpoint(lock, cache, ttl, builder):
    """Serve fresh data within TTL; on builder failure, fall back to the last
    good payload (stale beats a 500 for a personal dashboard)."""
    with lock:
        now = time.time()
        if cache["data"] is not None and (now - cache["fetched_at"]) < ttl:
            return jsonify(cache["data"])
        try:
            data = builder()
        except Exception:
            if cache["data"] is not None:
                return jsonify(cache["data"])
            raise
        cache["data"] = data
        cache["fetched_at"] = now
        return jsonify(data)


@app.route("/api/portfolio")
def api_portfolio():
    return _cached_endpoint(_cache_lock, _cache, CACHE_TTL_SECONDS, portfolio_lib.build_snapshot)


@app.route("/api/insights")
def api_insights():
    return _cached_endpoint(_insights_lock, _insights_cache, INSIGHTS_TTL_SECONDS, portfolio_lib.build_insights)


def _build_smart_money():
    config = portfolio_lib.load_config()
    positions = portfolio_lib.load_positions()
    held = [p["ticker"] for p in positions]
    return smart_money.build_smart_money(held, config)


@app.route("/api/smart-money")
def api_smart_money():
    return _cached_endpoint(_smart_money_lock, _smart_money_cache, SMART_MONEY_TTL_SECONDS, _build_smart_money)


@app.route("/api/ticker-search")
def api_ticker_search():
    return jsonify(portfolio_lib.search_tickers(request.args.get("q", "")))


@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_add():
    data = request.get_json(force=True, silent=True) or {}
    result = portfolio_lib.add_to_watchlist(data.get("ticker", ""), data.get("name", ""))
    return jsonify(result), (200 if result["ok"] else 409)


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
def api_watchlist_remove(ticker):
    result = portfolio_lib.remove_from_watchlist(ticker)
    return jsonify(result), (200 if result["ok"] else 404)


@app.route("/")
def index():
    head = (BASE_DIR / "templates" / "head.html").read_text(encoding="utf-8")
    body = (BASE_DIR / "templates" / "body.html").read_text(encoding="utf-8")
    html = f'<!doctype html>\n<html lang="en">\n<head>\n{head}\n</head>\n<body>\n{body}\n</body>\n</html>'
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5057, debug=False)
