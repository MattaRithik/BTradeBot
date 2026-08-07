# REFERENCES — External APIs & Design Prior Art

Compiled 2026-08-07 from fetched official documentation. Facts marked **UNVERIFIED** could not be confirmed from a fetched source; do not rely on them without re-checking.

---

## 1. Bloomberg BLPAPI (Python)

### Official URLs
- API library support page (install instructions): https://www.bloomberg.com/professional/support/api-library
- Pip index (only working one): https://blpapi.bloomberg.com/repository/releases/python/simple/
- Developer docs landing: https://blpapi.bloomberg.com/ (mirror: https://bloomberg.github.io/blpapi-docs/)
- Python class reference (3.26.6): https://bloomberg.github.io/blpapi-docs/python/3.26.6/
- BLPAPI Core Developer Guide (PDF): https://data.bloomberglp.com/professional/sites/10/2017/03/BLPAPI-Core-Developer-Guide.pdf
- BLPAPI Core User Guide (PDF): https://data.bloomberglp.com/professional/sites/10/2017/03/BLPAPI-Core-User-Guide.pdf

### Verified facts
- **Install**: `python -m pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi` — verbatim from Bloomberg's support page. There is **no `blpapi` package on PyPI** (JSON API returns 404); distribution is exclusively Bloomberg's index. The old URLs (`/download/sdks/blpapi`, `bcms.bloomberg.com/pip/simple/`, bintray) are dead.
- **Latest version**: `3.26.6.1` (SDK line 3.26.6). Wheels are **self-contained** — the C++ API is bundled, no separate C++ install needed.
- **Python support**: release artifacts require `>=3.10`; Bloomberg states they provide wheels "for all Python versions supported by community". Wheels for win32/amd64, macOS arm64, manylinux2014 x86_64, plus sdist.
- **Session pattern** (from official SDK examples in the 3.26.6.1 sdist): connect to **localhost:8194** (default), open service **`//blp/refdata`**, then `service.createRequest("ReferenceDataRequest")` appending `securities` / `fields` (e.g. `PX_LAST`, `DS002`) with optional `overrides`; **`HistoricalDataRequest`** (BDH-equivalent) uses `periodicityAdjustment`, `periodicitySelection`, `startDate`/`endDate` (YYYYMMDD), `maxDataPoints`, `returnEids`.
- **Entitlements**: BLPAPI models data access via numeric **EIDs** (Entitlement Identifiers); contributor-page and premium data access is entitlement-controlled (Core User Guide / Developers Guide).
- **News**: Bloomberg's machine-readable news is a **separately subscribed enterprise product** — customizable Real-Time News Feeds (Bloomberg News + >175k web/social sources, News Analytics sentiment, News Insights), plus end-of-day Textual News / News Analytics via **Data License** (data.bloomberg.com). Official statement: https://www.prnewswire.com/news-releases/bloomberg-launches-customizable-real-time-news-feeds-for-enhanced-systematic-workflows-302701889.html
- **UNVERIFIED**: whether the **Desktop API (localhost:8194) exposes news at all** — no `//blp/news` service appears in official docs or SDK examples. Assume news requires separate enterprise feeds/licensing; treat API news as unavailable until confirmed with Bloomberg.

### Design implication for this project
- Pin `blpapi==3.26.6.1` from Bloomberg's index in requirements (document the custom `--index-url`), Python >= 3.10.
- Wrap session lifecycle + `//blp/refdata` request/response behind an adapter interface so Bloomberg is swappable; assume reference/historical data only — **no news via Desktop API**; news sentiment must come from another source (or an enterprise feed contract later).

---

## 2. Interactive Brokers (IBKR) API

### Official URLs
- TWS API docs: https://interactivebrokers.github.io/tws-api/ (landing), initial setup: https://interactivebrokers.github.io/tws-api/initial_setup.html
- IBKR Campus guide: https://ibkrcampus.com/ibkr-api-page/twsapi-doc/
- Official client (`ibapi`) on PyPI: https://pypi.org/project/ibapi/
- Paper account setup: https://www.ibkrguides.com/clientportal/papertradingaccount.htm

