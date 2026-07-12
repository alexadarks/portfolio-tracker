# Portfolio Tracker

A self-hosted, local-first dashboard for your own brokerage positions:
live prices, unrealized P&L, day/week/month moves, sector/broker
composition, a watchlist, and a "smart money" panel (SEC EDGAR insider
trades + curated 13F filings for well-known investors) — all from free
public data sources.

No account, no cloud, no telemetry. It runs on `localhost` and reads a JSON
file you control.

## Quickstart

```bash
git clone https://github.com/alexadarks/portfolio-tracker.git
cd portfolio-tracker
pip install -r requirements.txt

cp config.example.yaml config.yaml
# edit config.yaml: your local currency, your brokers (or none), watchlist

# Add each holding you own — pick ONE of these:
python3 add_position.py                       # interactive prompts, no AI needed
# OR, if you use Claude Code: ask Claude to read your broker screenshots and
# write them into data/positions.json following the schema in
# data/positions.example.json (copy that file to data/positions.json first)

python3 app.py
# open http://localhost:5057
```

That's it — the dashboard fetches live prices via `yfinance` on each load.

## What this is

- `portfolio_lib.py` — reads `data/positions.json` + `config.yaml`, fetches
  live/historical prices via yfinance, computes P&L, builds the JSON snapshot
  the dashboard renders.
- `smart_money.py` — free SEC EDGAR lookups: Form 4 insider buy/sell for your
  own holdings, and 13F quarterly holdings for the investors you list in
  `config.yaml`'s `famous_investors`.
- `app.py` — a small Flask app serving the dashboard and a few JSON
  endpoints (`/api/portfolio`, `/api/insights`, `/api/smart-money`,
  `/api/ticker-search`, `/api/watchlist`).
- `templates/head.html` + `templates/body.html` — the UI (editorial
  cream/serif design, light + dark themes, no build step — plain HTML/CSS/JS).
- `add_position.py` — interactive CLI to add/update a position by hand. This
  is the primary, zero-AI onboarding path.
- `claude-integration/` — **optional** example templates for wiring this up
  to Claude Code scheduled tasks (see below). Skip this folder entirely if
  you don't use Claude Code.

## Configuration

Copy `config.example.yaml` to `config.yaml` (gitignored — your real setup
never gets committed) and edit:

- `local_currency` — an ISO code (`CLP`, `MXN`, `EUR`, `GBP`, ...) for a
  secondary currency conversion next to USD figures. Set to `USD` to disable
  it. If the FX ticker can't be resolved on Yahoo Finance, the dashboard
  quietly falls back to USD-only — it never crashes on this.
- `brokers` — define as many as you use, each with a `commission_model` of
  `per_trade`, `aum_annual`, or `none`. Zero brokers is also fine.
- `cash_balances`, `watchlist`, `famous_investors` — all optional, all
  editable at any time.

Positions live in `data/positions.json` (gitignored), a flat JSON array — see
`data/positions.example.json` for the shape:

```json
{
  "ticker": "VOO",
  "name": "Vanguard S&P 500 ETF",
  "broker": "Example Broker A",
  "quantity": 2.5,
  "cost_price": 480.0,
  "entry_date": "2026-01-15",
  "realized_usd": 0.0,
  "dividends_usd": 3.2
}
```

## Optional: Claude Code integration

`claude-integration/portfolio-daily-suggestion/` and
`claude-integration/portfolio-price-refresh/` are example scheduled-task
templates for Claude Code users — one shows how you might have Claude
synthesize an expert-style daily read (analysts, insiders, famous-investor
filings, news) on top of the same data this dashboard uses; the other is a
lightweight price-cache warm-up. **Neither is required.** The dashboard is
100% functional without Claude Code, without any LLM, and without internet
access to anything other than Yahoo Finance and SEC EDGAR.

### Setup prompt

If you have [Claude Code](https://claude.com/claude-code) installed, the
easiest way to turn this on is to paste the prompt below into a Claude Code
session opened at the root of your clone. It reads the template, fills in
your real path, wires the small amount of glue code the template
deliberately leaves out (an `/api/expert-analysis` endpoint + a dashboard
card), and schedules the daily run — adjust the cron time/timezone to taste.

```
I want to enable the optional "expert daily read" feature described in
claude-integration/portfolio-daily-suggestion/SKILL.md in this repo
(portfolio-tracker). Please:

1. Read that SKILL.md and claude-integration/portfolio-price-refresh/SKILL.md.
2. Replace every <YOUR_PROJECT_PATH> placeholder with the absolute path to
   this repo on my machine.
3. Add a small /api/expert-analysis endpoint to app.py that reads
   data/expert_analysis.json (tolerating a missing/corrupt file) and a
   matching card in templates/body.html to display market_view + the
   per-ticker action/conviction/reasoning, styled consistently with the
   rest of the dashboard.
4. Set up a recurring scheduled task (ask me what time/timezone and how
   often — I'd like once a day before market open, plus optionally a
   lightweight price-only refresh at midday) that runs the daily-suggestion
   routine and writes data/expert_analysis.json using an atomic write
   (write to .tmp, then rename).
5. Never place real buy/sell orders or fabricate data — this feature is
   informational only, exactly as the SKILL.md says.

Ask me before scheduling anything, and show me the diff before writing to
app.py or templates/body.html.
```

## What this does NOT do

- No real brokerage integration (no OAuth/API to Schwab, Fidelity, Fintual,
  etc.) — you enter your own positions.
- No financial advice, no trade execution, no automated buying/selling.
- Data comes only from free public sources: Yahoo Finance (via `yfinance`,
  prices/news/analyst consensus) and SEC EDGAR (insider Form 4, 13F filings).
  Both can be delayed, incomplete, or rate-limited — treat this as a
  convenience view, not a source of truth for tax or accounting purposes.
- No guarantee of uptime, accuracy, or fitness for any particular purpose —
  see LICENSE.

## License

MIT — see [LICENSE](LICENSE).
