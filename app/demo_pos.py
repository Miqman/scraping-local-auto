"""Demo POS — situs POS lokal untuk membuktikan W1-W5 end-to-end di mesin dev
(T09/T14). Bukan fitur operator: POS nyata (SiberPOS dll) tetap target produksi.
Login: operator / demo1234. Gate captcha login sekali per profil browser;
cabang OTP memicu gate kedua di tengah scrape (bukti E1 per cabang).
"""
import hashlib
import random
from datetime import date, timedelta
from urllib.parse import urlencode, quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .paths import TEMPLATES_DIR

router = APIRouter(prefix="/demo-pos")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

DEMO_USERNAME = "operator"
DEMO_PASSWORD = "demo1234"

BRANCHES = [
    ("demo-pusat", "Cabang Pusat"),
    ("demo-mall", "Cabang Mall"),
    ("demo-bandara", "Cabang Bandara"),
    ("demo-otp", "Cabang OTP"),
    ("demo-kosong", "Cabang Sepi"),
]
BRANCH_NAMES = dict(BRANCHES)
PAGE_SIZE = 8
MAX_DAYS = 31

_solved_flag = False   # harness: model "user menyelesaikan captcha" (sekali pakai)
_fail_once = {}        # mall: HTTP 500 sekali -> bukti E3 auto re-login
_broken_once = {}      # bandara: UI rusak sekali -> bukti E15 ui_changed + W3 retry
_otp_once = {}         # otp: gate tengah scrape sekali -> bukti E1 per cabang
_customers = ["Budi Santoso", "Siti Aminah", "Agus Wijaya", "Dewi Lestari", "Rina Hartati",
              "Joko Susilo", "Maya Putri", "Andi Pratama", "Lina Marlina", "Hendra Gunawan"]


