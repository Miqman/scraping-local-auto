"""Crawler Loyverse — port dari MaphyMerchantApps/scraping-service .../LoyversePOSSvc.java
(jalur produksi getAllTrxRAW) + scraper-service/app/loyverse (Go).
2captcha, proxy, dan limit captcha harian tidak diport (lokal: pause user W2, bukan saldo).

Alur Java yang dipertahankan:
1. Guard cookie: baca cookies dari DB (padanan lokal: tabel `sessions` terenkripsi) ->
   checkCookies via POST r.loyverse.com/data/ownercab/getreceiptsarchive;
   401 -> invalid, 200 -> valid, error jaringan -> anggap valid sementara (hemat captcha).
2. Login: Camoufox headed buka loyverse.com/en/login; reCAPTCHA v2 invisible
   (sitekey 6LdCH9IUAAAAAFP6czcCwYhAgc7yGegk8vT9C8bt) dieksekusi JS situs saat submit;
   bila challenge interaktif muncul -> pause user (W2). Situs sendiri yang POST
   r.loyverse.com/data/cabinetlogin {email, password, recaptchaResponse, rememberMe,
   cabinetLang, type:pos}; cookies sesi ditangkap dari context browser (padanan doc.cookies()).
3. WAJIB: simpan cookies ke DB (sessions, AES-256-GCM) — padanan saveToDB cartenz.
4. Cabang: agregat ("all", "Semua Outlet") seperti Java getBranchList (outletsIds=all);
   nama outlet per baris tetap ada di receipt.outletName (Go: fillSelectData.outlets).
5. Transaksi: POST getreceiptsarchive customPeriod, limit "100", paginasi pages/offset
   (Java getTrxPagesRaw); SALE saja; REFUND menghapus SALE induknya (parentPrintedNo)
   kecuali mode online (Java htmlToRaw); dedup printedNo per chunk (Java trxFinal).
"""
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Iterator, List, Tuple

from app import browser, crypto, db
from app.errors import CrawlError
from .base import Crawler, CrawlContext, date_chunks

log = logging.getLogger("sla.crawler.loyverse")

BASE = "https://loyverse.com"
LOGIN_URL = BASE + "/en/login"
CABINET = "https://r.loyverse.com"
LOGIN_API_PATH = "/data/cabinetlogin"
RECEIPTS_API = CABINET + "/data/ownercab/getreceiptsarchive"
DASHBOARD_URL = CABINET + "/dashboard/"
SITEKEY = "6LdCH9IUAAAAAFP6czcCwYhAgc7yGegk8vT9C8bt"  # referensi Java; JS situs yang execute
PAGE_LIMIT = 100
TZ_NAME = "Asia/Jakarta"
TZ_OFFSET_MS = 25200000
TZ_PLUS_H = 7  # Java htmlToRaw: date receipt (UTC) + 7 jam
SUBMIT_WAIT_S = 25
MAX_SUBMIT = 3
TAX_NAMES = ("tax", "pajak restoran")

LOGIN_USER = 'input[name="email"], input[type="email"], form input[type="text"]'
LOGIN_PASS = 'input[type="password"]'
LOGIN_SUBMIT = 'button[type="submit"], input[type="submit"]'
LOGIN_ERROR = ('.mat-error, mat-error, .mat-snack-bar-container, .alert, .error-message, '
               '.login-error, [role="alert"], .invalid-feedback, .text-danger')


def receipts_body(a: str, b: str, page: int, limit: int = PAGE_LIMIT) -> dict:
    """Body POST getreceiptsarchive (Java getTransaction, field persis)."""
    return {
        "startDate": a + " 00:00:00",
        "endDate": b + " 23:59:59",
        "customPeriod": True,
        "endTime": None,
        "limit": str(limit),
        "offset": limit * page,
        "merchantsIds": "all",
        "outletsIds": "all",
        "page": page,
        "payType": None,
        "receiptId": None,
        "receiptType": None,
        "search": None,
        "tzName": TZ_NAME,
        "tzOffset": TZ_OFFSET_MS,
        "startWeek": 0,
        "startTime": None,
    }


