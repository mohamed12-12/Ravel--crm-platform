# Seeds

Production-safe seed data and import fixtures belong here.

Do not commit customer data or raw exported workbooks.

For TestSprite/UI validation, use `seed_testsprite_ui_cases.py` to create
observable duplicate travelers and a pending handoff in a test or staging
database:

```bash
python database/seeds/seed_testsprite_ui_cases.py --mode all
```

Available modes:

- `duplicates`
- `handoff`
- `all`
