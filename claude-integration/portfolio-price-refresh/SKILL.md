---
name: portfolio-price-refresh
description: Optional — refreshes cached price data intraday if you run this dashboard via Claude Code scheduled tasks. Not required to use the dashboard.
---

This is an OPTIONAL scheduled task template for Claude Code users. If you don't
use Claude Code, ignore this folder entirely — the dashboard refreshes prices
on its own every time you load the page (subject to its internal cache TTL in
app.py).

If you do use Claude Code and want a scheduled nudge (e.g. to pre-warm the
cache before market open), you can register a scheduled task pointing at this
skill. Fill in `<YOUR_PROJECT_PATH>` below with the absolute path to your
clone of this repo.

## What to do

1. Run:
   ```bash
   cd <YOUR_PROJECT_PATH> && python3 -c "import portfolio_lib; import json; print(json.dumps(portfolio_lib.build_snapshot(), default=str)[:200])"
   ```
   This re-fetches live prices via yfinance and warms `portfolio_lib`'s
   internal history cache, so the next dashboard load is fast.
2. If it fails (yfinance rate-limited, network issue), that's fine — just
   note it; the dashboard itself falls back to stale cached data rather than
   crashing.

## What NOT to do

- Don't edit `portfolio_lib.py`, `app.py`, or `smart_money.py` as part of this
  routine task — if you spot an actual bug, fix it as a separate deliberate
  change, not inside an automated refresh.
- Don't touch `data/positions.json` or `config.yaml` here — this task is
  read-only against your data.

## Notes for adapting this template

- This is a stripped-down placeholder. The original private version this was
  based on also regenerated a static published snapshot (an "artifact") twice
  a day and posted a one-line summary notification. That's out of scope for
  the generic template — add it back yourself if you build an equivalent
  static export feature.
