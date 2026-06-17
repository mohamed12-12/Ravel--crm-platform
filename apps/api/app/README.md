# Database-Backed Flask CRM

This package contains the CRM Flask application backed by SQLAlchemy models and migrations.

- `models/` defines database entities.
- `routes/` defines Jinja pages and CRM APIs.
- `services/` contains import, identity, and copy guard helpers.
- `templates/` and `static/` contain the current operator UI.

The package name remains `app` for Flask compatibility. Import changes should be coordinated with tests and scripts.
