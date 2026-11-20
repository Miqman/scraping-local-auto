"""Entry sidecar T24: uvicorn in-process dari app FastAPI yang ada.
Tanpa membuka browser OS (SLA_NO_BROWSER / Electron yang menampilkan UI).
Dibundle PyInstaller onedir oleh scripts/build_sidecar.py."""
import os

import uvicorn

from app.main import app

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1",
                port=int(os.environ.get("SLA_PORT", "8765")), log_level="info")
