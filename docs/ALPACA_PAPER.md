# Cutover: mock broker → real Alpaca paper trading

The single biggest free execution-realism upgrade: Alpaca's **paper-trading
API** gives real-time fills, real spreads, partial fills, and order
rejections — instead of the mock's cached-close marks (± the `SLIPPAGE_BPS`
haircut). The code path already exists (`auto_trader/broker/alpaca_client.py`
builds the real REST client when `ALPACA_USE_MOCK=false`); the cutover is
configuration, one account, and one deliberate book decision.

> Paper only. The two live-trading gates in `auto_trader/credentials.py`
> (minimum paper duration + explicit `LIVE_TRADING_CONFIRMED`) stay
> untouched by this cutover — `TRADING_MODE=paper` throughout.

## Steps (yours — the keys never pass through anyone else)

1. **Create a free Alpaca account** at alpaca.markets and open the
   **Paper Trading** dashboard. Generate an API key pair (paper keys work
   only against `paper-api.alpaca.markets` — they cannot trade real money).
2. **Set the paper account's starting cash to $10,000.** Easiest path: the
   account switcher (top-left) → **New Paper Account** → name it, Set Funds
   `10000`, Save, then SELECT it — keys generate for whichever paper account
   is selected. (Alpaca allows up to 3 paper accounts; the default $100k one
   can sit unused.)
3. **Put the keys in `.env`** (gitignored; never commit):
   ```
   ALPACA_USE_MOCK=false
   ALPACA_API_KEY=<your paper key>
   ALPACA_SECRET_KEY=<your paper secret>
   ALPACA_BASE_URL=https://paper-api.alpaca.markets
   ```
   ⚠ **No trailing `/v2`** on the base URL — the dashboard displays the
   endpoint as `…markets/v2`, but the SDK appends `/v2` itself; copying the
   displayed value verbatim yields 404s on `/v2/v2/account`.
4. **Verify** before any cycle:
   ```bash
   ./track doctor && ./track status
   ```
   The client log line should read
   `Alpaca: https://paper-api.alpaca.markets | Status=ACTIVE | Cash=$…`
   (the mock's line says `Alpaca client: MOCK`), and status should show the
   fresh account's cash.

## The book decision (pick one)

- **Fresh start (recommended):** let the flagship begin a new $10k book at
  Alpaca on the next monthly window. The mock book's history stays in
  `store/portfolio.db` / snapshots as the "mock era"; the equity chart
  simply continues. Cleanest accounting.
- **Position migration:** manually re-buy the current 16 holdings in the
  Alpaca paper account. Fills won't match the mock's cost bases exactly
  (real spreads), so the reconciler will flag cost-basis drift vs the local
  ledger until the first rebalance washes it through. Only worth it if
  preserving the exact open book matters more than clean books.

## Scope + caveats

- **The fleet stays on the mock.** Alpaca allows one paper account per
  login; the 9 member books keep their env-isolated mock state (their job
  is relative comparison, where consistent marks are actually a feature).
- The daily monitor's snapshot/reconciler math is broker-agnostic — it
  reads positions and cash from whatever client `get_client()` returns.
- Orders submit as market orders in the existing MOO window; Alpaca paper
  fills them against the real (simulated) book at market open.
- If the keys are absent while `ALPACA_USE_MOCK=false`, `get_client()`
  raises with a clear message — the scheduled runs fail loudly rather than
  silently falling back to the mock.
