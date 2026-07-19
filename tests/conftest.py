"""Keep legacy integration tests isolated from the production auth default."""

import os


os.environ.setdefault("CRM_AUTH_ENABLED", "false")
os.environ.setdefault("DATA_AUTHORITY", "crm")
os.environ.setdefault("CRM_ACCESS_MODE", "shared_service")
os.environ.setdefault("DIRECT_IMPORT_APPLY_ENABLED", "false")
os.environ.setdefault("CRM_SHEET_MIRROR_ENABLED", "false")
os.environ.setdefault("DEMO_DATA_MODE", "true")
