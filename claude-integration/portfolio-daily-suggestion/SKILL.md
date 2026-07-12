---
name: portfolio-daily-suggestion
description: Optional — an example template for having Claude Code produce an expert-style daily read on your portfolio (analysts, insiders, famous-investor 13F, news). Not required to use the dashboard; purely illustrative.
---

**This entire folder is optional.** The dashboard (`app.py`) works completely
on its own with zero AI/LLM involvement — this is just an example of how you
*could* wire up a Claude Code scheduled task on top of it, if you want a
narrative "what should I think about today" read layered over the raw
numbers. Adapt or delete freely.

Everything here is informational only — never place real buy/sell orders
from this or any automated routine.

## Context this task would need (fill in your own paths)

- `<YOUR_PROJECT_PATH>/portfolio_lib.py` — reads `data/positions.json` +
  `config.yaml`, fetches live prices.
- `<YOUR_PROJECT_PATH>/smart_money.py` — public SEC EDGAR data:
  `build_smart_money(held_tickers, config)` returns insider Form 4 trades and
  13F holdings for the `famous_investors` you listed in `config.yaml`.
- `config.yaml` — your brokers, commission models, cash balances, watchlist,
  and famous_investors list.

## Example steps (adapt to taste)

1. **Snapshot the current numbers**:
   ```bash
   cd <YOUR_PROJECT_PATH> && python3 -c "import portfolio_lib, json; print(json.dumps(portfolio_lib.build_snapshot(), default=str))"
   ```
2. **Pull insider trades + 13F holdings** (takes ~1-2 min, several SEC EDGAR calls):
   ```bash
   cd <YOUR_PROJECT_PATH> && python3 -c "
   import json, portfolio_lib, smart_money
   config = portfolio_lib.load_config()
   positions = portfolio_lib.load_positions()
   held = [p['ticker'] for p in positions]
   print(json.dumps(smart_money.build_smart_money(held, config), default=str))
   "
   ```
3. **Pull analyst consensus + news** per ticker:
   ```bash
   cd <YOUR_PROJECT_PATH> && python3 -c "import portfolio_lib, json; print(json.dumps(portfolio_lib.build_insights(), default=str))"
   ```
4. For each active position, synthesize (with real judgment, not a mechanical
   score): analyst consensus + target price, any recent insider buy/sell
   (note: routine 10b5-1 planned sales are not a bearish signal — say so
   explicitly), whether a famous investor from your `famous_investors` list
   holds the same name, and any concrete recent news catalyst.
5. Decide, per position: an action label of your choosing (e.g. `add` /
   `hold` / `watch` / `trim`) and a short, concrete reasoning string citing
   the actual signals used.
6. If you want this surfaced in the live dashboard, write your own
   `expert_analysis.json` file and wire a small `/api/expert-analysis`
   endpoint into `app.py` that serves it — this is not implemented in the
   base template; the original private version this was adapted from had one,
   but it's out of scope here since it depends entirely on your own chosen
   output schema.

## What NOT to do

- Don't apply a mechanical point-scoring formula — reason like an analyst
  reading the signals together.
- Don't edit `portfolio_lib.py` / `smart_money.py` / `app.py` as a side effect
  of running this routine task.
- Never fabricate a data point (a "reported" broker balance, a news headline)
  — if you don't have a real source for something, omit it.
