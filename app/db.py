"""SQLite WAL — sumber kebenaran status (PRD §9). Koneksi per operasi."""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from .paths import db_path

_local = threading.local()
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pos_code TEXT NOT NULL,
    label TEXT NOT NULL,
    username TEXT NOT NULL,
    password_enc BLOB NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    pos_code TEXT NOT NULL,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS job_branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    branch_key TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    trx_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    job_branch_id INTEGER NOT NULL REFERENCES job_branches(id) ON DELETE CASCADE,
    trx_id TEXT NOT NULL,
    trx_date TEXT NOT NULL,
    grand_total REAL,
    total_price REAL,
    total_tax REAL,
    raw_json TEXT NOT NULL,
    UNIQUE(account_id, trx_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_branches_job ON job_branches(job_id);
CREATE INDEX IF NOT EXISTS idx_trx_branch ON transactions(job_branch_id);
"""

JOB_ACTIVE = ("queued", "running", "waiting_user")
BRANCH_ACTIVE = ("pending", "running", "waiting_user")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def conn():
    """Koneksi per operasi; WAL mengizinkan baca saat tulis."""
    c = sqlite3.connect(db_path(), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        # Token sesi per akun (padanan langkah wajib "token ke DB cartenz" di referensi Java Majoo)
        c.execute("CREATE TABLE IF NOT EXISTS sessions ("
                  "account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,"
                  "token_enc BLOB NOT NULL, updated_at TEXT NOT NULL)")
        # Jadwal interval menit per akun (hanya berjalan saat aplikasi terbuka)
        c.execute("CREATE TABLE IF NOT EXISTS schedules ("
                  "account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,"
                  "enabled INTEGER NOT NULL DEFAULT 0,"
                  "interval_minutes INTEGER NOT NULL DEFAULT 60,"
                  "last_run_at TEXT,"
                  "updated_at TEXT NOT NULL)")
        # Migrasi idempoten: jadwal harian lama (run_at) -> interval 1440 menit
        cols = [r["name"] for r in c.execute("PRAGMA table_info(schedules)")]
        if "run_at" in cols:
            c.execute("CREATE TABLE schedules_new ("
                      "account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,"
                      "enabled INTEGER NOT NULL DEFAULT 0,"
                      "interval_minutes INTEGER NOT NULL DEFAULT 60,"
                      "last_run_at TEXT,"
                      "updated_at TEXT NOT NULL)")
            c.execute("INSERT INTO schedules_new(account_id,enabled,interval_minutes,last_run_at,updated_at) "
                      "SELECT account_id, enabled, 1440,"
                      " CASE WHEN last_run_date IS NOT NULL THEN last_run_date || 'T' || run_at || ':00' END,"
                      " updated_at FROM schedules")
            c.execute("DROP TABLE schedules")
            c.execute("ALTER TABLE schedules_new RENAME TO schedules")
        # Migrasi idempoten: dedup transaksi per akun (lintas job), bukan per cabang.
        # Baris lama tanpa account_id -> backfill dari job; duplikat (account,trx_id)
        # dibuang mempertahankan versi terbaru (job_branch_id terbesar = run terakhir).
        tcols = [r["name"] for r in c.execute("PRAGMA table_info(transactions)")]
        if "account_id" not in tcols:
            c.execute("CREATE TABLE transactions_new ("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                      "account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,"
                      "job_branch_id INTEGER NOT NULL REFERENCES job_branches(id) ON DELETE CASCADE,"
                      "trx_id TEXT NOT NULL, trx_date TEXT NOT NULL,"
                      "grand_total REAL, total_price REAL, total_tax REAL,"
                      "raw_json TEXT NOT NULL, UNIQUE(account_id, trx_id))")
            c.execute("INSERT INTO transactions_new(account_id,job_branch_id,trx_id,trx_date,"
                      "grand_total,total_price,total_tax,raw_json) "
                      "SELECT j.account_id, t.job_branch_id, t.trx_id, t.trx_date,"
                      " t.grand_total, t.total_price, t.total_tax, t.raw_json "
                      "FROM transactions t JOIN job_branches b ON b.id=t.job_branch_id "
                      "JOIN jobs j ON j.id=b.job_id "
                      "WHERE t.id IN (SELECT MAX(t2.id) FROM transactions t2 "
                      " JOIN job_branches b2 ON b2.id=t2.job_branch_id "
                      " JOIN jobs j2 ON j2.id=b2.job_id "
                      " GROUP BY j2.account_id, t2.trx_id)")
            c.execute("DROP TABLE transactions")
            c.execute("ALTER TABLE transactions_new RENAME TO transactions")
            c.execute("CREATE INDEX IF NOT EXISTS idx_trx_branch ON transactions(job_branch_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_trx_account ON transactions(account_id)")
            c.execute("UPDATE job_branches SET trx_count=(SELECT COUNT(*) FROM transactions "
                      "WHERE job_branch_id=job_branches.id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_trx_account ON transactions(account_id)")


def recover_interrupted() -> int:
    """E5: job/cabang aktif saat proses mati -> failed [interrupted]. Data committed tetap utuh."""
    ts = now()
    with conn() as c:
        jobs = c.execute(
            "SELECT id FROM jobs WHERE status IN ('queued','running','waiting_user')"
        ).fetchall()
        for j in jobs:
            c.execute(
                "UPDATE job_branches SET status='failed', error='[interrupted] Aplikasi tertutup saat job berjalan; data cabang selesai tetap tersimpan. Retry untuk melanjutkan.', updated_at=? "
                "WHERE job_id=? AND status IN ('pending','running','waiting_user')",
                (ts, j["id"]),
            )
            c.execute(
                "UPDATE jobs SET status='failed', finished_at=? WHERE id=?", (ts, j["id"])
            )
        return len(jobs)


def purge_old_jobs(days: int = 90) -> int:
    """E18: retensi 90 hari, cascade. Akun tidak pernah dihapus otomatis."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with conn() as c:
        cur = c.execute("DELETE FROM jobs WHERE created_at < ?", (cutoff,))
        return cur.rowcount


# ---------- accounts ----------

def create_account(pos_code: str, label: str, username: str, password_enc: bytes) -> int:
    ts = now()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO accounts(pos_code,label,username,password_enc,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (pos_code, label, username, password_enc, ts, ts),
        )
        return cur.lastrowid


def update_account(account_id: int, label: str, username: str,
                   password_enc: Optional[bytes] = None) -> None:
    ts = now()
    with conn() as c:
        if password_enc is not None:
            c.execute(
                "UPDATE accounts SET label=?, username=?, password_enc=?, updated_at=? WHERE id=?",
                (label, username, password_enc, ts, account_id),
            )
        else:
            c.execute(
                "UPDATE accounts SET label=?, username=?, updated_at=? WHERE id=?",
                (label, username, ts, account_id),
            )


def get_account(account_id: int) -> Optional[sqlite3.Row]:
    with conn() as c:
        return c.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()


def list_accounts() -> list:
    with conn() as c:
        return c.execute("SELECT * FROM accounts ORDER BY pos_code, label").fetchall()


def account_has_jobs(account_id: int) -> bool:
    with conn() as c:
        r = c.execute("SELECT 1 FROM jobs WHERE account_id=? LIMIT 1", (account_id,)).fetchone()
        return r is not None


def delete_account(account_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM accounts WHERE id=?", (account_id,))


# ---------- jobs ----------

def create_job(account_id: int, pos_code: str, date_from: str, date_to: str,
               branches: list) -> int:
    """branches: list (key, name); kosong = auto-fetch setelah login (PRD §6, E14)."""
    ts = now()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO jobs(account_id,pos_code,date_from,date_to,status,created_at) VALUES(?,?,?,?, 'queued', ?)",
            (account_id, pos_code, date_from, date_to, ts),
        )
        job_id = cur.lastrowid
        for key, name in branches:
            c.execute(
                "INSERT INTO job_branches(job_id,branch_key,branch_name,status,updated_at) VALUES(?,?,?,'pending',?)",
                (job_id, key, name, ts),
            )
        return job_id


def get_job(job_id: int) -> Optional[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT j.*, a.label AS account_label, a.username AS account_username "
            "FROM jobs j JOIN accounts a ON a.id=j.account_id WHERE j.id=?",
            (job_id,),
        ).fetchone()


def get_branches(job_id: int) -> list:
    with conn() as c:
        return c.execute(
            "SELECT * FROM job_branches WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()


def get_branch(branch_id: int) -> Optional[sqlite3.Row]:
    with conn() as c:
        return c.execute("SELECT * FROM job_branches WHERE id=?", (branch_id,)).fetchone()


def set_job_status(job_id: int, status: str) -> None:
    ts = now()
    finished = ts if status in ("done", "partial", "failed") else None
    with conn() as c:
        c.execute(
            "UPDATE jobs SET status=?, finished_at=? WHERE id=?",
            (status, finished, job_id),
        )


def refresh_job_status(job_id: int, executing: bool = False) -> str:
    """Status job diturunkan dari cabang; DB = sumber kebenaran.
    executing=True: eksekusi masih hidup (teardown) -> status terminal ditahan
    agar retry tidak balapan dengan finally run_job."""
    with conn() as c:
        rows = c.execute(
            "SELECT status FROM job_branches WHERE job_id=?", (job_id,)
        ).fetchall()
        statuses = [r["status"] for r in rows]
        if not statuses:
            return "queued"  # menunggu auto-fetch cabang
        if "running" in statuses or "pending" in statuses:
            status = "running"
        elif "waiting_user" in statuses:
            status = "waiting_user"
        elif all(s == "done" for s in statuses):
            status = "done"
        elif all(s == "failed" for s in statuses):
            status = "failed"
        else:
            status = "partial"
        if executing and status in ("done", "partial", "failed"):
            status = "running"
        ts = now()
        finished = ts if status in ("done", "partial", "failed") else None
        c.execute(
            "UPDATE jobs SET status=?, finished_at=? WHERE id=?",
            (status, finished, job_id),
        )
        return status


def set_branch_status(branch_id: int, status: str, error: Optional[str] = None,
                      attempts: Optional[int] = None, trx_count: Optional[int] = None,
                      executing: bool = False) -> None:
    ts = now()
    with conn() as c:
        sets = ["status=?", "updated_at=?"]
        vals = [status, ts]
        if error is not None:
            sets.append("error=?")
            vals.append(error)
        if attempts is not None:
            sets.append("attempts=?")
            vals.append(attempts)
        if trx_count is not None:
            sets.append("trx_count=?")
            vals.append(trx_count)
        vals.append(branch_id)
        c.execute(f"UPDATE job_branches SET {', '.join(sets)} WHERE id=?", vals)
        job_id = c.execute("SELECT job_id FROM job_branches WHERE id=?", (branch_id,)).fetchone()["job_id"]
    refresh_job_status(job_id, executing=executing)


def insert_branches(job_id: int, branches: list) -> None:
    ts = now()
    with conn() as c:
        for key, name in branches:
            c.execute(
                "INSERT INTO job_branches(job_id,branch_key,branch_name,status,updated_at) VALUES(?,?,?,'pending',?)",
                (job_id, key, name, ts),
            )


def queued_jobs(exclude_account_ids: set, limit: int) -> list:
    if limit <= 0:
        return []
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id"
        ).fetchall()
    out = []
    for r in rows:
        if r["account_id"] in exclude_account_ids:
            continue
        out.append(r)
        exclude_account_ids.add(r["account_id"])
        if len(out) >= limit:
            break
    return out


def active_jobs() -> list:
    with conn() as c:
        return c.execute(
            "SELECT * FROM jobs WHERE status IN ('running','waiting_user') ORDER BY id"
        ).fetchall()


def job_stats(latest: bool = True) -> dict:
    """Hitung job per status. latest=True: hanya job terbaru per akun (konsisten dgn tabel)."""
    scope = ("WHERE id IN (SELECT MAX(j2.id) FROM jobs j2 GROUP BY j2.account_id)"
             if latest else "")
    with conn() as c:
        rows = c.execute(f"SELECT status, COUNT(*) n FROM jobs {scope} GROUP BY status").fetchall()
    stats = {s: 0 for s in ("queued", "running", "waiting_user", "done", "partial", "failed")}
    for r in rows:
        stats[r["status"]] = r["n"]
    return stats


def list_jobs(q: str = "", status: str = "", pos: str = "", date_from: str = "",
              date_to: str = "", sort: str = "id", direction: str = "desc",
              page: int = 1, page_size: int = 10, latest: bool = True) -> dict:
    sort_col = {"id": "j.id", "created_at": "j.created_at", "status": "j.status",
                "branches": "done_count"}.get(sort, "j.id")
    direction = "ASC" if direction == "asc" else "DESC"
    where, vals = [], []
    if q:
        where.append("(a.label LIKE ? OR j.pos_code LIKE ? OR CAST(j.id AS TEXT) LIKE ?)")
        vals += [f"%{q}%"] * 3
    if status:
        where.append("j.status = ?")
        vals.append(status)
    if pos:
        where.append("j.pos_code = ?")
        vals.append(pos)
    if date_from:
        where.append("j.date_to >= ?")
        vals.append(date_from)
    if date_to:
        where.append("j.date_from <= ?")
        vals.append(date_to)
    if latest:
        # job terbaru per akun saja (scheduler interval -> banyak run berulang)
        where.append("j.id IN (SELECT MAX(j2.id) FROM jobs j2 GROUP BY j2.account_id)")
    w = ("WHERE " + " AND ".join(where)) if where else ""
    with conn() as c:
        total = c.execute(
            f"SELECT COUNT(*) n FROM jobs j JOIN accounts a ON a.id=j.account_id {w}", vals
        ).fetchone()["n"]
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        rows = c.execute(
            f"SELECT j.*, a.label AS account_label, "
            f"(SELECT COUNT(*) FROM job_branches b WHERE b.job_id=j.id) AS branch_total, "
            f"(SELECT COUNT(*) FROM job_branches b WHERE b.job_id=j.id AND b.status='done') AS done_count, "
            f"(SELECT COUNT(*) FROM transactions t JOIN job_branches b ON b.id=t.job_branch_id WHERE b.job_id=j.id) AS trx_count, "
            f"(SELECT COALESCE(s.enabled,0) FROM schedules s WHERE s.account_id=j.account_id) AS is_scheduled "
            f"FROM jobs j JOIN accounts a ON a.id=j.account_id {w} "
            f"ORDER BY {sort_col} {direction}, j.id DESC LIMIT ? OFFSET ?",
            vals + [page_size, (page - 1) * page_size],
        ).fetchall()
    return {"rows": rows, "total": total, "page": page, "pages": pages,
            "page_size": page_size, "latest": latest}


def delete_job(job_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM jobs WHERE id=?", (job_id,))


# ---------- transactions ----------

def upsert_transactions(branch_id: int, rows: list) -> int:
    """Dedup per akun (account_id,trx_id) lintas job: trx sudah ada & identik -> skip;
    isi berubah -> update di tempat + pindah ke cabang run ini (versi terbaru menang).
    Retry dalam cabang tetap tidak mendobel (E10, W3). Return jumlah baris baru."""
    import json
    inserted = 0
    with _write_lock, conn() as c:
        acct = c.execute("SELECT j.account_id FROM job_branches b JOIN jobs j ON j.id=b.job_id "
                         "WHERE b.id=?", (branch_id,)).fetchone()
        if acct is None:
            return 0
        account_id = acct["account_id"]
        touched = set()
        for r in rows:
            raw = json.dumps(r, ensure_ascii=False, sort_keys=True)
            trx_id = str(r.get("trx_id"))
            old = c.execute("SELECT id, job_branch_id, raw_json FROM transactions "
                            "WHERE account_id=? AND trx_id=?", (account_id, trx_id)).fetchone()
            if old is None:
                c.execute(
                    "INSERT INTO transactions(account_id,job_branch_id,trx_id,trx_date,"
                    "grand_total,total_price,total_tax,raw_json) VALUES(?,?,?,?,?,?,?,?)",
                    (account_id, branch_id, trx_id, str(r.get("trx_date", "")),
                     _f(r.get("grand_total")), _f(r.get("total_price")), _f(r.get("total_tax")), raw),
                )
                inserted += 1
                touched.add(branch_id)
            elif old["raw_json"] != raw or old["job_branch_id"] != branch_id:
                # isi berubah ATAU milik run lain -> versi terbaru pindah ke cabang ini
                touched.add(old["job_branch_id"])
                c.execute(
                    "UPDATE transactions SET job_branch_id=?, trx_date=?, grand_total=?, "
                    "total_price=?, total_tax=?, raw_json=? WHERE id=?",
                    (branch_id, str(r.get("trx_date", "")), _f(r.get("grand_total")),
                     _f(r.get("total_price")), _f(r.get("total_tax")), raw, old["id"]),
                )
                touched.add(branch_id)
            # else: identik & sudah di cabang ini -> skip (tidak mendobel)
        for bid in touched:
            c.execute("UPDATE job_branches SET trx_count=(SELECT COUNT(*) FROM transactions "
                      "WHERE job_branch_id=?) WHERE id=?", (bid, bid))
    return inserted


def _f(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def count_transactions(job_id: int) -> int:
    with conn() as c:
        return c.execute(
            "SELECT COUNT(*) n FROM transactions t JOIN job_branches b ON b.id=t.job_branch_id WHERE b.job_id=?",
            (job_id,),
        ).fetchone()["n"]


def preview_transactions(job_id: int, page: int = 1, page_size: int = 25) -> dict:
    with conn() as c:
        total = c.execute(
            "SELECT COUNT(*) n FROM transactions t JOIN job_branches b ON b.id=t.job_branch_id WHERE b.job_id=?",
            (job_id,),
        ).fetchone()["n"]
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, pages))
        rows = c.execute(
            "SELECT t.trx_id, t.trx_date, t.grand_total, t.total_tax, b.branch_name "
            "FROM transactions t JOIN job_branches b ON b.id=t.job_branch_id "
            "WHERE b.job_id=? ORDER BY t.trx_date, t.id LIMIT ? OFFSET ?",
            (job_id, page_size, (page - 1) * page_size),
        ).fetchall()
    return {"rows": rows, "total": total, "page": page, "pages": pages}


def iter_transaction_batches(job_id: int, batch_size: int = 500):
    """E13: baca DB per batch (keyset), tidak memuat semua ke RAM."""
    last = 0
    while True:
        with conn() as c:
            rows = c.execute(
                "SELECT t.id, t.trx_id, t.trx_date, t.raw_json, b.branch_name "
                "FROM transactions t JOIN job_branches b ON b.id=t.job_branch_id "
                "WHERE b.job_id=? AND t.id>? ORDER BY t.id LIMIT ?",
                (job_id, last, batch_size),
            ).fetchall()
        if not rows:
            return
        for r in rows:
            yield r
        last = rows[-1]["id"]
        if len(rows) < batch_size:
            return


def known_branches(account_id: int) -> list:
    """Cabang dari job sebelumnya -> kandidat checkbox di Job Baru."""
    with conn() as c:
        return c.execute(
            "SELECT b.branch_key, b.branch_name FROM job_branches b "
            "JOIN jobs j ON j.id=b.job_id WHERE j.account_id=? "
            "GROUP BY b.branch_key ORDER BY MAX(b.id) DESC",
            (account_id,),
        ).fetchall()


def get_session_token(account_id: int):
    with conn() as c:
        r = c.execute("SELECT token_enc FROM sessions WHERE account_id=?", (account_id,)).fetchone()
    return r["token_enc"] if r else None


def save_session_token(account_id: int, token_enc: bytes) -> None:
    ts = now()
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE account_id=?", (account_id,))
        c.execute("INSERT INTO sessions(account_id,token_enc,updated_at) VALUES(?,?,?)",
                  (account_id, token_enc, ts))


def delete_session_token(account_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE account_id=?", (account_id,))


# ---------- schedules (interval menit per akun) ----------

def get_schedule(account_id: int):
    with conn() as c:
        return c.execute("SELECT * FROM schedules WHERE account_id=?", (account_id,)).fetchone()


def list_schedules() -> list:
    """Semua akun + jadwalnya (LEFT JOIN; default nonaktif tiap 60 menit bila belum ada baris)."""
    with conn() as c:
        return c.execute(
            "SELECT a.id AS account_id, a.label, a.pos_code, a.username,"
            " COALESCE(s.enabled, 0) AS enabled, COALESCE(s.interval_minutes, 60) AS interval_minutes,"
            " s.last_run_at"
            " FROM accounts a LEFT JOIN schedules s ON s.account_id = a.id"
            " ORDER BY a.pos_code, a.label").fetchall()


def upsert_schedule(account_id: int, enabled: bool, interval_minutes: int) -> None:
    ts = now()
    with conn() as c:
        c.execute(
            "INSERT INTO schedules(account_id,enabled,interval_minutes,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled,"
            " interval_minutes=excluded.interval_minutes, updated_at=excluded.updated_at",
            (account_id, int(enabled), interval_minutes, ts))



def bulk_set_schedule(enabled: bool, pos_code: str = None) -> int:
    """Aktifkan/nonaktifkan jadwal semua akun (pos_code None) atau satu POS.
    Interval yang sudah ada dipertahankan; akun belum punya baris -> default 60 menit.
    Return jumlah akun terdampak."""
    ts = now()
    with conn() as c:
        if pos_code:
            ids = [r["id"] for r in c.execute("SELECT id FROM accounts WHERE pos_code=?", (pos_code,))]
        else:
            ids = [r["id"] for r in c.execute("SELECT id FROM accounts")]
        for aid in ids:
            c.execute(
                "INSERT INTO schedules(account_id,enabled,interval_minutes,updated_at) VALUES(?,?,60,?) "
                "ON CONFLICT(account_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
                (aid, int(enabled), ts))
    return len(ids)


def mark_schedule_run(account_id: int, run_at: str) -> None:
    ts = now()
    with conn() as c:
        c.execute("UPDATE schedules SET last_run_at=?, updated_at=? WHERE account_id=?",
                  (run_at, ts, account_id))


def account_busy(account_id: int) -> bool:
    """Saran A: ada job aktif -> jangan menumpuk antrian."""
    with conn() as c:
        return c.execute(
            "SELECT 1 FROM jobs WHERE account_id=? AND status IN ('queued','running','waiting_user') LIMIT 1",
            (account_id,)).fetchone() is not None


def last_job_auth_failed(account_id: int) -> bool:
    """Saran C: job terbaru gagal auth -> tahan jadwal sampai retry/manual (cegah lockout)."""
    with conn() as c:
        j = c.execute("SELECT id, status FROM jobs WHERE account_id=? ORDER BY id DESC LIMIT 1",
                      (account_id,)).fetchone()
        if j is None or j["status"] != "failed":
            return False
        return c.execute("SELECT 1 FROM job_branches WHERE job_id=? AND error LIKE '[auth]%' LIMIT 1",
                         (j["id"],)).fetchone() is not None


def last_job_range(account_id: int):
    """Rentang (date_from, date_to) job terbaru akun — scheduler menyambung dari sini.
    None bila akun belum punya job."""
    with conn() as c:
        j = c.execute("SELECT date_from, date_to FROM jobs WHERE account_id=? "
                      "ORDER BY id DESC LIMIT 1", (account_id,)).fetchone()
    return (j["date_from"], j["date_to"]) if j else None


def last_job_branches(account_id: int) -> list:
    """Cabang (key, name) dari job terbaru akun — scheduler memakai ulang pilihan cabang
    job terakhir, bukan auto-fetch semua. [] bila akun belum punya job / job tanpa cabang."""
    with conn() as c:
        j = c.execute("SELECT id FROM jobs WHERE account_id=? ORDER BY id DESC LIMIT 1",
                      (account_id,)).fetchone()
        if j is None:
            return []
        return [(r["branch_key"], r["branch_name"]) for r in c.execute(
            "SELECT branch_key, branch_name FROM job_branches WHERE job_id=? ORDER BY id",
            (j["id"],)).fetchall()]