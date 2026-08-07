# Current Task

task: Stage B — Bloomberg data layer
objective: Provider interfaces + Bloomberg Desktop (BLPAPI) adapter +
  CSV/XLSX export adapter + data validation + `bloomberg doctor/sample` CLI.
files_expected:
  - src/quant_platform/data/__init__.py
  - src/quant_platform/data/providers.py
  - src/quant_platform/data/bloomberg_desktop.py
  - src/quant_platform/data/bloomberg_export.py
  - src/quant_platform/data/validation.py
  - src/quant_platform/cli/bloomberg.py
  - tests/test_bloomberg_export.py, tests/test_data_validation.py,
    tests/test_bloomberg_desktop_contract.py
dependencies: Stage A core (done)
acceptance:
  - Export adapter normalizes `NVDA US Equity`→`NVDA` preserving raw id
  - PX_OPEN/HIGH/LOW/LAST/VOLUME/CUR_MKT_CAP mapped to schemas
  - Doctor prints PASS / FAIL / NOT ENTITLED per capability, exit codes honest
  - Contract tests mock blpapi module; no real Bloomberg needed
  - Corrupt exports fail loudly, never silently pass
tests: targeted `pytest tests/test_bloomberg*` then full suite
