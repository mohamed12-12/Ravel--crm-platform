# Regression Test Checklist

- Default pytest collection includes only `tests/`.
- Active suite passes with isolated DB/workbook.
- Deterministic and tool-calling mode fixtures explicitly set mode.
- Writes validate authorization, inventory and idempotency.
- Agent replies use verified CRM facts and do not expose prompts, secrets or thought signatures.
- Attachment and webhook security tests pass.