def map_receipts(receipts: list, online: bool = False) -> List[dict]:
    """Java htmlToRaw + Go ResTrxDetails: SALE saja, refund dihapus (kecuali online),
    dedup printedNo, uang cents -> desimal exact (Java int-div memotong sen; lokal
    mempertahankan presisi sesuai W4 — raw_json tetap memuat cents asli)."""
    sales = {}
    refunds = set()
    for r in receipts:
        if not isinstance(r, dict):
            continue
        kind = str(r.get("type") or "").upper()
        if kind == "REFUND":
            refunds.add(str(r.get("parentPrintedNo")))
            continue
        if kind != "SALE":
            continue
        pn = str(r.get("printedNo"))
        row = dict(r)  # baris utuh = sumber kebenaran raw_json
        grand = int(r.get("totalAmount") or 0) / 100
        tax = 0
        for td in ((r.get("taxAmount") or {}).get("taxesDataList") or []):
            if isinstance(td, dict) and str(td.get("taxName") or "").lower() in TAX_NAMES:
                tax = int(td.get("amount") or 0) / 100
                break
        raw_dt = str(r.get("date") or "")
        try:
            dt = (datetime.strptime(raw_dt[:19], "%Y-%m-%dT%H:%M:%S")
                  + timedelta(hours=TZ_PLUS_H)).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            dt = raw_dt[:19]
        row["trx_id"] = f"{r.get('ownerCashRegisterNo')}-{pn}"  # Go TrxId / Java Struk No
        row["trx_date"] = dt
        row["grand_total"] = grand
        row["total_tax"] = tax
        row["total_price"] = round(grand - tax, 2)
        row["canceled"] = str(r.get("cancelled") or "null") != "null"
        sales[pn] = row  # Java trxFinal: keyed printedNo
    if not online:
        for pn in refunds:
            sales.pop(pn, None)
    return list(sales.values())


