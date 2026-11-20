"""T22: scheduler harian in-process (story 6; Q5 default PRD §12 keputusan 2026-11-19).

Harian pukul `run_at` (default 06:00) waktu lokal mesin -> job `queued` rentang
kemarin (D-1), semua hari. Runner mengambil job queued seperti biasa — batas E6
(akun sama antri) / E7 (maks 2 paralel) tetap berlaku. Override manual = Job Baru.

At-most-once per hari: `last_run_date` ditandai SEBELUM job dibuat. Catch-up:
app dibuka setelah pukul run_at -> tetap jalan di hari yang sama; app tutup
seharian -> tidak ada backfill hari-hari sebelumnya.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta

import crawlers
from . import db

log = logging.getLogger("sla.scheduler")

TICK_S = 30
DEFAULT_RUN_AT = "06:00"
RUN_AT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def due(now_hhmm: str, run_at: str, last_run_date, today: str) -> bool:
    """Jatuh tempo bila jam lokal >= run_at dan belum jalan hari ini (string HH:MM
    zero-padded -> perbandingan leksikografis benar)."""
    return bool(RUN_AT_RE.match(run_at or "")) and now_hhmm >= run_at and last_run_date != today


class Scheduler:
    def __init__(self):
        self._task = None
        self._stop = False

    async def start(self):
        self._task = asyncio.get_running_loop().create_task(self._tick_loop())

    async def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()

    async def _tick_loop(self):
        while not self._stop:
            await asyncio.sleep(TICK_S)
            try:
                self.run_due()
            except Exception:
                log.exception("scheduler tick error")

    def run_due(self, now: datetime = None) -> int:
        """Sinkron & cepat (operasi DB saja); dipanggil dari tick asyncio."""
        now = now or datetime.now()
        today = now.date().isoformat()
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        hhmm = now.strftime("%H:%M")
        n = 0
        for s in db.list_schedules():
            if not s["enabled"] or not due(hhmm, s["run_at"], s["last_run_date"], today):
                continue
            db.mark_schedule_run(s["account_id"], today)  # at-most-once: tandai dulu
            meta = crawlers.BY_CODE.get(s["pos_code"])
            if meta is None or not meta.available:
                log.warning("scheduler: crawler %s belum tersedia — akun %s (%s) dilewati hari ini",
                            s["pos_code"], s["account_id"], s["label"])
                continue
            job_id = db.create_job(s["account_id"], s["pos_code"], yesterday, yesterday, [])
            n += 1
            log.info("scheduler: job %s dibuat (akun %s %s, rentang %s)",
                     job_id, s["account_id"], s["label"], yesterday)
        return n


def _self_check() -> None:
    today = "2026-11-19"
    assert due("05:59", "06:00", None, today) is False  # belum jamnya
    assert due("06:00", "06:00", None, today) is True  # tepat jamnya
    assert due("09:30", "06:00", None, today) is True  # catch-up hari sama
    assert due("09:30", "06:00", today, today) is False  # sudah jalan hari ini
    assert due("09:30", "06:00", "2026-11-18", today) is True  # hari baru
    assert due("23:59", "00:00", None, today) is True  # tengah malam
    assert due("06:00", "25:00", None, today) is False  # run_at invalid
    assert due("06:00", "", None, today) is False
    assert RUN_AT_RE.match("06:00") and RUN_AT_RE.match("23:59") and RUN_AT_RE.match("00:00")
    assert not RUN_AT_RE.match("6:00") and not RUN_AT_RE.match("24:00") and not RUN_AT_RE.match("06:60")
    print("scheduler self_check ok")


if __name__ == "__main__":
    _self_check()
