"""T01: python -m app -> uvicorn 127.0.0.1:8765 + browser terbuka otomatis."""
import os
import threading
import webbrowser

import uvicorn

PORT = int(os.environ.get("SLA_PORT", "8765"))

if __name__ == "__main__":
    if not os.environ.get("SLA_NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}/")).start()
    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, log_level="info")