### Verified facts
- **Architecture**: TCP socket protocol to a running **TWS or IB Gateway** instance with a GUI login (no headless). IB Gateway is ~40% lighter; both apps restart daily. TWS by default refuses API connections; IB Gateway accepts socket API by default.
- **`ibapi` on PyPI is stale**: latest is `9.81.1.post1` (2020-12-06) while current TWS API is 10.x. Primary distribution is the installer gated behind a Non-Commercial License at interactivebrokers.github.io. → Prefer a third-party client that implements the protocol itself.
- **`ib_insync` is archived** (author Ewald de Wit passed away March 2024): https://github.com/erdewit/ib_insync (BSD-2-Clause, `archived: true`).
- **Successor: `ib_async`** — https://github.com/ib-api-reloaded/ib_async, maintained by Matt Stancliff; PyPI name `ib_async`, latest **2.1.0** (2025-12-08), Python >= 3.10; **implements the full IBKR binary protocol internally — official `ibapi` package NOT required**. Docs: https://ib-api-reloaded.github.io/ib_async/
- Alternative REST path: Client Portal Web API (https://ibkrcampus.com/ibkr-api-page/cpapi-v1/) via `ibind` (actively maintained, https://github.com/Voyz/ibind).
- **Default ports** (verified on official docs):
  - TWS live **7496**, TWS paper **7497** (initial_setup.html)
  - IB Gateway live **4001**, IB Gateway paper **4002** (rtd_simple_syntax.html); default host 127.0.0.1
  - Warning: running paper and live TWS on one machine — ensure the client connects to the correct session/ports.
- **Paper account**: auto-provisioned for new clients (1,000,000 USD paper equity), managed at Client Portal → Settings → Account Configuration → Paper Trading Account (tied to an existing IBKR account).
- **TWS API settings** (Edit → Global Configuration → API → Settings): enable **"Enable ActiveX and Socket Clients"** (required); set socket port (7497 paper); note **"Read Only API" is enabled by default** and blocks order info — disable it for trading; optional Master Client ID to see all clients' orders.
- **UNVERIFIED**: explicit official text on "Trusted IPs" (add 127.0.0.1) — shown only in official screenshot + ib_async README; current TWS API 10.x version number not confirmed from a download page; IB Gateway download URLs cited but IBKR pages not fetched.

### Design implication for this project
- Use **`ib_async`** as the IBKR client (actively maintained, no stale C++-bound dependency, asyncio-native). Paper trading: IB Gateway paper on **4002** (lighter, API-first) or TWS paper on **7497**.
- Config must make port/host explicit and refuse to run order flow against 7496/4001 in paper mode (live/paper guard).
- Setup runbook: enable socket clients, disable Read-Only API, match ports, trusted IP 127.0.0.1.

---

## 3. Kimi (Moonshot AI) API

### Official URLs
- API overview: https://platform.kimi.ai/docs/api/overview (platform.moonshot.ai/docs redirects here)
- Chat completions reference: https://platform.kimi.ai/docs/api/chat
- Model list: https://platform.kimi.ai/docs/models
- Pricing: https://platform.kimi.ai/docs/pricing/chat-k3 (and sibling pages per model)
- JSON mode guide: https://platform.kimi.ai/docs/guide/use-json-mode-feature-of-kimi-api
- China platform docs: https://platform.moonshot.cn/docs

### Verified facts
- **Base URLs**: international **`https://api.moonshot.ai/v1`**, China **`https://api.moonshot.cn/v1`**. Auth: `Authorization: Bearer $MOONSHOT_API_KEY`; keys are platform-specific (an .ai key gets 401 on .cn and vice versa).
- **OpenAI-compatible**: `POST /v1/chat/completions`; the official OpenAI Python/Node SDKs (>=1.0.0) work by repointing `base_url`. Extra endpoints: `/v1/models`, `/v1/files`, `/v1/batches`, `/v1/tokenizers/estimate-token-count`, `/v1/users/me/balance`. Kimi-specific quirks: `thinking` via `extra_body`, per-message `partial` field, K3 `reasoning_effort` (low/high/max), responses carry `reasoning_content` and `usage.cached_tokens`.
- **Current models** (per official model list; all `kimi-k2-*-preview` and `kimi-latest` names are retired):
  - `kimi-k3` — flagship, 1M-token context
  - `kimi-k2.7-code` / `kimi-k2.7-code-highspeed` — 256K context
  - `kimi-k2.6` — general multimodal (text/image/video), thinking + non-thinking, 256K
  - `kimi-k2.5` — 256K, closed to new users, sunset 2026-08-31
  - `moonshot-v1-8k/32k/128k` (+`-vision-preview`) — full sunset 2026-08-31
- **Structured output**: `response_format` supports `{"type":"json_object"}` (JSON mode) and **`{"type":"json_schema","json_schema":{...}}` (true structured output)**. **Tool calling** via `tools` (JSON Schema), `tool_choice` (auto/none/required/specific), `finish_reason:"tool_calls"`, results fed back as `role:"tool"`. Don't mix partial mode with `json_object`.
- **Pricing** (international, USD per 1M tokens; cache-hit input / cache-miss input / output):
  - `kimi-k3`: $0.30 / $3.00 / $15.00
  - `kimi-k2.7-code`: $0.19 / $0.95 / $4.00; highspeed: $0.38 / $1.90 / $8.00
  - `kimi-k2.6`: $0.16 / $0.95 / $4.00
  - `moonshot-v1-8k`: $0.20 in / $2.00 out; 32k: $1.00/$3.00; 128k: $2.00/$5.00 (no cache tiers)
  - China platform prices in CNY on platform.moonshot.cn (e.g. `kimi-k3` ¥2/¥20/¥100).
- **No official "Kimi swarm" / multi-agent API exists.** The endpoint list contains nothing of the kind. "Agent Swarm" is a **model capability of Kimi K2.5** (self-directed orchestration of ~100 sub-agents, https://github.com/MoonshotAI/Kimi-K2.5) and a consumer kimi.com feature — **not a public orchestration API**. Any multi-agent layer must be built client-side on chat completions + tool calling.
- **UNVERIFIED**: batch-API pricing, rate limits, tools pricing (nav-listed, not extracted); moonshot-v1 pricing table headers; whether `platform.kimi.ai` fully replaces `platform.moonshot.ai` as the console domain (both resolve to the same docs; the `api.moonshot.ai/v1` base URL is confirmed current).

### Design implication for this project
- Client: plain OpenAI Python SDK with `base_url="https://api.moonshot.ai/v1"` — no vendor SDK needed. Default model `kimi-k2.6` (cheap, thinking-capable) or `kimi-k3` for heavy research; **do not reference retired `kimi-k2-*` names**.
- Use `json_schema` structured output for machine-readable research artifacts; use tool calling for any agent loops we build ourselves.
- Build our own multi-agent orchestration on top of standard endpoints — there is nothing official to integrate.

---

## 4. Backtesting frameworks (design prior art — inspiration only, no code copying)

| Project | URL | License | Useful concept | What we do differently |
|---|---|---|---|---|
| vectorbt | https://github.com/polakowo/vectorbt · https://vectorbt.dev | Apache-2.0 + Commons Clause (restrictive — no selling products primarily built on it) | Vectorized "think in matrices" simulation; `Portfolio.from_signals/from_orders`; Numba-accelerated parameter sweeps; walk-forward optimization | We stay event-driven for point-in-time correctness; borrow vectorization only for parameter screening, not the core engine |
| backtrader | https://github.com/mementum/backtrader · https://www.backtrader.com/docu | GPL-3.0 (upstream unmaintained; last PyPI release 2023-04) | Cerebro engine; broker simulation with full order-type/slippage/commission model; analyzers/observers; data-feed abstraction | Inspiration only (GPL); we write our own OMS with cleaner asyncio-native design and live/backtest parity |
| zipline-reloaded | https://github.com/stefan-jansen/zipline-reloaded · https://zipline.ml4trading.io | Apache-2.0 | Event-driven lifecycle (`initialize`/`handle_data`, `order_target`, `data.history`); data bundles + CLI; exchange_calendars; perf output to pyfolio-style analysis | Same lifecycle idea, but our event loop enforces strict timestamp ordering / no lookahead by construction |
| qlib (Microsoft) | https://github.com/microsoft/qlib · https://qlib.readthedocs.io | MIT | Point-in-time database (released 2022-03); expression engine for formulaic alphas; DataHandler learn/infer processor pipelines; Dataset train/valid/test segments; `qrun` YAML workflow; Alpha158/360 | We adopt the PIT-store + learn/infer split concepts, but with our own storage (likely Parquet/DuckDB) instead of qlib's .bin format |
| lumibot | https://github.com/Lumiwealth/lumibot · https://lumibot.lumiwealth.com | GPL-3.0 per repo LICENSE (PyPI metadata still says MIT — stale; treat repo as authoritative) | **Same strategy code for backtest and live/paper** via broker abstraction; supported brokers incl. Alpaca, IBKR, Schwab, Tradier; strategy lifecycle (`initialize`, `on_trading_iteration`) | Same parity principle (GPL — no reuse); narrower broker set (IBKR + Bloomberg) with a stricter, typed adapter interface |

### Design implication for this project
- Core: **event-driven engine** (zipline/backtrader lineage) with an **algorithm lifecycle** (`initialize` / per-bar or per-event handler) and a simulated **OMS/broker** (order types, slippage, commissions — backtrader's model is the reference).
- Data: **point-in-time store** with as-of timestamps for every observation (qlib's PIT concept); research/ML path gets **learn/infer processor separation** (qlib DataHandlerLP) to prevent training-time leakage.
- Execution parity: one strategy interface running against both the backtest broker and the live IBKR adapter (lumibot's key idea) — this is the most important architectural borrow.
- License posture: qlib (MIT) and zipline-reloaded (Apache-2.0) are safe to read closely; backtrader/lumibot (GPL) and vectorbt (Commons Clause) are **concepts-only** — do not copy code or mirror APIs.

---

## Not found / unverified

- **No official Kimi "swarm" or multi-agent orchestration API exists.** Kimi K2.5's "Agent Swarm" is an in-model capability, not an endpoint. We must build orchestration ourselves.
- **Bloomberg Desktop API news access**: UNVERIFIED whether `localhost:8194` sessions expose any news service; official docs/examples show none. Assume no news via DAPI.
- **Bloomberg news feed licensing/pricing**: not published; enterprise-negotiated (Real-Time News Feeds / Data License).
- **Current TWS API software version (10.x)**: not confirmed from an official download page; PyPI `ibapi` is stuck at 9.81.1.post1 (2020).
- **TWS "Trusted IPs" official documentation text**: only seen in official screenshots and third-party docs.
- **zipline-reloaded Pipeline API details** and **qlib PIT-database dedicated doc page**: not fetched.
- **Kimi batch pricing / rate limits / tools pricing**: pages listed in nav but content not extracted.
- **lumibot license-change date** (MIT → GPL-3.0): repo history not inspected.
