#!/usr/bin/env python3
"""
Manual position entry — the primary onboarding path. No AI/Claude required.

Prompts for ticker/broker/quantity/cost price/date and appends to
data/positions.json. Run it once per holding you want to track:

    python3 add_position.py

You can also edit data/positions.json by hand at any time — it's a plain
JSON array; see data/positions.example.json for the shape.
"""
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
POSITIONS_PATH = BASE_DIR / "data" / "positions.json"


def load_existing():
    if not POSITIONS_PATH.exists():
        return []
    with open(POSITIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save(positions):
    POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(POSITIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2, ensure_ascii=False)


def ask(prompt, required=True, cast=str, default=None):
    while True:
        raw = input(prompt).strip()
        if not raw:
            if default is not None:
                return default
            if not required:
                return None
            print("This field is required.")
            continue
        try:
            return cast(raw)
        except ValueError:
            print("Please enter a valid value.")


def main():
    print("Portfolio Tracker — add a position")
    print("(Ctrl+C to cancel at any time)\n")

    positions = load_existing()

    try:
        ticker = ask("Ticker (e.g. AAPL): ").upper()
        name = ask(f"Display name [{ticker}]: ", required=False) or ticker
        broker = ask("Broker name (must match a key in config.yaml, or any label): ")
        quantity = ask("Quantity (shares, can be fractional): ", cast=float)
        cost_price = ask("Cost price per share, in USD: ", cast=float)
        entry_date = ask("Entry date (YYYY-MM-DD): ")
        realized_usd = ask("Realized gains so far, in USD [0]: ", required=False, cast=float, default=0.0)
        dividends_usd = ask("Dividends received so far, in USD [0]: ", required=False, cast=float, default=0.0)
    except KeyboardInterrupt:
        print("\nCancelled — nothing was saved.")
        sys.exit(1)

    entry = {
        "ticker": ticker,
        "name": name,
        "broker": broker,
        "quantity": quantity,
        "cost_price": cost_price,
        "entry_date": entry_date,
        "realized_usd": realized_usd,
        "dividends_usd": dividends_usd,
    }

    # If this ticker+broker combo already exists, offer to update quantity in
    # place rather than creating a duplicate row.
    existing = next((p for p in positions if p["ticker"] == ticker and p.get("broker") == broker), None)
    if existing:
        print(f"\n{ticker} at {broker} already exists (quantity {existing.get('quantity')}).")
        overwrite = ask("Overwrite it with these new values? [y/N]: ", required=False, default="n").lower()
        if overwrite == "y":
            positions[positions.index(existing)] = entry
        else:
            positions.append(entry)
    else:
        positions.append(entry)

    save(positions)
    print(f"\nSaved. {POSITIONS_PATH} now has {len(positions)} position(s).")
    print("Run `python3 app.py` and open http://localhost:5057 to see it.")


if __name__ == "__main__":
    main()
