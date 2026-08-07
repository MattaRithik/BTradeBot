# Failed Tasks

(none yet)

## Notes for future sessions
- Initial lint run flagged StrEnum (UP042), blind-except test (B017),
  nested-if (SIM102) — all fixed; keep new code StrEnum-style.
- `require_visible` originally rejected without raising — caught by tests.
  Any gatekeeper change must re-run tests/test_gatekeeper.py.
