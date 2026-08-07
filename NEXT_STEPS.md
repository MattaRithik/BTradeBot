# Next Steps

Immediate (Stage B):
1. `data/providers.py` — MarketDataProvider / ReferenceDataProvider /
   FundamentalDataProvider / NewsDataProvider interfaces + DiagnosticStatus
2. `data/bloomberg_desktop.py` — BLPAPI adapter (optional import, honest
   NOT ENTITLED / unavailable statuses)
3. `data/bloomberg_export.py` — CSV/XLSX import, ticker normalization,
   PX_* field mapping
4. `data/validation.py` — data-quality checks
5. `quantctl bloomberg doctor` + `quantctl bloomberg sample`
6. Contract tests with a mocked BLPAPI module

Then Stage C (gatekeeper-backed repositories, feature engine, sample data).

Blocked externally (cannot be resolved offline):
- Live BLPAPI connectivity — college Bloomberg terminal only
- Bloomberg news entitlement — must be proven by doctor on terminal
- Real Kimi API call — needs KIMI_API_KEY
- Real IBKR paper connection — needs TWS/Gateway running
