# Tests

The Python test suite locks key MVP behavior:

- workbook cleanup and audit logic
- read-only and write-through agent behavior
- booking draft creation
- identity resolution and duplicate safety
- DB-first sync behavior
- spreadsheet adapter compatibility

Run from the repository root:

```bash
python -m pytest tests
```
