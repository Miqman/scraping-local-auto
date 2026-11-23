"""T07: job runner asyncio in-process. Status DB = sumber kebenaran.
E6 lock per akun (antri), E7 maks 2 paralel, E3 re-login <=2, S7 attempt <=3,
E4 auth tanpa auto-retry. Browser sync jalan di ThreadPoolExecutor(2).
"""
import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from app import browser, crypto, db
from app.errors import CrawlError
from crawlers import get_crawler
from crawlers.base import CrawlContext

log = logging.getLogger("sla.runner")

MAX_PARALLEL = 2
MAX_ATTEMPTS = 3
MAX_RELOGINS = 2


class Runner:
    def __init__(self):
        self._events = {}
        self._executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="job")
        self._futures = {}
        self._task = None
        self._loop = None
        self._stop = False

    # ---------- sinyal fallback "Sudah selesai" ----------
    def resume_event(self, key: int) -> threading.Event:
        return self._events.setdefault(key, threading.Event())

    def signal_resume(self, key: int) -> bool:
        ev = self._events.get(key)
        if ev is None:
            return False
        ev.set()
        return True

    # ---------- siklus hidup ----------
    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._task = self._loop.create_task(self._tick_loop())

    async def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _tick_loop(self):
        while not self._stop:
            await asyncio.sleep(0.5)
            try:
                self._dispatch()
            except Exception:
                log.exception("dispatch error")

    def _dispatch(self):
        for jid, fut in list(self._futures.items()):
            if fut.done():
                self._futures.pop(jid)
                try:
                    fut.result()
                except Exception:
                    log.exception("job %s crashed", jid)
        active = db.active_jobs()
        busy = {j["account_id"] for j in active}
        slots = MAX_PARALLEL - len(active)
        for job in db.queued_jobs(busy, slots):
            db.set_job_status(job["id"], "running")
            fut = self._loop.run_in_executor(self._executor, run_job, job["id"], self)
            self._futures[job["id"]] = fut
            log.info("job %s mulai (pos=%s akun=%s)", job["id"], job["pos_code"], job["account_id"])


def _fail_remaining(job_id: int, code: str, message: str, attempts: int = 1):
    from app.crypto import redact
    msg = f"[{code}] {redact(message)}"
    for br in db.get_branches(job_id):
        if br["status"] != "done":
            db.set_branch_status(br["id"], "failed", error=msg,
                                 attempts=max(br["attempts"], attempts))
    db.refresh_job_status(job_id)


def _login(crawler, ctx, username, password):
    crawler.login(ctx, username, password)
    ctx.check_gate()


def run_job(job_id: int, runner: Runner) -> None:
    job = db.get_job(job_id)
    if job is None:
        return
    account = db.get_account(job["account_id"])
    if account is None:
        _fail_remaining(job_id, "unknown", "Akun merchant sudah dihapus")
        return
    try:
        crawler = get_crawler(job["pos_code"])
    except KeyError as exc:
        _fail_remaining(job_id, "unknown", str(exc))
        return
    try:
        password = crypto.decrypt(account["password_enc"])
    except Exception as exc:
        _fail_remaining(job_id, "unknown", f"Kredensial tidak bisa didekripsi: {exc}")
        return

    try:
        with browser.Session(account["id"]) as sess:
            ctx = CrawlContext(sess, runner, job_id, gate_fn=getattr(crawler, "gate_active", None),
                               gate_assist=getattr(crawler, "gate_assist", None))
            try:
                _login(crawler, ctx, account["username"], password)
            except CrawlError as exc:
                _fail_remaining(job_id, exc.code, str(exc))
                return
            branches = db.get_branches(job_id)
            if not branches:
                # PRD §6: cabang di-fetch otomatis setelah login pertama
                try:
                    fetched = crawler.list_branches(ctx)
                except CrawlError as exc:
                    _fail_remaining(job_id, exc.code, str(exc))
                    return
                if not fetched:
                    _fail_remaining(job_id, "ui_changed", "POS mengembalikan daftar cabang kosong")
                    return
                db.insert_branches(job_id, fetched)
                branches = db.get_branches(job_id)
            for br in branches:
                if br["status"] == "done":
                    continue  # W3: hasil sukses tidak diulang
                if not sess.alive:
                    _fail_remaining(job_id, "browser", "Browser ditutup atau crash di tengah job")
                    return
                _run_branch(crawler, ctx, job, br, account["username"], password)
    except browser.BrowserDead as exc:
        _fail_remaining(job_id, "browser", str(exc))
    except CrawlError as exc:
        _fail_remaining(job_id, exc.code, str(exc))
    except Exception as exc:
        log.exception("job %s error tak terduga", job_id)
        _fail_remaining(job_id, "unknown", crypto.redact_exc(exc))
    finally:
        status = db.refresh_job_status(job_id)
        if status in ("running", "waiting_user"):
            # jaring pengaman: eksekusi berakhir tapi cabang masih aktif -> jangan zombie
            for br in db.get_branches(job_id):
                if br["status"] in db.BRANCH_ACTIVE:
                    db.set_branch_status(
                        br["id"], "failed", attempts=max(br["attempts"], 1),
                        error="[interrupted] Job berhenti sebelum cabang selesai; retry untuk melanjutkan.")
            db.refresh_job_status(job_id)
        if db.get_job(job_id)["status"] in db.JOB_ACTIVE and not db.get_branches(job_id):
            db.set_job_status(job_id, "failed")  # gagal sebelum cabang ada: jangan zombie
        log.info("job %s selesai: %s", job_id, db.get_job(job_id)["status"])


def _run_branch(crawler, ctx, job, br, username, password):
    bid = br["id"]
    ctx.branch_id = bid
    attempts = br["attempts"]
    relogins = 0
    error = None
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        db.set_branch_status(bid, "running", attempts=attempts, error="", executing=True)
        try:
            for batch in crawler.scrape_branch(ctx, br["branch_key"], job["date_from"], job["date_to"]):
                db.upsert_transactions(bid, batch)  # commit per batch; dedup per akun lintas job
            count = db.get_branch(bid)["trx_count"]
            db.set_branch_status(bid, "done", trx_count=count, error="", executing=True)
            return
        except CrawlError as exc:
            error = exc
            if exc.code == "auth":
                break  # E4/S7: tanpa auto-retry, cegah lockout
            if exc.code in ("user_timeout", "ui_changed"):
                break  # E1: cabang gagal, job lanjut; ui_changed: lapor developer
            if exc.code == "browser":
                raise browser.BrowserDead(str(exc))
            if exc.code == "network" and relogins < MAX_RELOGINS and attempts < MAX_ATTEMPTS:
                relogins += 1  # E3: sesi putus (IP hotspot berubah) -> auto re-login
                try:
                    _login(crawler, ctx, username, password)
                    continue
                except CrawlError as lexc:
                    error = lexc
                    break
            if attempts < MAX_ATTEMPTS:
                continue
            break
        except browser.BrowserDead:
            raise
        except Exception as exc:
            wrapped = browser._wrap_pw(exc)
            if isinstance(wrapped, browser.BrowserDead):
                raise wrapped from exc  # E2: driver mati di tengah operasi crawler
            error = CrawlError("unknown", crypto.redact_exc(exc))
            break
    db.set_branch_status(bid, "failed", attempts=attempts, executing=True,
                         error=crypto.redact(error.labeled() if error else "[unknown] Gagal tanpa detail"))