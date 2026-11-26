"""Interface crawler + konteks scrape bersama (dipakai runner & semua POS)."""
import hashlib
import json
from datetime import date, timedelta
from typing import Iterator, List, Tuple

from app import browser, db
from app.errors import CrawlError


# Versi kontrak plugin import (app/plugins.py): naikkan bila interface Crawler berubah;
# file plugin wajib mendeklarasikan PLUGIN_API yang sama.
PLUGIN_API = 1


class CrawlContext:
    """Pegangan page + status DB + event resume selama satu job."""

    def __init__(self, session: browser.Session, runner, job_id: int, gate_fn=None, gate_assist=None):
        self.session = session
        self.page = session.page
        self.runner = runner
        self.job_id = job_id
        self.branch_id = None  # None = tahap login / auto-fetch cabang
        self.gate_fn = gate_fn  # predikat gate khusus POS (mis. token Turnstile)
        self.gate_assist = gate_assist  # auto-click aman selama pause (mis. checkbox Turnstile)

    def check_gate(self):
        gated = self.gate_fn(self.page) if self.gate_fn else browser.is_gated(self.page)
        if not gated:
            return
        if self.branch_id is None:
            bid = -self.job_id  # event key level job
            browser.wait_gate_clear(
                self.session,
                on_wait=lambda: db.set_job_status(self.job_id, "waiting_user"),
                on_resume=lambda: db.set_job_status(self.job_id, "running"),
                resume_event=self.runner.resume_event(bid),
                gate_fn=self.gate_fn,
                assist_fn=self.gate_assist,
            )
        else:
            bid = self.branch_id
            browser.wait_gate_clear(
                self.session,
                on_wait=lambda: db.set_branch_status(bid, "waiting_user"),
                on_resume=lambda: db.set_branch_status(bid, "running"),
                resume_event=self.runner.resume_event(bid),
                gate_fn=self.gate_fn,
                assist_fn=self.gate_assist,
            )

    def after_nav(self, response=None):
        """Panggil setelah tiap navigasi: deteksi browser mati, HTTP error, gate."""
        self.session.check_alive()
        if response is not None and response.status >= 500:
            raise CrawlError("network", f"Server POS membalas HTTP {response.status}")
        self.check_gate()

    def require_logged_in(self, login_url_part: str):
        if login_url_part in self.page.url:
            raise CrawlError("network", "Sesi putus di tengah scrape (kembali ke halaman login)")


def stable_trx_id(row: dict) -> str:
    """E11: POS tanpa id stabil -> sha256 baris ternormalisasi."""
    norm = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def date_chunks(date_from: str, date_to: str, max_days: int) -> List[Tuple[str, str]]:
    """E9: pecah rentang sesuai batas POS; transparan bagi user."""
    a = date.fromisoformat(date_from)
    b = date.fromisoformat(date_to)
    if b < a:
        raise CrawlError("unknown", "Rentang tanggal terbalik")
    out = []
    cur = a
    while cur <= b:
        end = min(cur + timedelta(days=max_days - 1), b)
        out.append((cur.isoformat(), end.isoformat()))
        cur = end + timedelta(days=1)
    return out


class Crawler:
    pos_code = ""
    name = ""
    base_url = ""
    max_range_days = 31
    has_branch_api = True
    gate_kind = "turnstile"

    def login(self, ctx: CrawlContext, username: str, password: str) -> None:
        raise NotImplementedError

    def list_branches(self, ctx: CrawlContext) -> List[Tuple[str, str]]:
        raise NotImplementedError

    def scrape_branch(self, ctx: CrawlContext, branch_key: str,
                      date_from: str, date_to: str) -> Iterator[List[dict]]:
        raise NotImplementedError