# Resume Prompt (paste into a fresh Kimi session)

The Quant AI research & paper-trading platform at
`/Users/rithikreddymatta/Desktop/Research/Bloomberg` is FEATURE COMPLETE:
all stages A–K are implemented, tested, and committed on main.

First read, in order:
1. PROJECT_STATE.md
2. .progress/CHECKPOINT.md
3. .progress/COMPLETED_TASKS.md
4. .progress/TEST_STATUS.md
5. DECISIONS.md
6. docs/ARCHITECTURE.md

Verify state before doing anything:
```bash
cd /Users/rithikreddymatta/Desktop/Research/Bloomberg
git status                     # expect clean
.venv/bin/python -m pytest     # expect all passed (see TEST_STATUS.md)
.venv/bin/ruff check src tests # expect clean
.venv/bin/quantctl demo --data-root /tmp/quantdemo  # end-to-end offline demo
```

Remaining work is OPTIONAL polish, not missing stages:
- Real-data run on the college Bloomberg terminal (docs/COLLEGE_CHECKLIST.md)
- Real Kimi run once KIMI_API_KEY is set (MockModelProvider covers offline)
- mypy enforcement (configured, not enforced)
- Additional strategy builders / baselines if desired

Key invariants that must never regress: paper-only + dry-run defaults; UTC-
strict timestamps; gatekeeper blocks post-cutoff data with audit; sector
signals never tradable; Python does all math, LLMs only reason; snapshots
frozen before future data opens; failure analysis never rewrites history.

Update .progress/* and PROJECT_STATE.md after every change; commit after
green tests.
