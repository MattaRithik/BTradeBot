# College Terminal Checklist

What to do on the Bloomberg-terminal machine (and what honest output to
expect). Everything offline-safe fails loudly, never silently.

## One-time setup

1. `git pull` and `make setup`
2. Install BLPAPI from Bloomberg's own index:
   `pip install blpapi --index-url https://blpapi.bloomberg.com/repository/releases/python`
3. Copy `.env.example` to `.env`; set `KIMI_API_KEY` if you have one
   (without it the platform runs on MockModelProvider — fine for the demo).
4. For paper trading: start TWS or IB Gateway, log into the PAPER account
   (`DU...`), enable API on port 7497 (TWS) or 4002 (Gateway), and set
   `IBKR_ACCOUNT=DU...` in `.env`.

## Verification sequence

| Command | Expect |
|---|---|
| `quantctl doctor` | all PASS; optional adapters line shows `blpapi=yes` |
| `quantctl config check` | all configs OK; scoring weights sum to 1.0 |
| `quantctl bloomberg doctor` | BLPAPI capabilities PASS / FAIL / NOT ENTITLED (news may be NOT ENTITLED — that's an honest answer, not a bug) |
| `quantctl bloomberg sample` | normalized bars saved under `data/normalized/` |
| `quantctl paper doctor` | trading mode + account prefix PASS; kill switch clear |
| `quantctl paper dry-run` | computes a sample order, submits nothing |
| `quantctl demo` | full pipeline on synthetic data; audit log written |
| `quantctl dashboard` | Streamlit UI with the NO LIVE TRADING banner |

## Rules that protect you

- Never set `TRADING_MODE` to anything but `paper` — startup refuses otherwise.
- Keep `DRY_RUN=true` until you deliberately want paper orders submitted.
- `touch data/paper_trading/KILL_SWITCH` halts all new orders immediately.
- Raw Bloomberg exports are git-ignored; never commit terminal data.
