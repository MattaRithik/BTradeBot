# Resume Prompt (paste into a fresh Kimi session)

Continue the existing Quant AI research & paper-trading platform at
`/Users/rithikreddymatta/Desktop/Research/Bloomberg`. DO NOT restart or
rewrite completed work.

First read, in order:
1. PROJECT_STATE.md
2. .progress/CHECKPOINT.md
3. .progress/CURRENT_TASK.md
4. .progress/TEST_STATUS.md
5. DECISIONS.md
6. NEXT_STEPS.md
7. docs/ARCHITECTURE.md

Current state: Stage A (foundation) COMPLETE — 47 tests passing, ruff clean,
commit 4648919 on main. Stage B (Bloomberg data layer) is next.

Verify state before coding:
```bash
cd /Users/rithikreddymatta/Desktop/Research/Bloomberg
git status
.venv/bin/python -m pytest        # expect 47 passed
.venv/bin/ruff check src tests    # expect clean
```

Then implement Stage B per .progress/CURRENT_TASK.md and NEXT_STEPS.md:
provider interfaces → Bloomberg export adapter (CSV/XLSX normalization) →
BLPAPI desktop adapter (optional import, honest NOT ENTITLED) → data
validation → `quantctl bloomberg doctor/sample` → contract tests with mocked
blpapi.

Key invariants that must never regress: paper-only + dry-run defaults; UTC-
strict timestamps; gatekeeper blocks post-cutoff data with audit; sector
signals never tradable; Python does all math, LLMs only reason.

Update .progress/* and PROJECT_STATE.md after every module; commit after
green tests.