class LoyverseCrawler(Crawler):
    pos_code = "loyverse"
    name = "Loyverse"
    base_url = BASE
    max_range_days = 31
    has_branch_api = False  # Java getBranchList agregat; outletsIds=all, outletName per baris
    gate_kind = "reCAPTCHA v2 invisible (Camoufox; pause user bila interaktif)"

    def __init__(self):
        self.cookies = []

    # ---------- gate (reCAPTCHA invisible vs interaktif) ----------
    def _recaptcha_interactive(self, page) -> bool:
        """Iframe recaptcha terlihat & berukuran challenge = butuh user.
        Invisible reCAPTCHA tetap menaruh iframe tersembunyi + badge — bukan gate."""
        try:
            loc = page.locator('iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"]')
            for i in range(loc.count()):
                frame = loc.nth(i)
                if frame.is_visible():
                    box = frame.bounding_box()
                    if box and box["width"] >= 50 and box["height"] >= 50:
                        return True
        except Exception:
            pass
        return False

    def gate_active(self, page, ctx=None) -> bool:
        url = (page.url or "").lower()
        if "loyverse.com" in url and "/login" in url:
            return self._recaptcha_interactive(page)
        return browser.is_gated(page)

    # ---------- cookie guard (padanan DB cartenz) ----------
    def _api_headers(self) -> dict:
        return {
            "content-type": "application/json;charset=UTF-8",
            "accept": "application/json, text/plain, */*",
            "origin": CABINET,
            "referer": DASHBOARD_URL,
        }

    def _check_cookies(self, ctx: CrawlContext) -> bool:
        today = date.today().isoformat()
        try:
            r = ctx.session._ctx.request.post(
                RECEIPTS_API, data=json.dumps(receipts_body(today, today, 0, 10)),
                headers=self._api_headers())
            if r.status == 401:
                return False
            body = r.text()
            if body.lstrip().startswith("<"):
                return False  # halaman login HTML = sesi putus
            return r.status == 200  # Java checkCookies: 200 -> valid
        except Exception:
            return True  # error jaringan: anggap valid sementara (ala Java, hemat captcha)

    def _snapshot_cookies(self, ctx: CrawlContext) -> list:
        # cookie auth (ownercub-lls, JSESSIONID) ber-path /data -> filter URL path /
        # membuangnya; ambil semua lalu saring domain loyverse (padanan doc.cookies() Java).
        try:
            return [c for c in ctx.session._ctx.cookies()
                    if "loyverse.com" in (c.get("domain") or "")]
        except Exception:
            return []

    # ---------- login ----------
    def _click_submit(self, page) -> bool:
        try:
            btn = page.locator(LOGIN_SUBMIT).first
            if btn.count() and btn.is_enabled():
                btn.click(timeout=3000)
                return True
        except Exception:
            pass
        return False

    def _login_error(self, page) -> str:
        try:
            loc = page.locator(LOGIN_ERROR)
            for i in range(loc.count()):
                el = loc.nth(i)
                if el.is_visible():
                    txt = (el.inner_text() or "").strip()
                    if txt:
                        return txt[:200]
        except Exception:
            pass
        return ""

    def login(self, ctx: CrawlContext, username: str, password: str) -> None:
        ctx.page.set_default_timeout(60000)
        acc_id = db.get_job(ctx.job_id)["account_id"]
        enc = db.get_session_token(acc_id)
        if enc:
            try:
                saved = json.loads(crypto.decrypt(enc))
            except Exception:
                saved = []
            if saved:
                try:
                    ctx.session._ctx.add_cookies(saved)
                except Exception:
                    saved = []
            if saved and self._check_cookies(ctx):
                self.cookies = saved
                log.info("Loyverse reuse cookies dari sessions (account %s) tanpa login browser", acc_id)
                return
            db.delete_session_token(acc_id)  # 401/expired -> login ulang (T21)
        resp = ctx.session.goto(LOGIN_URL)  # Camoufox hanya untuk login (pola Majoo)
        ctx.after_nav(resp)
        page = ctx.page
        log.info("Loyverse login page url=%s", page.url)
        try:
            page.wait_for_selector(LOGIN_PASS, timeout=30000)
        except Exception as exc:
            raise CrawlError("ui_changed",
                             f"Form login Loyverse tidak tampil (input password absen): {exc}") from exc
        page.fill(LOGIN_USER, username)
        page.fill(LOGIN_PASS, password)
        captured = {}

        def on_resp(r):
            try:
                if LOGIN_API_PATH in r.url and r.request.method == "POST":
                    captured["status"] = r.status
                    try:
                        captured["json"] = r.json()
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", on_resp)
        ok = False
        try:
            for attempt in range(MAX_SUBMIT):
                captured.clear()
                if not self._click_submit(page):
                    raise CrawlError("ui_changed", "Tombol submit login Loyverse tidak ditemukan")
                end = time.monotonic() + SUBMIT_WAIT_S
                while time.monotonic() < end:
                    if captured.get("status") is not None or page.url.startswith(DASHBOARD_URL):
                        break
                    page.wait_for_timeout(500)
                status = captured.get("status")
                d = captured.get("json")
                login_result = str(d.get("loginResult") or "") if isinstance(d, dict) else ""
                if status in (401, 403) or (login_result and login_result != "ok"):
                    # terobservasi: kredensial salah = HTTP 200 + loginResult=user_password_not_found
                    err = self._login_error(page) or login_result or "email/password salah"
                    raise CrawlError("auth", f"Login Loyverse ditolak: {err}")
                if status == 200 or page.url.startswith(DASHBOARD_URL):
                    # tunggu cookie sesi terbit / redirect backoffice; AWSALB sudah ada
                    # sejak load awal jadi snapshot tidak boleh langsung diambil
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        self.cookies = self._snapshot_cookies(ctx)
                        if any(c.get("name") in ("ownercub-lls", "JSESSIONID")
                               for c in self.cookies) or page.url.startswith(CABINET + "/"):
                            break
                        page.wait_for_timeout(500)
                    self.cookies = self._snapshot_cookies(ctx)
                    if self.cookies and self._check_cookies(ctx):
                        ok = True
                        break
                    if self.gate_active(page):
                        ctx.check_gate()  # captcha belum lolos -> pause user (W2), submit ulang
                        continue
                    # Java: cookies login invalid = wrong username or password (tanpa retry, S7)
                    msg = self._login_error(page)
                    raise CrawlError("auth", f"Login Loyverse ditolak: {msg or 'email/password salah'}")
                # tidak ada respons cabinetlogin: gate interaktif atau error inline situs
                if self.gate_active(page):
                    ctx.check_gate()  # user selesaikan challenge, lalu submit ulang
                    continue
                err = self._login_error(page)
                if err:
                    raise CrawlError("auth", f"Login Loyverse ditolak: {err}")
                if attempt == MAX_SUBMIT - 1:
                    raise CrawlError("ui_changed",
                                     "Respons cabinetlogin Loyverse tidak tertangkap setelah submit")
        finally:
            page.remove_listener("response", on_resp)
        if not ok:
            raise CrawlError("auth", "Login Loyverse gagal: cookies sesi tidak terbit")
        # langkah wajib ala Java saveToDB(cartenz): cookies persisten per akun, terenkripsi
        db.save_session_token(acc_id, crypto.encrypt(json.dumps(self.cookies)))
        log.info("Loyverse cookies tersimpan ke sessions (account %s)", acc_id)

    # ---------- cabang ----------
    def list_branches(self, ctx: CrawlContext) -> List[Tuple[str, str]]:
        return [("all", "Semua Outlet")]

    # ---------- transaksi ----------
    def scrape_branch(self, ctx: CrawlContext, branch_key: str,
                      date_from: str, date_to: str) -> Iterator[List[dict]]:
        for a, b in date_chunks(date_from, date_to, self.max_range_days):
            acc = []
            page_n = 0
            while True:
                ctx.session.check_alive()
                r = ctx.session._ctx.request.post(
                    RECEIPTS_API, data=json.dumps(receipts_body(a, b, page_n)),
                    headers=self._api_headers())
                if r.status in (401, 403):
                    raise CrawlError("network",
                                     f"API transaksi Loyverse ditolak ({r.status}) — sesi perlu login ulang")
                if r.status != 200:
                    raise CrawlError("network", f"API transaksi Loyverse HTTP {r.status} (page {page_n})")
                try:
                    d = r.json()
                except Exception as exc:
                    raise CrawlError("ui_changed",
                                     f"Respons getreceiptsarchive Loyverse bukan JSON: {exc}") from exc
                if str(d.get("result") or "").lower() != "ok":
                    raise CrawlError("ui_changed",
                                     f"getreceiptsarchive Loyverse result={d.get('result')!r} (bukan ok)")
                receipts = d.get("receipts") or []
                acc.extend(receipts)
                try:
                    pages = int(d.get("pages") or 0)
                except (TypeError, ValueError):
                    pages = 0
                if page_n == 0:
                    log.info("Loyverse chunk %s..%s pages=%s receipts=%s", a, b, pages, len(receipts))
                page_n += 1
                if pages:
                    if page_n >= pages:
                        break
                elif len(receipts) < PAGE_LIMIT:
                    break  # respons tanpa pages (terobservasi di rentang lama): halaman pendek = selesai
            rows = map_receipts(acc)  # filter SALE + refund + dedup printedNo (Java htmlToRaw)
            if rows:
                yield rows


