import os
from pathlib import Path


def _load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


SYSTEM_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SYSTEM_ROOT.parent
_load_env_file(REPO_ROOT / ".env")
_load_env_file(SYSTEM_ROOT / ".env")

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-rahma-traveler')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Google Sheets integration
    GOOGLE_SHEET_ID   = os.environ.get('GOOGLE_SHEET_ID', '')
    GOOGLE_CREDS_PATH = os.environ.get('GOOGLE_CREDS_PATH', 'rahma-496108-a27c767efdaf.json')

class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL', 'sqlite:///rahma_traveler_dev.db')

class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')

config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig
}
