# Test Strategy

Use `tests/` as the only default collection root. Run fast unit tests first, isolated CRM/agent integration tests second, disposable migrations third and secret-gated live integrations separately. Every test owns its DB/workbook/environment and cleans artifacts. Operational data is never a test target.
