# Next Steps

Immediate (Stage E — thesis / mapping / validation / ranking):
1. `research/evidence.py` — news → EvidenceCard extraction via Stage D agents
   (cards cite source news ids; gatekeeper-filtered inputs only)
2. `research/thesis.py` — SectorThesis builder with causal chains +
   invalidation conditions
3. `research/mapping.py` — thesis → CompanyMapping/ETFMapping + tradability
   filters (liquidity/history via PIT repository)
4. `research/scoring.py` — transparent Python-only ScoreBreakdown, weights
   from configs/scoring.yaml (must sum to 1.0)
5. `research/validation.py` — bull/bear/risk/leakage/judge debate;
   leakage_detected forces REJECTED
6. `research/ranking.py` — cross-sector leaderboard; selecting NOTHING is
   a valid outcome when evidence is weak
7. Tests: `pytest tests/test_research*` then full suite

Then Stage F (signals + portfolio + risk).

Blocked externally (cannot be resolved offline):
- Live BLPAPI connectivity — college Bloomberg terminal only
- Bloomberg news entitlement — must be proven by doctor on terminal
- Real Kimi API call — needs KIMI_API_KEY (MockModelProvider covers offline)
- Real IBKR paper connection — needs TWS/Gateway running
