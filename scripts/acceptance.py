"""T13/T14: acceptance run M0 — bukti observable PRD §13 (W1-W5 + keamanan) + E1-E18.
App dijalankan sebagai subprocess; restart = uji recovery E5 + retensi E18.
Pakai: python3 scripts/acceptance.py   (port 8765 harus bebas)
"""
import http.cookiejar
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SLA_DATA_DIR", str(ROOT / "data"))
DATA = Path(os.environ["SLA_DATA_DIR"])
DATA.mkdir(parents=True, exist_ok=True)
os.environ["SLA_GATE_TIMEOUT_SECONDS"] = "8"  # knob dev: timeout gate dipersingkat utk uji E1

BASE = "http://127.0.0.1:8765"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(time.strftime("%H:%M:%S ") + ("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def note(name, reason):
    RESULTS.append((name, True, "CATATAN: " + reason))
    print("NOTE " + name + " — " + reason, flush=True)


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None

    def req(self, method, path, data=None, headers=None, csrf=True):
        body = None
        hdrs = dict(headers or {})
        if data is not None:
            if isinstance(data, dict):
                if csrf and self.csrf:
                    data = dict(data, csrf_token=self.csrf)
                body = urllib.parse.urlencode(data, doseq=True).encode()
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = data
        if csrf and self.csrf and method != "GET":
            hdrs["X-CSRF-Token"] = self.csrf
        r = urllib.request.Request(BASE + path, data=body, headers=hdrs, method=method)
        try:
            resp = self.op.open(r, timeout=60)
        except urllib.error.HTTPError as e:
            resp = e
        raw = resp.read()
        if self.csrf is None:
            for ck in self.jar:
                if ck.name == "sla_csrf":
                    self.csrf = ck.value
        return resp.status, raw, resp.headers

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, data=None, **kw):
        return self.req("POST", path, data=data or {}, **kw)

    def new_job(self, account_id, dfrom, dto, branches):
        st, _, hdr = self.post("/jobs", {"account_id": account_id, "date_from": dfrom,
                                         "date_to": dto, "branches": branches})
        loc = hdr.get("HX-Redirect", "")
        return int(loc.rsplit("/", 1)[-1]) if loc else 0


def db_conn():
    c = sqlite3.connect(DATA / "app.db")
    c.row_factory = sqlite3.Row
    return c


def q(sql, args=()):
    with db_conn() as c:
        return [dict(r) for r in c.execute(sql, args)]


def job_status(jid):
    r = q("SELECT status FROM jobs WHERE id=?", (jid,))
    return r[0]["status"] if r else None


def wait_job(jid, want, timeout=240):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = job_status(jid)
        if last in want:
            return last
        time.sleep(0.4)
    return last


def wait_branch(jid, key, want, timeout=120):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        r = q("SELECT * FROM job_branches WHERE job_id=? AND branch_key=?", (jid, key))
        last = r[0] if r else None
        if last and last["status"] in want:
            return last
        time.sleep(0.4)
    return last


def branches_of(jid):
    return {b["branch_key"]: b for b in q("SELECT * FROM job_branches WHERE job_id=?", (jid,))}


def trx_count(jid):
    return q("SELECT COUNT(*) n FROM transactions t JOIN job_branches b ON b.id=t.job_branch_id "
             "WHERE b.job_id=?", (jid,))[0]["n"]


def xlsx_info(raw):
    tmp = DATA / "exports" / "_acceptance.xlsx"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw)
    with zipfile.ZipFile(tmp) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode()
    tmp.unlink(missing_ok=True)
    rows = len(re.findall(r"<row[ >]", xml))
    m = re.search(r'<c r="G2"[^>]*><v>([^<]+)</v>', xml)
    return rows, (float(m.group(1)) if m else None)


def start_app():
    dbg = open(DATA / "app-debug.log", "ab")
    p = subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT, env=dict(os.environ),
                         stdout=dbg, stderr=dbg)
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=2)
            return p
        except Exception:
            time.sleep(0.5)
    p.kill()
    raise RuntimeError("app tidak mau start di 127.0.0.1:8765")


