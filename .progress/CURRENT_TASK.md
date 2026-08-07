# Current Task

task: Stage E — thesis / mapping / validation / ranking
objective: Evidence engine (news→EvidenceCard via agents), thesis builder,
  causal chains, security mapping + tradability filters, transparent scoring
  (config weights), bull/bear/risk/leakage/judge debate, sector competition
  leaderboard with "choose nothing" allowed.
files_expected:
  - src/quant_platform/research/__init__.py
  - src/quant_platform/research/evidence.py
  - src/quant_platform/research/thesis.py
  - src/quant_platform/research/mapping.py
  - src/quant_platform/research/scoring.py
  - src/quant_platform/research/validation.py
  - src/quant_platform/research/ranking.py
  - tests/test_research_*.py
dependencies: Stage D models + agents (done); configs/scoring.yaml weights sum to 1.0
acceptance:
  - EvidenceCards cite source news ids; nothing fabricated
  - SectorThesis carries causal chain + invalidation conditions
  - Mapping applies tradability filters (liquidity/history from repository)
  - Scoring is Python-only, weights from configs/scoring.yaml
  - Debate uses Stage D agents; leakage_detected forces REJECTED
  - Leaderboard may select NOTHING when evidence is weak
tests: targeted `pytest tests/test_research*` then full suite
