# College Terminal Checklist

Exact steps for the college Bloomberg-terminal Windows machine, in order.
Run everything in **PowerShell**. Everything fails loudly and honestly —
a FAIL or NOT ENTITLED is information, not a crash.

## 1. Clone and install (~5 min)

```powershell
git clone git@github.com:MattaRithik/BTradeBot.git
cd BTradeBot
py -3.13 -m venv .venv                    # or: python -m venv .venv
.\.venv\Scripts\Activate.ps1
# if activation is blocked by policy, run once, then retry the line above:
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"         # includes pytest, pytest-asyncio, openpyxl, ruff
```

## 2. Verify the base install (no external services needed)

```powershell
quantctl doctor                           # expect: all PASS
quantctl config check                     # expect: all OK, weights sum to 1.0
python -m pytest -q                       # expect: 270 passed
quantctl demo                             # full pipeline on SYNTHETIC data
```

If all four pass, the machine is correctly deployed. Everything below adds
real services on top.

## 3. Bloomberg (on the terminal machine only)

BLPAPI requires a running, logged-in Bloomberg Terminal (it talks to
localhost:8194). It is NOT on public PyPI — use Bloomberg's own index:

```powershell
python -m pip install blpapi --index-url https://blpapi.bloomberg.com/repository/releases/python
quantctl bloomberg doctor                 # PASS / FAIL / NOT ENTITLED per capability
quantctl bloomberg sample                 # saves normalized bars to data/normalized/
```

News may come back NOT ENTITLED — that is an honest terminal answer; the
CSV/XLSX export path (`data/raw/bloomberg_exports/`) remains first-class.

## 4. Kimi (optional — without it the MockModelProvider runs)

```powershell
Copy-Item .env.example .env
notepad .env                              # set KIMI_API_KEY=...
```

## 5. First REAL research run (Bloomberg + Kimi)

Needs sections 3 and 4 done. Export news from the terminal as CSV/XLSX
(columns like `security,date,headline,body`) into
`data\raw\bloomberg_exports\news\` — the `news\` subdirectory matters.
Price bars come from BLPAPI directly, or from CSV/XLSX exports dropped in
`data\raw\bloomberg_exports\` itself.

```powershell
quantctl research doctor                  # expect: all PASS (news-missing is only a WARN)
quantctl research run --as-of 2025-06-30  # explicit past cutoff; add --export-only off-terminal
```

PASS looks like: `research run complete` with bars/news/evidence counts,
selected sectors, a snapshot id, and backtest metrics (or an honest
"backtest skipped" warning if no post-as-of data exists yet). Artifacts
land in `data\snapshots\` (frozen prediction), `data\backtests\`,
`data\analysis\` (failure records), features in `data\features\`, and the
audit trail in `logs\`. A clear red error + exit code 1 is the honest
outcome when Bloomberg, news, or Kimi is missing — never fake output.

## 6. IBKR paper (optional)

1. Start TWS or IB Gateway and log into the **PAPER** account (`DU...`).
2. Enable the API: paper port 7497 (TWS) or 4002 (Gateway).
3. In `.env`: set `IBKR_ACCOUNT=DU...` (and `IBKR_PORT` if using Gateway).
4. Then:

```powershell
python -m pip install -e ".[ibkr]"
quantctl paper doctor                     # trading mode + DU prefix + kill switch
quantctl paper dry-run                    # computes a sample order, submits nothing
```

## 7. Dashboard (optional)

```powershell
python -m pip install -e ".[dashboard]"
quantctl dashboard
```

## Rules that protect you

- Never set `TRADING_MODE` to anything but `paper` — startup refuses otherwise.
- Keep `DRY_RUN=true` until you deliberately want paper orders submitted.
- `New-Item data\paper_trading\KILL_SWITCH -ItemType File` halts all new
  orders immediately; delete the file to resume.
- Raw Bloomberg exports are git-ignored; never commit terminal data.
