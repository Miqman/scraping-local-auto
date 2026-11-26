"""FastAPI app: 4 halaman (PRD §7) + partials htmx + Demo POS lokal.
Bind hanya 127.0.0.1 (S1). Status live via polling 2 detik; DB = sumber kebenaran.
"""
import logging
import shutil
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import crawlers
from . import crypto, db, demo_pos, export, plugins, security
from .paths import STATIC_DIR, TEMPLATES_DIR, profile_dir
from .runner import MAX_ATTEMPTS, Runner
from .scheduler import MIN_INTERVAL, Scheduler

log = logging.getLogger("sla")
runner = Runner()
scheduler = Scheduler()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["pos_registry"] = crawlers.REGISTRY
templates.env.globals["app_version"] = "M0 · v0.1.0"


def _local_dt(iso: str) -> str:
    """Timestamp DB (UTC '...Z' atau lokal tanpa zona) -> waktu lokal mesin, 'YYYY-MM-DD HH:MM:SS'."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso).replace("T", " ").replace("Z", "")
    if dt.tzinfo is not None:
        dt = dt.astimezone()  # UTC -> zona waktu lokal mesin operator
    return dt.strftime("%Y-%m-%d %H:%M:%S")


templates.env.filters["local_dt"] = _local_dt

JOB_LABEL = {"queued": "Antri", "running": "Berjalan", "waiting_user": "Menunggu User",
             "done": "Selesai", "partial": "Parsial", "failed": "Gagal"}
BRANCH_LABEL = {"pending": "Antri", "running": "Berjalan", "waiting_user": "Menunggu User",
                "done": "Selesai", "failed": "Gagal"}
templates.env.globals["job_label"] = JOB_LABEL
templates.env.globals["branch_label"] = BRANCH_LABEL


def _seed_demo():
    """Buka aplikasi sudah terisi: akun demo + 3 riwayat job Demo POS (data generator sama)."""
    if db.list_accounts():
        return
    aid = db.create_account("demo", "Merchant Demo", demo_pos.DEMO_USERNAME,
                            crypto.encrypt(demo_pos.DEMO_PASSWORD))
    today = date.today()

    def mk(days_ago, dfrom, dto, specs):
        created = (today - timedelta(days=days_ago)).strftime("%Y-%m-%dT08:00:00Z")
        jid = db.create_job(aid, "demo", dfrom.isoformat(), dto.isoformat(),
                            [(k, demo_pos.BRANCH_NAMES[k]) for k, _, _, _ in specs])
        branches = db.get_branches(jid)
        for br, (key, status, attempts, err) in zip(branches, specs):
            if status == "done":
                rows = []
                cur = dfrom
                while cur <= dto:
                    rows.extend(demo_pos.gen_rows(key, cur.isoformat()))
                    cur += timedelta(days=1)
                db.upsert_transactions(br["id"], rows)
            db.set_branch_status(br["id"], status, attempts=attempts, error=err,
                                 trx_count=db.get_branch(br["id"])["trx_count"])
        db.refresh_job_status(jid)
        with db.conn() as c:
            c.execute("UPDATE jobs SET created_at=?, finished_at=? WHERE id=?",
                      (created, created, jid))

    mk(3, today - timedelta(days=4), today - timedelta(days=3),
       [("demo-pusat", "done", 1, None), ("demo-mall", "done", 1, None)])
    mk(2, today - timedelta(days=2), today - timedelta(days=2),
       [("demo-pusat", "done", 1, None),
        ("demo-bandara", "failed", 3, "[network] Server POS membalas HTTP 500 (gangguan sesaat)")])
    mk(1, today - timedelta(days=1), today - timedelta(days=1),
       [("demo-pusat", "failed", 1, "[auth] Kredensial ditolak POS — perbarui password di Akun Merchant")])
    log.info("seed demo: akun + 3 job riwayat")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init()
    n = db.recover_interrupted()
    if n:
        log.info("recovery E5: %d job aktif ditandai failed [interrupted]", n)
    p = db.purge_old_jobs()
    if p:
        log.info("retensi E18: %d job >90 hari dihapus", p)
    _seed_demo()
    plugins.load_all()  # POS import (data/plugins) aktif lagi tanpa update aplikasi
    await runner.start()
    await scheduler.start()
    yield
    await scheduler.stop()
    await runner.stop()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(security.SecurityMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(demo_pos.router)


def render(request: Request, name: str, status_code: int = 200, headers: dict = None, **kw):
    base = {"csrf": security.csrf_token(request), "keyfile_warning": crypto.using_keyfile}
    base.update(kw)
    return templates.TemplateResponse(request, name, base, status_code=status_code, headers=headers)


def hx_trigger(response_headers: dict, payload: dict):
    import json
    response_headers["HX-Trigger"] = json.dumps(payload)


# ================= Dashboard =================

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return render(request, "dashboard.html", stats=db.job_stats(),
                  jobs=db.list_jobs(), active_page="dashboard",
                  filters={"q": "", "status": "", "pos": "", "date_from": "",
                           "date_to": "", "sort": "id", "dir": "desc", "view": "latest"})


@app.get("/partials/stats", response_class=HTMLResponse)
def stats_partial(request: Request):
    view = request.query_params.get("view", "latest")
    return render(request, "_stats.html", stats=db.job_stats(latest=view != "all"), view=view)


@app.get("/partials/jobs", response_class=HTMLResponse)
def jobs_partial(request: Request):
    p = request.query_params
    jobs = db.list_jobs(q=p.get("q", ""), status=p.get("status", ""), pos=p.get("pos", ""),
                        date_from=p.get("date_from", ""), date_to=p.get("date_to", ""),
                        sort=p.get("sort", "id"), direction=p.get("dir", "desc"),
                        page=int(p.get("page", "1") or 1),
                        latest=p.get("view", "latest") != "all")
    return render(request, "_jobs_table.html", jobs=jobs,
                  filters={"q": p.get("q", ""), "status": p.get("status", ""),
                           "pos": p.get("pos", ""), "date_from": p.get("date_from", ""),
                           "date_to": p.get("date_to", ""), "sort": p.get("sort", "id"),
                           "dir": p.get("dir", "desc"), "view": p.get("view", "latest")})


# ================= Job Baru =================

def _job_form_ctx(request: Request, **kw):
    form = kw.get("form") or {}
    all_accounts = db.list_accounts()
    sel_acc = int(form.get("account_id") or 0)
    account = db.get_account(sel_acc) if sel_acc else None
    if account is not None:
        selected_pos = account["pos_code"]
    else:
        # default: POS available pertama yang punya akun (bukan selalu demo)
        owned = {a["pos_code"] for a in all_accounts}
        selected_pos = next((p.code for p in crawlers.REGISTRY if p.available and p.code in owned), "")
        if not selected_pos:
            selected_pos = next((p.code for p in crawlers.REGISTRY if p.available), "")
    if sel_acc:
        rng = db.last_job_range(sel_acc)
        default_from, default_to = rng if rng else (date.today().isoformat(), date.today().isoformat())
    else:
        default_from = default_to = (date.today() - timedelta(days=1)).isoformat()
    base = {"pos_list": crawlers.REGISTRY,
            "accounts": [a for a in all_accounts if a["pos_code"] == selected_pos],
            "selected_pos": selected_pos, "selected_account_id": sel_acc,
            "known": db.known_branches(sel_acc) if sel_acc else [],
            "default_from": default_from, "default_to": default_to,
            "active_page": "new-job"}
    base.update(kw)
    return base


@app.get("/jobs/new", response_class=HTMLResponse)
def job_new(request: Request):
    return render(request, "job_new.html", **_job_form_ctx(request))


@app.get("/partials/account-options", response_class=HTMLResponse)
def account_options(request: Request, pos: str = ""):
    accounts = [a for a in db.list_accounts() if a["pos_code"] == pos]
    return render(request, "_account_options.html", accounts=accounts)


@app.get("/partials/branch-candidates", response_class=HTMLResponse)
def branch_candidates(request: Request, account_id: int = 0):
    known = db.known_branches(account_id) if account_id else []
    return render(request, "_branch_candidates.html", known=known, account_id=account_id)


@app.get("/partials/job-dates")
def job_dates(request: Request, account_id: int = 0):
    """Rentang default utk akun: sambung job terakhir (date_to+1 .. hari ini); belum ada job = kemarin."""
    today = date.today()
    rng = db.last_job_range(account_id) if account_id else None
    if rng:
        d_from = min((date.fromisoformat(rng[1]) + timedelta(days=1)).isoformat(), today.isoformat())
    else:
        d_from = (today - timedelta(days=1)).isoformat()
    return JSONResponse({"date_from": d_from, "date_to": today.isoformat(),
                         "last_from": rng[0] if rng else None, "last_to": rng[1] if rng else None})


@app.post("/jobs", response_class=HTMLResponse)
async def create_job(request: Request):
    form = await request.form()
    try:
        account_id = int(form.get("account_id") or 0)
    except (TypeError, ValueError):
        account_id = 0
    date_from = str(form.get("date_from", ""))
    date_to = str(form.get("date_to", ""))
    raw_branches = form.getlist("branches")
    account = db.get_account(account_id)
    error = None
    if account is None:
        error = "Pilih akun merchant dulu."
    elif not getattr(crawlers.BY_CODE.get(account["pos_code"]), "available", False):
        error = f"Crawler {account['pos_code']} belum tersedia (M1)."
    else:
        try:
            a, b = date.fromisoformat(date_from), date.fromisoformat(date_to)
            if b < a:
                error = "Tanggal akhir sebelum tanggal awal."
            elif (b - a).days > 366:
                error = "Rentang maksimal 1 tahun per job."
        except ValueError:
            error = "Rentang tanggal tidak valid."
    if error:
        form_state = {"account_id": account_id, "date_from": date_from,
                      "date_to": date_to, "branches": raw_branches}
        return render(request, "_job_form.html", status_code=422,
                      error=error, form=form_state, **_job_form_ctx(request, form=form_state))
    branches = []
    for raw in raw_branches:
        if "|" in str(raw):
            k, n = str(raw).split("|", 1)
            if k.strip():
                branches.append((k.strip(), n.strip() or k.strip()))
    job_id = db.create_job(account_id, account["pos_code"], date_from, date_to, branches)
    resp = PlainTextResponse("", status_code=200)
    resp.headers["HX-Redirect"] = f"/jobs/{job_id}"
    return resp


# ================= Detail Job =================

def _live_ctx(request: Request, job_id: int, preview_page: int = 1):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job tidak ditemukan")
    branches = db.get_branches(job_id)
    done_count = sum(1 for b in branches if b["status"] == "done")
    failed_retryable = [b for b in branches if b["status"] == "failed" and b["attempts"] < MAX_ATTEMPTS]
    waiting = [b for b in branches if b["status"] == "waiting_user"]
    return {"job": job, "branches": branches, "done_count": done_count,
            "failed_retryable": failed_retryable, "waiting_branches": waiting,
            "can_retry_job": job["status"] == "failed" and (bool(failed_retryable) or not branches),
            "job_waiting": job["status"] == "waiting_user" and not waiting,
            "preview": db.preview_transactions(job_id, page=preview_page),
            "trx_total": db.count_transactions(job_id),
            "active": job["status"] in ("queued", "running", "waiting_user"),
            "active_page": "dashboard"}


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int):
    return render(request, "job_detail.html", **_live_ctx(request, job_id))


@app.get("/partials/jobs/{job_id}", response_class=HTMLResponse)
def job_live_partial(request: Request, job_id: int, preview_page: int = 1):
    return render(request, "_job_live.html", **_live_ctx(request, job_id, preview_page))


@app.post("/jobs/{job_id}/resume")
def resume_job(request: Request, job_id: int):
    ok = runner.signal_resume(-job_id)
    headers = {}
    hx_trigger(headers, {"toast": {"msg": "Lanjut — menunggu konfirmasi halaman POS" if ok
                                   else "Tidak ada gate yang menunggu di job ini",
                                   "kind": "info" if ok else "error"}})
    return PlainTextResponse("", status_code=204, headers=headers)


@app.post("/jobs/{job_id}/branches/{branch_id}/resume")
def resume_branch(request: Request, job_id: int, branch_id: int):
    ok = runner.signal_resume(branch_id)
    headers = {}
    hx_trigger(headers, {"toast": {"msg": "Lanjut — menunggu konfirmasi halaman POS" if ok
                                   else "Cabang ini tidak sedang menunggu user",
                                   "kind": "info" if ok else "error"}})
    return PlainTextResponse("", status_code=204, headers=headers)


def _retry_branches(job_id: int, branch_ids: list) -> int:
    n = 0
    for bid in branch_ids:
        br = db.get_branch(bid)
        if br is None or br["job_id"] != job_id:
            continue
        if br["status"] != "failed" or br["attempts"] >= MAX_ATTEMPTS:
            continue
        db.set_branch_status(bid, "pending", error="")
        n += 1
    if n:
        db.set_job_status(job_id, "queued")
    return n


@app.post("/jobs/{job_id}/retry-all", response_class=HTMLResponse)
def retry_all(request: Request, job_id: int):
    branches = db.get_branches(job_id)
    ids = [b["id"] for b in branches if b["status"] == "failed" and b["attempts"] < MAX_ATTEMPTS]
    n = _retry_branches(job_id, ids)
    if not n and not branches and db.get_job(job_id)["status"] == "failed":
        db.set_job_status(job_id, "queued")  # job gagal sebelum cabang sempat di-fetch
        n = 1
    headers = {}
    hx_trigger(headers, {"toast": {"msg": f"{n} cabang dijadwalkan ulang" if n
                                   else "Tidak ada cabang gagal yang bisa di-retry (maks 3 attempt)",
                                   "kind": "success" if n else "error"}})
    return render(request, "_job_live.html", headers=headers, **_live_ctx(request, job_id))


@app.post("/jobs/{job_id}/branches/{branch_id}/retry", response_class=HTMLResponse)
def retry_branch(request: Request, job_id: int, branch_id: int):
    n = _retry_branches(job_id, [branch_id])
    headers = {}
    hx_trigger(headers, {"toast": {"msg": "Cabang dijadwalkan ulang" if n
                                   else "Cabang tidak bisa di-retry (status/maks 3 attempt)",
                                   "kind": "success" if n else "error"}})
    return render(request, "_job_live.html", headers=headers, **_live_ctx(request, job_id))


@app.get("/jobs/{job_id}/export")
def export_excel(request: Request, job_id: int):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job tidak ditemukan")
    branches = db.get_branches(job_id)
    if not any(b["status"] == "done" for b in branches):
        raise HTTPException(409, "Export aktif setelah minimal satu cabang selesai (E12)")
    path = export.export_job(job_id)
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/jobs/{job_id}/errors.txt", response_class=PlainTextResponse)
def job_errors(request: Request, job_id: int):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job tidak ditemukan")
    lines = [f"Job #{job_id} · {job['pos_code']} · {job['account_label']}",
             f"Rentang: {job['date_from']} s/d {job['date_to']} · Status: {job['status']}", ""]
    for br in db.get_branches(job_id):
        if br["error"]:
            lines.append(f"Cabang {br['branch_name']} ({br['branch_key']}) · attempt {br['attempts']}/{MAX_ATTEMPTS}")
            lines.append(crypto.redact(br["error"]))
            lines.append("")
    return PlainTextResponse("\n".join(lines) or "Tidak ada error tercatat.")


@app.get("/jobs/{job_id}/delete-confirm", response_class=HTMLResponse)
def delete_job_confirm(request: Request, job_id: int):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404)
    return render(request, "_delete_job_modal.html", job=job)


@app.post("/jobs/{job_id}/delete")
def delete_job(request: Request, job_id: int):
    db.delete_job(job_id)  # CASCADE branches + transactions
    resp = PlainTextResponse("", status_code=200)
    resp.headers["HX-Redirect"] = "/"
    return resp


# ================= Akun Merchant =================

@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request):
    return render(request, "accounts.html", accounts=db.list_accounts(),
                  active_page="accounts")


def _filter_schedules(rows, q: str):
    q = (q or "").strip().lower()
    if not q:
        return rows
    return [s for s in rows if q in s["label"].lower() or q in s["username"].lower()
            or q in s["pos_code"].lower()]


@app.get("/schedules", response_class=HTMLResponse)
def schedules_page(request: Request):
    q = request.query_params.get("q", "")
    return render(request, "schedules.html", schedules=_filter_schedules(db.list_schedules(), q),
                  open_pos="", q=q, active_page="schedules")


@app.get("/partials/accounts", response_class=HTMLResponse)
def accounts_partial(request: Request):
    return render(request, "_accounts_table.html", accounts=db.list_accounts())


@app.get("/partials/schedules", response_class=HTMLResponse)
def schedules_partial(request: Request):
    q = request.query_params.get("q", "")
    return render(request, "_schedules.html", schedules=_filter_schedules(db.list_schedules(), q),
                  open_pos="", q=q)


@app.post("/schedules/bulk", response_class=HTMLResponse)
async def bulk_schedule(request: Request):
    form = await request.form()
    scope = str(form.get("scope", "all")).strip()
    enabled = form.get("enabled") == "1"
    headers = {}
    pos_code = None if scope == "all" else scope
    if pos_code is not None and pos_code not in crawlers.BY_CODE:
        hx_trigger(headers, {"toast": {"msg": f"POS {scope} tidak dikenal", "kind": "error"}})
        return PlainTextResponse("", status_code=422, headers=headers)
    n = db.bulk_set_schedule(enabled, pos_code)
    label = "semua merchant" if pos_code is None else crawlers.BY_CODE[pos_code].name
    hx_trigger(headers, {"toast": {"msg": f"Jadwal {label} {'diaktifkan' if enabled else 'dinonaktifkan'} ({n} akun)",
                                   "kind": "success"}, "refreshSchedules": True})
    return render(request, "_schedules.html", schedules=db.list_schedules(), open_pos=pos_code or "", q="", headers=headers)


@app.post("/accounts/{account_id}/schedule", response_class=HTMLResponse)
async def save_schedule(request: Request, account_id: int):
    account = db.get_account(account_id)
    if account is None:
        raise HTTPException(404)
    form = await request.form()
    try:
        interval = int(str(form.get("interval_minutes", "")).strip())
    except ValueError:
        interval = 0
    enabled = form.get("enabled") == "1"
    open_pos = str(form.get("open_pos", "")).strip()
    headers = {}
    if interval < MIN_INTERVAL:
        hx_trigger(headers, {"toast": {"msg": "Interval minimal 1 menit", "kind": "error"}})
        return PlainTextResponse("", status_code=422, headers=headers)
    db.upsert_schedule(account_id, enabled, interval)
    hx_trigger(headers, {"toast": {"msg": f"Jadwal {'diaktifkan' if enabled else 'dinonaktifkan'} — tiap {interval} menit, rentang sama dengan job terakhir",
                                   "kind": "success"}, "refreshSchedules": True})
    return render(request, "_schedules.html", schedules=db.list_schedules(),
                  open_pos=open_pos or account["pos_code"], q="", headers=headers)


@app.get("/accounts/modal", response_class=HTMLResponse)
def account_modal(request: Request, id: int = 0, pos: str = ""):
    account = db.get_account(id) if id else None
    return render(request, "_account_modal.html", account=account,
                  pos_code=account["pos_code"] if account else pos,
                  pos_list=crawlers.REGISTRY)


@app.post("/accounts")
async def save_account(request: Request):
    form = await request.form()
    aid = int(form.get("id") or 0)
    pos_code = str(form.get("pos_code", ""))
    label = str(form.get("label", "")).strip()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    headers = {}
    if pos_code not in crawlers.BY_CODE or not label or not username or (not aid and not password):
        hx_trigger(headers, {"toast": {"msg": "Lengkapi POS, label, username" +
                                       (" dan password" if not aid else ""), "kind": "error"}})
        return PlainTextResponse("", status_code=422, headers=headers)
    if aid:
        acc = db.get_account(aid)
        if acc is None:
            raise HTTPException(404)
        if username != acc["username"]:
            if db.account_has_jobs(aid):
                hx_trigger(headers, {"toast": {"msg": "Username tidak bisa diubah selama akun punya job: "
                                               "sesi lama akan terpakai. Hapus job-nya dulu, atau buat akun baru.",
                                               "kind": "error"}, "closeModal": True})
                return PlainTextResponse("", status_code=409, headers=headers)
            shutil.rmtree(profile_dir(aid), ignore_errors=True)  # sesi profil milik username lama
        enc = crypto.encrypt(password) if password else None
        db.update_account(aid, label, username, enc)
        msg = "Akun diperbarui" + (" (password baru tersimpan terenkripsi)" if password else "")
    else:
        db.create_account(pos_code, label, username, crypto.encrypt(password))
        msg = "Akun tersimpan terenkripsi (AES-256-GCM)"
    hx_trigger(headers, {"toast": {"msg": msg, "kind": "success"},
                         "closeModal": True, "refreshAccounts": True, "refreshSchedules": True})
    return PlainTextResponse("", status_code=204, headers=headers)


@app.get("/accounts/{account_id}/delete-confirm", response_class=HTMLResponse)
def delete_account_confirm(request: Request, account_id: int):
    acc = db.get_account(account_id)
    if acc is None:
        raise HTTPException(404)
    return render(request, "_delete_account_modal.html", account=acc,
                  has_jobs=db.account_has_jobs(account_id))


@app.post("/accounts/{account_id}/delete")
def delete_account(request: Request, account_id: int):
    headers = {}
    if db.account_has_jobs(account_id):
        hx_trigger(headers, {"toast": {"msg": "Ditolak (E17): akun masih punya job. Hapus job-nya dulu.",
                                       "kind": "error"}, "closeModal": True})
        return PlainTextResponse("", status_code=409, headers=headers)
    db.delete_account(account_id)
    shutil.rmtree(profile_dir(account_id), ignore_errors=True)  # S6: profil browser ikut hilang
    hx_trigger(headers, {"toast": {"msg": "Akun + profil browser dihapus", "kind": "success"},
                         "closeModal": True, "refreshAccounts": True, "refreshSchedules": True})
    return PlainTextResponse("", status_code=204, headers=headers)


# ================= Import POS (plugin manual) =================

def _plugins_ctx():
    return {"plugins": plugins.list_plugins(), "builtin_codes": [
        p.code for p in crawlers.REGISTRY if p.milestone != "import"]}


@app.get("/plugins", response_class=HTMLResponse)
def plugins_page(request: Request):
    return render(request, "plugins.html", active_page="plugins", **_plugins_ctx())


@app.post("/plugins/import", response_class=HTMLResponse)
async def import_plugin(request: Request):
    form = await request.form()
    upload = form.get("file")
    headers = {}
    data = await upload.read() if upload is not None and hasattr(upload, "read") else b""
    try:
        entry = plugins.import_bytes(data)
    except ValueError as exc:
        # 200 + toast error: htmx tidak swap non-2xx; pola penolakan rumah = save_schedule
        hx_trigger(headers, {"toast": {"msg": str(exc), "kind": "error"}, "refreshPlugins": True})
        return render(request, "_plugins_table.html", headers=headers, **_plugins_ctx())
    hx_trigger(headers, {"toast": {"msg": f"POS {entry['name']} ({entry['code']}) siap dipakai — "
                                          "buat akun merchant untuk POS ini", "kind": "success"},
                         "refreshPlugins": True})
    return render(request, "_plugins_table.html", headers=headers, **_plugins_ctx())


@app.post("/plugins/{code}/delete", response_class=HTMLResponse)
def delete_plugin(request: Request, code: str):
    headers = {}
    if not plugins.delete(code):
        raise HTTPException(404)
    hx_trigger(headers, {"toast": {"msg": f"POS {code} dihapus — akun terkait tidak bisa job baru "
                                          "sampai file diimport lagi", "kind": "success"},
                         "refreshPlugins": True})
    return render(request, "_plugins_table.html", headers=headers, **_plugins_ctx())


@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.headers.get("hx-request"):
        return PlainTextResponse("Tidak ditemukan", status_code=404)
    return render(request, "error.html", status_code=404,
                  message="Halaman atau data tidak ditemukan.", active_page="")