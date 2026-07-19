# Test Execution Report

Environment: Windows PowerShell, Python 3.14, repository root. No operational-data mutation command was issued.

| Command | Result | Duration |
|---|---:|---:|
| `python -m pytest --collect-only -q` | 284 collected, 1 archive collection error | 9.84s |
| `python -m pytest tests -q` | no final result before 124s execution window | 124s |
| controlled phases 0-4 group | 117 passed, 4 failed | 105.97s |
| controlled phases 5-11 group | 99 passed, 1 failed | 29.92s |
| controlled phases 20/27/39 + data group | 63 passed | 18.03s |

Remediation result: **289 passed, 0 failed, 0 skipped reported** across the active suite, including five new security tests. Default collection is clean with no archived import error.

| ID | Failure | Root cause | Priority |
|---|---|---|---|
| TEST-01 | phase1/phase2 system-alignment | expected legacy intake completion; got `identity_required` | High |
| TEST-02 | phase3 booking write-through | expected `completed`; got `identity_required` | High |
| TEST-03 | phase3 demo web | expected `awaiting_phone`; got `identity_required` | Medium |
| TEST-04 | phase4 Gemini validation | fixture omits required thought signature | Medium |
| TEST-05 | phase8 demo alignment | expected `awaiting_phone`; got `identity_required` | Medium |

Fix the mode/state contract and test fixtures; do not weaken thought-signature validation.
