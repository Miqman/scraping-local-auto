"""T24 dev-only: build sidecar PyInstaller onedir (runtime Python + deps + driver Playwright).
Jalankan dari root repo: python scripts/build_sidecar.py
Hasil: sidecar/dist/sla-sidecar/ (dipakai desktop/package.json extraResources).
Browser Camoufox terpisah: vendor/camoufox (salin dari cache `python -m camoufox path`;
folder fonts/ opsional runtime — diuji launch tanpa fonts, lihat bukti T24)."""
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent

PyInstaller.__main__.run([
    str(ROOT / "sidecar" / "entry.py"),
    "--name", "sla-sidecar",
    "--noconfirm", "--clean", "--onedir", "--noconsole",
    "--distpath", str(ROOT / "sidecar" / "dist"),
    "--workpath", str(ROOT / "sidecar" / "build"),
    "--specpath", str(ROOT / "sidecar"),
    "--paths", str(ROOT),
    "--add-data", f"{ROOT / 'templates'};templates",
    "--add-data", f"{ROOT / 'static'};static",
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "camoufox",
    "--collect-data", "camoufox",
    "--collect-data", "language_tags",
    "--collect-data", "browserforge",
    "--collect-data", "apify_fingerprint_datapoints",
    "--collect-data", "playwright",  # hook resmi playwright juga aktif (entry_points)
    "--hidden-import", "camoufox.sync_api",
    "--hidden-import", "greenlet",
    "--hidden-import", "pyee.asyncio",
    "--hidden-import", "keyring.backends.Windows",
    "--hidden-import", "uvicorn.loops.asyncio",
    "--hidden-import", "uvicorn.protocols.http.h11_impl",
    "--hidden-import", "uvicorn.protocols.websockets.wsproto_impl",
    "--hidden-import", "uvicorn.lifespan.on",
    "--hidden-import", "_yaml",
])