def gen_rows(branch_key: str, day: str) -> list:
    """Deterministik per (cabang, tanggal); uang = string exact di raw_json."""
    if branch_key == "demo-kosong":
        return []
    seed = int(hashlib.sha256(f"{branch_key}:{day}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    rows = []
    for i in range(rng.randint(3, 7)):
        price = rng.randint(15000, 950000)
        tax = round(price * 0.11)
        rows.append({
            "trx_id": f"DM-{branch_key.split('-')[1][:4].upper()}-{day.replace('-', '')}-{i + 1:03d}",
            "trx_date": f"{day} {rng.randint(8, 21):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}",
            "branch_key": branch_key,
            "branch_name": BRANCH_NAMES.get(branch_key, branch_key),
            "customer": rng.choice(_customers),
            "payment_method": rng.choice(["QRIS", "Cash", "Debit", "Credit"]),
            "items": rng.randint(1, 9),
            "total_price": f"{price}.00",
            "total_tax": f"{tax}.00",
            "grand_total": f"{price + tax}.00",
            "currency": "IDR",
        })
    rows.sort(key=lambda r: r["trx_date"])  # E10: tertua dulu, urutan stabil
    return rows


def _cookie(kind: str) -> str:
    return f"demo_gate_{kind}"


def _logged_in(request: Request) -> bool:
    return request.cookies.get("demo_sess") == "1"


def _gate_passed(request: Request, kind: str) -> bool:
    return request.cookies.get(_cookie(kind)) == "1"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "demo_pos/login.html", {"error": error})


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    if username != DEMO_USERNAME or password != DEMO_PASSWORD:
        return templates.TemplateResponse(
            request, "demo_pos/login.html",
            {"error": "Username atau password salah."}, status_code=401)
    next_url = str(form.get("next", "/demo-pos/home"))
    target = next_url if _gate_passed(request, "login") else \
        "/demo-pos/gate?" + urlencode({"next": next_url, "kind": "login"})
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie("demo_sess", "1", httponly=True, samesite="lax", path="/demo-pos")
    return resp


@router.get("/gate", response_class=HTMLResponse)
def gate_page(request: Request, next: str = "/demo-pos/home", kind: str = "login"):
    global _solved_flag
    if _gate_passed(request, kind):
        return RedirectResponse(next)
    if _solved_flag:
        _solved_flag = False
        resp = RedirectResponse(next)
        resp.set_cookie(_cookie(kind), "1", httponly=True, samesite="lax", path="/demo-pos", max_age=2592000)
        return resp
    return templates.TemplateResponse(request, "demo_pos/gate.html", {"next": next, "kind": kind})


@router.post("/gate")
async def gate_submit(request: Request):
    form = await request.form()
    next_url = str(form.get("next", "/demo-pos/home"))
    kind = str(form.get("kind", "login"))
    resp = RedirectResponse(next_url, status_code=303)
    resp.set_cookie(_cookie(kind), "1", httponly=True, samesite="lax", path="/demo-pos", max_age=2592000)
    return resp


@router.get("/gate-status")
def gate_status(request: Request, kind: str = "login"):
    """Dipoll JS halaman gate: lolos bila cookie ada ATAU flag harness (sekali pakai)."""
    global _solved_flag
    solved = _gate_passed(request, kind)
    resp = JSONResponse({"solved": solved or _solved_flag})
    if _solved_flag:
        _solved_flag = False
        resp.set_cookie(_cookie(kind), "1", httponly=True, samesite="lax", path="/demo-pos", max_age=2592000)
    return resp


@router.post("/solve")
def solve_gate():
    """Harness dev/acceptance: model user menyelesaikan captcha di browser scraper."""
    global _solved_flag
    _solved_flag = True
    return JSONResponse({"solved": True})


@router.post("/reset")
def reset_harness():
    global _solved_flag
    _solved_flag = False
    _otp_once.clear()
    _fail_once.clear()
    _broken_once.clear()
    return JSONResponse({"reset": True})


@router.get("/home", response_class=HTMLResponse)
def home(request: Request):
    if not _logged_in(request):
        return RedirectResponse("/demo-pos/login")
    return templates.TemplateResponse(request, "demo_pos/home.html", {"branches": BRANCHES})


@router.get("/branches")
def branches(request: Request):
    if not _logged_in(request):
        return RedirectResponse("/demo-pos/login")
    return JSONResponse([{"key": k, "name": n} for k, n in BRANCHES])


@router.get("/transactions", response_class=HTMLResponse)
def transactions(request: Request, branch: str = "", date_from: str = "", date_to: str = "",
                 page: int = 1):
    if not _logged_in(request):
        return RedirectResponse("/demo-pos/login")
    if branch not in BRANCH_NAMES:
        return PlainTextResponse("Cabang tidak dikenal", status_code=404)
    try:
        a = date.fromisoformat(date_from)
        b = date.fromisoformat(date_to)
    except ValueError:
        return PlainTextResponse("Tanggal tidak valid", status_code=400)
    if (b - a).days + 1 > MAX_DAYS:
        return PlainTextResponse(f"Rentang maksimal {MAX_DAYS} hari per request", status_code=400)
    key = branch  # skenario sekali per cabang per proses (bukan per chunk)
    if branch == "demo-mall" and key not in _fail_once:
        _fail_once[key] = True
        return PlainTextResponse("Internal server error (simulasi gangguan sesaat)", status_code=500)
    if branch == "demo-bandara" and key not in _broken_once:
        _broken_once[key] = True
        return HTMLResponse("<html><body><h1>Transaksi</h1><p>Layout baru — tabel dipindah ke komponen lain.</p></body></html>")
    if branch == "demo-otp" and key not in _otp_once:
        _otp_once[key] = True
        next_url = str(request.url)
        return RedirectResponse("/demo-pos/gate?" + urlencode({"next": next_url, "kind": "otp"}))
    rows = []
    cur = a
    while cur <= b:
        rows.extend(gen_rows(branch, cur.isoformat()))
        cur += timedelta(days=1)
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    chunk = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return templates.TemplateResponse(request, "demo_pos/transactions.html", {
        "branch": branch, "branch_name": BRANCH_NAMES[branch],
        "date_from": date_from, "date_to": date_to,
        "rows": chunk, "page": page, "pages": pages, "total": len(rows),
    })