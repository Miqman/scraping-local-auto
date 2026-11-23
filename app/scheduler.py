"""Scheduler interval menit per akun — hanya berjalan saat aplikasi terbuka.

Tiap akun punya `interval_minutes` (min 1). Tick tiap TICK_S detik memeriksa
jadwal enabled yang jatuh tempo: now >= last_run_at + interval. Catch-up:
app dibuka setelah lewat -> langsung due sekali; tanpa backfill hari-hari
sebelumnya saat app tertutup. Job `queued` menyambung rentang job terakhir akun:
mulai H+1 setelah date_to terakhir sampai hari ini (job pertama: kemarin..hari ini);
bila sudah mencakup hari ini, rentang diulang (refresh). Cabang job = pilihan cabang job
terakhir akun (bukan auto-fetch semua); akun belum punya job -> auto-fetch. Runner mengambil
job queued seperti biasa — batas E6/E7 tetap berlaku. Override manual = Job Baru.

Pengaman:
- A: akun sedang punya job aktif -> skip tanpa menandai (jangan menumpuk antrian).
- B: rentang menyambung job terakhir akun (bukan selalu kemarin–hari ini).
- C: job terbaru gagal auth -> skip tanpa menandai + warning; berhenti sampai
  job di-retry/manual (cegah lockout akun).
- D: purge job lama sekali per ganti hari.

At-most-once: `last_run_at` ditandai SEBELUM job dibuat.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

import crawlers
from . import db

log = logging.getLogger("sla.scheduler")

TICK_S = 10
MIN_INTERVAL = 1


def due(now: datetime, interval_minutes: int, last_run_at) -> bool:
    """Jatuh tempo bila belum pernah jalan, atau now >= last_run_at + interval menit."""
    if last_run_at is None:
        return True
    return now >= datetime.fromisoformat(last_run_at) + timedelta(minutes=interval_minutes)


class Scheduler:
    def __init__(self):
        self._task = None
        self._stop = False
        self._last_purge_date = None

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
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
        if self._last_purge_date != today:  # saran D: purge sekali per ganti hari
            db.purge_old_jobs()
            self._last_purge_date = today
        n = 0
        for s in db.list_schedules():
            if not s["enabled"] or not due(now, s["interval_minutes"], s["last_run_at"]):
                continue
            aid = s["account_id"]
            if db.account_busy(aid):  # saran A: jangan menumpuk antrian
                continue
            if db.last_job_auth_failed(aid):  # saran C: cegah lockout
                log.warning("scheduler: job terakhir akun %s (%s) gagal auth — jadwal ditahan sampai retry/manual",
                            aid, s["label"])
                continue
            meta = crawlers.BY_CODE.get(s["pos_code"])
            db.mark_schedule_run(aid, now_iso)  # at-most-once: tandai dulu
            if meta is None or not meta.available:
                log.warning("scheduler: crawler %s belum tersedia — akun %s (%s) dilewati",
                            s["pos_code"], aid, s["label"])
                continue
            prev = db.last_job_range(aid)
            if prev:
                # sambung dari job terakhir akun: mulai H+1 setelah date_to terakhir, sampai hari ini.
                # Bila sudah mencakup hari ini (date_to >= today), ulang rentang yang sama (refresh).
                start = (date.fromisoformat(prev[1]) + timedelta(days=1)).isoformat()
                d_from = min(start, today)
            else:
                d_from = yesterday  # job pertama akun: kemarin..hari ini
            branches = db.last_job_branches(aid)  # ulang pilihan cabang job terakhir; [] = auto-fetch semua
            job_id = db.create_job(aid, s["pos_code"], d_from, today, branches)
            n += 1
            log.info("scheduler: job %s dibuat (akun %s %s, rentang %s..%s)",
                     job_id, aid, s["label"], d_from, today)
        return n


def _self_check() -> None:
    from types import SimpleNamespace

    assert MIN_INTERVAL == 1 and TICK_S == 10
    t0 = datetime(2026, 11, 23, 8, 0, 0)
    last = "2026-11-23T07:00:00"
    # due(): interval 1 & 60 — belum / tepat / lewat, last None, catch-up
    assert due(t0, 1, None) is True
    assert due(t0, 60, None) is True
    assert due(datetime(2026, 11, 23, 7, 0, 59), 1, last) is False
    assert due(datetime(2026, 11, 23, 7, 1, 0), 1, last) is True
    assert due(datetime(2026, 11, 23, 7, 59, 59), 60, last) is False
    assert due(datetime(2026, 11, 23, 8, 0, 0), 60, last) is True
    assert due(t0, 60, last) is True  # lewat
    assert due(t0, 60, "2026-11-22T23:00:00") is True  # catch-up setelah app tutup

    class FakeDB:
        def __init__(self, rows, busy=(), auth=(), ranges=None, branches=None):
            self.rows, self.busy, self.auth = rows, set(busy), set(auth)
            self.ranges = ranges or {}
            self.branches = branches or {}
            self.events, self.purges = [], 0

        def list_schedules(self):
            return self.rows

        def account_busy(self, aid):
            return aid in self.busy

        def last_job_auth_failed(self, aid):
            return aid in self.auth

        def last_job_range(self, aid):
            return self.ranges.get(aid)

        def last_job_branches(self, aid):
            return self.branches.get(aid, [])

        def mark_schedule_run(self, aid, ts):
            self.events.append(("mark", aid, ts))

        def create_job(self, aid, pos, d1, d2, branches):
            self.events.append(("job", aid, pos, d1, d2, branches))
            return len(self.events)

        def purge_old_jobs(self):
            self.purges += 1
            return 0

    def row(aid=1, **kw):
        r = dict(account_id=aid, label="Toko", pos_code="demo", enabled=1,
                 interval_minutes=60, last_run_at=None)
        r.update(kw)
        return r

    global db
    real_db, real_by_code = db, crawlers.BY_CODE
    crawlers.BY_CODE = {"demo": SimpleNamespace(available=True)}
    try:
        # due + tersedia, belum ada job: tandai DULU, lalu job rentang D-1..D0
        fake = FakeDB([row()])
        db = fake
        sched = Scheduler()
        assert sched.run_due(t0) == 1
        assert fake.events == [("mark", 1, "2026-11-23T08:00:00"),
                               ("job", 1, "demo", "2026-11-22", "2026-11-23", [])]
        assert fake.purges == 1
        # job terakhir 20..21 -> sambung 22..hari ini (bukan selalu D-1)
        fake = FakeDB([row()], ranges={1: ("2026-11-20", "2026-11-21")})
        db = fake
        assert Scheduler().run_due(t0) == 1
        assert fake.events[-1] == ("job", 1, "demo", "2026-11-22", "2026-11-23", [])
        # job terakhir punya cabang tunggal -> scheduler pakai cabang itu, bukan []
        fake = FakeDB([row()], ranges={1: ("2026-11-20", "2026-11-21")},
                      branches={1: [("outlet-01", "Outlet 01")]})
        db = fake
        assert Scheduler().run_due(t0) == 1
        assert fake.events[-1] == ("job", 1, "demo", "2026-11-22", "2026-11-23", [("outlet-01", "Outlet 01")])
        # job terakhir sudah mencakup hari ini (22..23) -> ulang rentang sama (refresh)
        fake = FakeDB([row()], ranges={1: ("2026-11-22", "2026-11-23")})
        db = fake
        assert Scheduler().run_due(t0) == 1
        assert fake.events[-1] == ("job", 1, "demo", "2026-11-23", "2026-11-23", [])
        # job terakhir di masa depan (date_to > today) -> mulai today (tak mundur)
        fake = FakeDB([row()], ranges={1: ("2026-11-25", "2026-11-26")})
        db = fake
        assert Scheduler().run_due(t0) == 1
        assert fake.events[-1] == ("job", 1, "demo", "2026-11-23", "2026-11-23", [])
        # belum due / nonaktif -> tidak ada aksi
        fake = FakeDB([row(last_run_at="2026-11-23T07:30:00"), row(aid=2, enabled=0)])
        db = fake
        assert Scheduler().run_due(t0) == 0 and fake.events == []
        # saran A: busy -> skip tanpa mark
        fake = FakeDB([row()], busy=(1,))
        db = fake
        assert Scheduler().run_due(t0) == 0 and fake.events == []
        # saran C: auth failed -> skip tanpa mark
        fake = FakeDB([row()], auth=(1,))
        db = fake
        assert Scheduler().run_due(t0) == 0 and fake.events == []
        # crawler tak tersedia -> tetap ditandai (tak coba ulang tiap tick)
        fake = FakeDB([row(pos_code="belum_ada")])
        db = fake
        assert Scheduler().run_due(t0) == 0
        assert fake.events == [("mark", 1, "2026-11-23T08:00:00")]
        # saran D: purge sekali per hari
        fake = FakeDB([])
        db = fake
        sched = Scheduler()
        sched.run_due(t0)
        sched.run_due(t0 + timedelta(seconds=10))
        assert fake.purges == 1
        sched.run_due(t0 + timedelta(days=1))
        assert fake.purges == 2
    finally:
        db, crawlers.BY_CODE = real_db, real_by_code
    print("scheduler self_check ok")


if __name__ == "__main__":
    _self_check()