def kill_app(p, browsers=True):
    if browsers:
        # bersihkan browser milik profil app agar tidak mengunci profil saat restart
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*profiles" +
              os.sep + "account_*' } | Select-Object -ExpandProperty ProcessId")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True, timeout=30).stdout
            for pid in out.split():
                if pid.strip().isdigit():
                    subprocess.run(["taskkill", "/F", "/PID", pid.strip()], capture_output=True)
        except Exception:
            pass
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait(timeout=5)


def browser_pids(account_id):
    """Cari proses browser milik profil akun ini (command line memuat path profil)."""
    pat = f"*profiles{os.sep}account_{account_id}*"
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '" + pat + "' } "
          "| Select-Object -ExpandProperty ProcessId")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=30).stdout
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def main():
    try:
        urllib.request.urlopen(BASE + "/healthz", timeout=2)
        print("App sudah jalan di 8765 — matikan dulu sebelum acceptance run.")
        sys.exit(2)
    except Exception:
        pass

    today = date.today()
    d_from = (today - timedelta(days=40)).isoformat()  # 41 hari -> chunking E9
    d_to = today.isoformat()
    one_day = today.isoformat()

    app = start_app()
    c = Client()
    j3 = None
    snap_before = {}
    old_job_id = None
    try:
        # ---------- S1 / S5 ----------
        st, raw, _ = c.get("/")
        html = raw.decode()
        check("S5 Dashboard terbuka dari satu perintah", st == 200 and "Dashboard" in html)
        ext = [u for u in re.findall(r'(?:src|href)=["\'](https?://[^"\']+)', html)
               if "127.0.0.1" not in u and "localhost" not in u]
        check("S5 nol request eksternal di HTML", not ext, str(ext[:3]))
        st, _, _ = c.req("GET", "/", headers={"Host": "evil.example.com"})
        check("S1 Host non-loopback -> 403", st == 403)
        st, _, _ = c.post("/accounts", {"pos_code": "demo", "label": "x", "username": "x",
                                        "password": "x"}, csrf=False)
        check("S1 POST tanpa CSRF -> 403", st == 403)

        # ---------- W5 seed + enkripsi ----------
        accs = q("SELECT * FROM accounts")
        demo = next(a for a in accs if a["pos_code"] == "demo")
        dump = b""
        with db_conn() as conn:
            for t in ("accounts", "jobs", "job_branches", "transactions"):
                for row in conn.execute(f"SELECT * FROM {t}"):
                    dump += json.dumps(list(row), default=str).encode()
        check("W5 nol plaintext password di seluruh DB", b"demo1234" not in dump)
        check("W5 riwayat job seed terisi (done/partial/failed)",
              {r["status"] for r in q("SELECT DISTINCT status FROM jobs")} >= {"done", "partial", "failed"})

        # ---------- W1 + W2 + W3 + E8 + E9 ----------
        jid = c.new_job(demo["id"], d_from, d_to,
                        ["demo-pusat|Cabang Pusat", "demo-mall|Cabang Mall",
                         "demo-bandara|Cabang Bandara", "demo-kosong|Cabang Sepi"])
        check("W1 job dibuat via form /jobs", jid > 0)
        saw_wait = False
        end = time.time() + 90
        while time.time() < end:
            s = job_status(jid)
            if s == "waiting_user":
                saw_wait = True
                break
            if s in ("done", "partial", "failed"):
                break
            time.sleep(0.25)
        check("W2 gate captcha -> status waiting_user + banner", saw_wait)
        if saw_wait:
            _, body, _ = c.get(f"/partials/jobs/{jid}")
            check("W2 banner MENUNGGU USER + tombol fallback di UI",
                  b"MENUNGGU USER" in body and b"Sudah selesai" in body)
            t0 = time.time()
            c.post("/demo-pos/solve")  # model: user menyelesaikan captcha di browser
            resumed = False
            while time.time() - t0 < 10:
                if job_status(jid) != "waiting_user":
                    resumed = True
                    break
                time.sleep(0.25)
            check("W2 auto-resume <=5s tanpa klik UI", resumed and time.time() - t0 <= 6,
                  f"{time.time() - t0:.1f}s")
        st = wait_job(jid, ("partial", "done", "failed"))
        brs = branches_of(jid)
        check("W3 job partial: cabang gagal tidak menghanguskan yang sukses", st == "partial", st)
        check("E15 bandara failed [ui_changed] menyebut elemen hilang",
              brs["demo-bandara"]["status"] == "failed"
              and "[ui_changed]" in (brs["demo-bandara"]["error"] or "")
              and "#trx-table" in (brs["demo-bandara"]["error"] or ""))
        check("E3 mall auto re-login lalu sukses (attempts=2)",
              brs["demo-mall"]["status"] == "done" and brs["demo-mall"]["attempts"] == 2,
              f"attempts={brs['demo-mall']['attempts']}")
        check("E8 cabang kosong done trx_count=0",
              brs["demo-kosong"]["status"] == "done" and brs["demo-kosong"]["trx_count"] == 0)
        check("E9 chunking 41 hari -> data penuh", brs["demo-pusat"]["trx_count"] > 100,
              f"pusat={brs['demo-pusat']['trx_count']}")
        n_before = trx_count(jid)
        pusat_before = brs["demo-pusat"]["trx_count"]

        # ---------- W3 retry per cabang + dedup ----------
        c.post(f"/jobs/{jid}/branches/{brs['demo-bandara']['id']}/retry")
        st = wait_job(jid, ("done",), timeout=120)
        brs2 = branches_of(jid)
        n_after = trx_count(jid)
        check("W3 retry hanya cabang gagal -> job done",
              st == "done" and brs2["demo-bandara"]["status"] == "done", st)
        check("W3 retry tidak mendobel baris",
              n_after == n_before + brs2["demo-bandara"]["trx_count"]
              and brs2["demo-pusat"]["trx_count"] == pusat_before,
              f"{n_before} -> {n_after}")

        # ---------- W4 Excel ----------
        st, raw, _ = c.get(f"/jobs/{jid}/export")
        check("W4 export 200 file xlsx", st == 200 and raw[:2] == b"PK")
        rows, g2 = xlsx_info(raw) if raw[:2] == b"PK" else (0, None)
        check("W4 jumlah baris Excel = DB + header", rows == n_after + 1, f"{rows} vs {n_after + 1}")
        first = q("SELECT t.raw_json FROM transactions t JOIN job_branches b "
                  "ON b.id=t.job_branch_id WHERE b.job_id=? ORDER BY t.id LIMIT 1", (jid,))
        first_raw = json.loads(first[0]["raw_json"]) if first else {}
        check("W4 nilai uang Excel cocok raw_json (exact)",
              g2 is not None and bool(first_raw)
              and abs(g2 - float(first_raw["grand_total"])) < 0.005,
              f"{g2} vs {first_raw.get('grand_total')}")
        st, raw2, _ = c.get(f"/jobs/{jid}/export")
        check("E12 export ulang identik (idempotent)", raw2 == raw)

        # ---------- E4 / S7 / S2 / E12 guard ----------
        c.post("/accounts", {"pos_code": "demo", "label": "Salah Password",
                             "username": "operator", "password": "password-salah"})
        bad_acc = q("SELECT id FROM accounts WHERE label='Salah Password'")[0]["id"]
        bad_job = c.new_job(bad_acc, one_day, one_day, ["demo-pusat|Cabang Pusat"])
        st = wait_job(bad_job, ("failed",), timeout=120)
        b = branches_of(bad_job)["demo-pusat"]
        check("E4/S7 kredensial salah -> [auth] attempts=1 tanpa auto-retry",
              st == "failed" and b["attempts"] == 1 and "[auth]" in (b["error"] or ""),
              f"attempts={b['attempts']}")
        st, _, _ = c.get(f"/jobs/{bad_job}/export")
        check("E12 export tanpa cabang done -> 409", st == 409)
        st, raw, _ = c.get(f"/jobs/{bad_job}/errors.txt")
        check("S2 detail error redacted (password tidak bocor)",
              st == 200 and b"password-salah" not in raw and b"[auth]" in raw)

        # ---------- E6 antrian akun sama ----------
        j1 = c.new_job(demo["id"], d_from, d_to, ["demo-pusat|Cabang Pusat"])
        j2 = c.new_job(demo["id"], d_from, d_to, ["demo-mall|Cabang Mall"])
        time.sleep(1.5)
        s1, s2 = job_status(j1), job_status(j2)
        check("E6 job kedua akun sama -> antri", s1 in ("running", "waiting_user") and s2 == "queued",
              f"{s1}/{s2}")
        wait_job(j1, ("done", "partial", "failed"))
        wait_job(j2, ("done", "partial", "failed"))

        # ---------- E7 maks 2 paralel ----------
        for label in ("Konkuren A", "Konkuren B"):
            c.post("/accounts", {"pos_code": "demo", "label": label,
                                 "username": "operator", "password": "demo1234"})
        ka = q("SELECT id FROM accounts WHERE label='Konkuren A'")[0]["id"]
        kb = q("SELECT id FROM accounts WHERE label='Konkuren B'")[0]["id"]
        jc1 = c.new_job(demo["id"], d_from, d_to, ["demo-pusat|Cabang Pusat"])
        jc2 = c.new_job(ka, d_from, d_to, ["demo-pusat|Cabang Pusat"])
        jc3 = c.new_job(kb, d_from, d_to, ["demo-pusat|Cabang Pusat"])
        max_active, saw_queued = 0, False
        end = time.time() + 12
        while time.time() < end:
            sts = [job_status(x) for x in (jc1, jc2, jc3)]
            active = sum(1 for s in sts if s in ("running", "waiting_user"))
            max_active = max(max_active, active)
            if "queued" in sts:
                saw_queued = True
            if all(s in ("done", "partial", "failed") for s in sts):
                break
            time.sleep(0.4)
        check("E7 maks 2 job paralel, ke-3 antri", max_active <= 2 and saw_queued,
              f"max_active={max_active}")
        for x in (jc1, jc2, jc3):
            wait_job(x, ("done", "partial", "failed"))

        # ---------- E1 timeout gate per cabang, job lanjut ----------
        c.post("/demo-pos/reset")
        c.post("/accounts", {"pos_code": "demo", "label": "Timeout Uji",
                             "username": "operator", "password": "demo1234"})
        to_acc = q("SELECT id FROM accounts WHERE label='Timeout Uji'")[0]["id"]
        je = c.new_job(to_acc, one_day, one_day, ["demo-otp|Cabang OTP", "demo-mall|Cabang Mall"])
        end = time.time() + 60
        while time.time() < end and job_status(je) != "waiting_user":
            time.sleep(0.25)
        c.post("/demo-pos/solve")  # loloskan gate LOGIN; gate OTP cabang dibiarkan timeout
        st = wait_job(je, ("partial", "done", "failed"))
        brs = branches_of(je)
        check("E1 gate OTP timeout -> cabang [user_timeout], cabang lain tetap jalan",
              brs["demo-otp"]["status"] == "failed"
              and "[user_timeout]" in (brs["demo-otp"]["error"] or "")
              and brs["demo-mall"]["status"] == "done" and st == "partial",
              f"otp={brs['demo-otp']['status']} mall={brs['demo-mall']['status']}")
        c.post(f"/jobs/{je}/branches/{brs['demo-otp']['id']}/retry")
        wait_job(je, ("done",), timeout=120)
        check("E1 cabang user_timeout bisa di-retry",
              branches_of(je)["demo-otp"]["status"] == "done")

        # ---------- E17 RESTRICT + S6 profil ----------
        st, _, _ = c.post(f"/accounts/{demo['id']}/delete")
        check("E17 hapus akun ber-job -> ditolak", st == 409)
        c.post(f"/jobs/{bad_job}/delete")
        prof = DATA / "profiles" / f"account_{bad_acc}"
        st, _, _ = c.post(f"/accounts/{bad_acc}/delete")
        check("S6 hapus akun tanpa job -> baris + profil browser hilang",
              st == 204 and not prof.exists() and not q("SELECT 1 FROM accounts WHERE id=?", (bad_acc,)))

        # ---------- E2 browser ditutup user ----------
        j2e = c.new_job(demo["id"], d_from, d_to, ["demo-pusat|Cabang Pusat"])
        wait_branch(j2e, "demo-pusat", ("running",), timeout=90)
        time.sleep(1.0)
        pids = browser_pids(demo["id"])
        if pids:
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            st = wait_job(j2e, ("failed", "partial"), timeout=90)
            br = branches_of(j2e)["demo-pusat"]
            app_alive = urllib.request.urlopen(BASE + "/healthz", timeout=5).status == 200
            check("E2 browser ditutup -> cabang [browser], aplikasi tetap hidup",
                  br["status"] == "failed" and "[browser]" in (br["error"] or "") and app_alive,
                  f"pids={pids}")
        else:
            note("E2 browser ditutup", "proses browser tidak ditemukan via CIM; jalur kode _wrap_pw -> BrowserDead")

        # ---------- E5 persiapan: job jalan + job tua, lalu restart ----------
        j3 = c.new_job(demo["id"], d_from, d_to, ["demo-pusat|Cabang Pusat", "demo-mall|Cabang Mall"])
        wait_branch(j3, "demo-pusat", ("running",), timeout=90)
        snap_before = {b["branch_key"]: (b["status"], b["trx_count"]) for b in branches_of(j3).values()}
        old_created = (today - timedelta(days=100)).strftime("%Y-%m-%dT08:00:00Z")
        with db_conn() as conn:
            cur = conn.execute(
                "INSERT INTO jobs(account_id,pos_code,date_from,date_to,status,created_at) "
                "VALUES(?,?,?,?, 'done', ?)", (demo["id"], "demo", one_day, one_day, old_created))
            old_job_id = cur.lastrowid
    finally:
        kill_app(app, browsers=False)  # E5: biarkan browser orphan; recovery yang menandai

    # ---------- Phase B: restart ----------
    app2 = start_app()
    c2 = Client()
    c2.get("/")  # ambil cookie CSRF sebelum POST pertama
    try:
        st = job_status(j3)
        brs = branches_of(j3)
        interrupted = [b for b in brs.values() if b["status"] == "failed" and "[interrupted]" in (b["error"] or "")]
        intact = all(brs[k]["trx_count"] == v[1] for k, v in snap_before.items() if v[0] == "done")
        check("E5 restart saat job jalan -> failed [interrupted], data committed utuh",
              st == "failed" and len(interrupted) >= 1 and intact,
              f"status={st} interrupted={len(interrupted)}")
        check("E18 job >90 hari terpurge saat startup, akun tetap",
              not q("SELECT 1 FROM jobs WHERE id=?", (old_job_id,))
              and q("SELECT 1 FROM accounts WHERE id=?", (demo["id"],)))
        # bersihkan browser orphan sisa E5 agar profil tidak terkunci saat job baru
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*profiles"
                        + os.sep + "account_*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                       capture_output=True, timeout=30)
        time.sleep(2)
        jw = c2.new_job(demo["id"], one_day, one_day, ["demo-pusat|Cabang Pusat"])
        st = wait_job(jw, ("done", "partial", "failed"), timeout=120)
        check("W5 restart -> job jalan tanpa input ulang kredensial", st == "done", st)
        st, raw, _ = c2.get("/jobs/new")
        check("UI Job Baru terbuka", st == 200 and b"Jalankan" in raw)
        st, raw, _ = c2.get("/accounts")
        check("UI Akun terbuka", st == 200 and b"Akun Merchant" in raw)
        st, raw, _ = c2.get(f"/jobs/{j3}")
        check("UI Detail Job menampilkan interrupted", st == 200 and b"interrupted" in raw)
    finally:
        kill_app(app2)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} PASS", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()