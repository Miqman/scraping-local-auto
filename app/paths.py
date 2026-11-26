"""Lokasi direktori data aplikasi (PRD §8: permission user-only)."""
import os
import stat
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    d = Path(os.environ.get("SLA_DATA_DIR", PROJECT_ROOT / "data"))
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)  # 0700; Windows: ACL user-profile sudah user-only
    except OSError:
        pass
    return d


def db_path() -> Path:
    return data_dir() / "app.db"


def profiles_dir() -> Path:
    d = data_dir() / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_dir(account_id: int) -> Path:
    d = profiles_dir() / f"account_{account_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d



def plugins_dir() -> Path:
    d = data_dir() / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exports_dir() -> Path:
    d = data_dir() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def keyfile_path() -> Path:
    return data_dir() / "master.key"


STATIC_DIR = PROJECT_ROOT / "static"
TEMPLATES_DIR = PROJECT_ROOT / "templates"