# Next Steps

All planned stages (A–K) are COMPLETE, plus the NewsCatcher news layer
(provider, deterministic news-intel orchestration, research-runtime
integration, `quantctl news` CLI — 336 tests green). What remains:

1. Real-data run on the college Bloomberg terminal — docs/COLLEGE_CHECKLIST.md
   (`quantctl bloomberg doctor` will report honest PASS/FAIL/NOT ENTITLED)
2. Real Kimi run once KIMI_API_KEY is set (MockModelProvider covers offline)
3. First live NewsCatcher pull: `quantctl news doctor`, then
   `quantctl news search --query "NVIDIA" --limit 5`, then a real
   `quantctl research run` with dual news sources
4. Monitor NewsCatcher API usage against the plan quota (per-run guards:
   `max_api_calls_per_run` / `max_articles_per_run` in configs/news.yaml;
   disk cache in `data/raw/news_cache/` keeps repeat runs free)
5. IBKR paper session with TWS/Gateway (`quantctl paper doctor` first)
6. mypy enforcement (configured in pyproject, not yet enforced)
7. Optional: more strategy builders, richer baselines, news entitlement
   adapter if the terminal proves access

Blocked externally (cannot be resolved offline):
- Live BLPAPI connectivity — college Bloomberg terminal only
- Bloomberg news entitlement — must be proven by doctor on terminal
- Real Kimi API call — needs KIMI_API_KEY
- Real NewsCatcher API call — needs NEWSCATCHER_API_KEY
- Real IBKR paper connection — needs TWS/Gateway running