def _self_check() -> None:
    b = receipts_body("2026-05-01", "2026-05-11", 2)
    assert b["startDate"] == "2026-05-01 00:00:00" and b["endDate"] == "2026-05-11 23:59:59"
    assert b["limit"] == "100" and b["offset"] == 200 and b["page"] == 2
    assert b["customPeriod"] is True and b["merchantsIds"] == "all" and b["outletsIds"] == "all"
    assert b["tzName"] == "Asia/Jakarta" and b["tzOffset"] == 25200000
    sale = {
        "type": "SALE", "printedNo": 1042, "ownerCashRegisterNo": 3,
        "date": "2026-05-01T17:30:00.000Z", "totalAmount": 1234567,
        "merchantName": "MERCHANT A", "outletName": "Outlet 1", "cancelled": "null",
        "taxAmount": {"taxesDataList": [{"taxName": "Pajak Restoran", "amount": 112233}]},
        "payments": [{"paymentTotal": 1234567}],
    }
    rows = map_receipts([sale])
    assert len(rows) == 1
    r = rows[0]
    assert r["trx_id"] == "3-1042"
    assert r["trx_date"] == "2026-05-02 00:30:00"  # +7 jam (Java)
    assert r["grand_total"] == 12345.67 and r["total_tax"] == 1122.33
    assert r["total_price"] == 11223.34 and r["canceled"] is False
    assert r["totalAmount"] == 1234567  # cents asli tetap di raw
    refund = {"type": "REFUND", "parentPrintedNo": 1042, "printedNo": 9999}
    assert map_receipts([sale, refund]) == []  # Java: refund menghapus SALE induk
    assert len(map_receipts([sale, refund], online=True)) == 1  # mode online: tetap
    dup = dict(sale, totalAmount=999)
    assert len(map_receipts([sale, dup])) == 1  # dedup printedNo (Java trxFinal)
    other = dict(sale, printedNo=1043, cancelled="2026-05-01T18:00:00", date="bad-date")
    r2 = map_receipts([other])[0]
    assert r2["canceled"] is True and r2["trx_date"] == "bad-date"[:19]
    assert map_receipts([{"type": "VOID", "printedNo": 1}]) == []
    assert map_receipts([]) == []
    print("loyverse crawler self_check ok")


if __name__ == "__main__":
    _self_check()
