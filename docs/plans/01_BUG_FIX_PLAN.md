# Bug Fix Plan

1. Version the session runtime contract and align deterministic/tool-calling fixtures.
2. Repair TEST-01 through TEST-05 without weakening production safeguards.
3. Configure pytest collection to `tests/` only.
4. Replace swallowed write/sync failures with typed errors and correlation IDs.
5. Add a regression test for each change.
