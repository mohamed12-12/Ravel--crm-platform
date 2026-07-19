# Pytest Collection Policy

Default collection is limited to `tests/` by `pytest.ini`:

```ini
[pytest]
testpaths = tests
norecursedirs = archive .history .tmp-* node_modules testsprite_tests
```

Historical tests remain available for manual review but are not silently collected as active tests. Legitimate active tests must live under `tests/` and be named `test_*.py` or `*_test.py`.
