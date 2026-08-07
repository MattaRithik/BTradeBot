# Next Steps

All planned stages (A–K) are COMPLETE. What remains is optional polish:

1. Real-data run on the college Bloomberg terminal — docs/COLLEGE_CHECKLIST.md
   (`quantctl bloomberg doctor` will report honest PASS/FAIL/NOT ENTITLED)
2. Real Kimi run once KIMI_API_KEY is set (MockModelProvider covers offline)
3. IBKR paper session with TWS/Gateway (`quantctl paper doctor` first)
4. mypy enforcement (configured in pyproject, not yet enforced)
5. Optional: more strategy builders, richer baselines, news entitlement
   adapter if the terminal proves access

Blocked externally (cannot be resolved offline):
- Live BLPAPI connectivity — college Bloomberg terminal only
- Bloomberg news entitlement — must be proven by doctor on terminal
- Real Kimi API call — needs KIMI_API_KEY
- Real IBKR paper connection — needs TWS/Gateway running
