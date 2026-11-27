"""Crawler Majoo — port dari MaphyMerchantApps/scraping-service .../MajooV2POSSvc.java
(2captcha & proxy diabaikan; Turnstile dilewati Camoufox anti-detect + pause user bila interaktif).

Alur Java yang dipertahankan:
1. Guard token: baca token dari DB (padanan lokal: tabel `sessions` terenkripsi) ->
   checkToken via GET dashboard.majoo.id/mayang/cabang/support_by_cabang (header `Token`);
   valid -> reuse tanpa login browser (hemat captcha).
2. Login: Camoufox headed buka dashboard.majoo.id/auth/login (Turnstile di latar belakang);
   token Turnstile dipanen dari halaman (managed auto-pass; interaktif -> pause user W2);
   POST mayang.majoo.id/0_0_11/user/login (form email/password/is_cms/is_retina,
   header X-Security-Key = token Turnstile) -> JSON {status, token}.
   Di sini token ditangkap dari respons login yang dipicu form situs sendiri.
3. WAJIB: simpan token ke DB (sessions, AES-256-GCM) — padanan saveToDB cartenz.
4. Cabang: GET support_by_cabang (header Token, UA PostmanRuntime) -> data[].id_cabang/cabang.
5. Transaksi per cabang: services.majoo.id/svc-data-reporting/api/v1/detail_sales
   (+ /total-page) header Authorization: Bearer, outlet_id per cabang,
   start_date/end_date jam 00:00/23:59, limit 100, paginasi total_page.
"""
import json
import logging
import re
import time
from typing import Iterator, List, Tuple

from app import browser, crypto, db
from app.errors import CrawlError
from .base import Crawler, CrawlContext, date_chunks, stable_trx_id

log = logging.getLogger("sla.crawler.majoo")

BASE = "https://dashboard.majoo.id"
LOGIN_URL = BASE + "/auth/login"
LOGIN_API = "https://mayang.majoo.id/0_0_11/user/login"
BRANCH_API = BASE + "/mayang/cabang/support_by_cabang"
SVC = "https://services.majoo.id"
SALES_PATH = "/svc-data-reporting/api/v1/detail_sales"
UA = "PostmanRuntime/7.26.8"  # penting: tanpa ini API membalas HTML (komentar Java)
TURNSTILE_GRACE_S = 12


def sales_params(a: str, b: str, outlet_id: str, page: int) -> str:
    return (f"limit=100&page={page}&template_id=0&min_total_sales=0&min_gross_sales=0"
            f"&is_payment=0&start_date={a}%2000%3A00&end_date={b}%2023%3A59"
            f"&outlet_id={outlet_id}")


class MajooCrawler(Crawler):
    pos_code = "majoo"
    name = "Majoo"
    base_url = BASE
    max_range_days = 31
    has_branch_api = True
    gate_kind = "Cloudflare Turnstile (latar belakang; pause user bila interaktif)"

    def __init__(self):
        self.token = ""
        self._branch_map = {}
        self._branch_by_name = {}

    # ---------- Turnstile / gate ----------
    def _turnstile_token(self, page) -> str:
        try:
            return str(page.evaluate(
                "() => { const i = document.querySelector('input[name=\"cf-turnstile-response\"]');"
                " return i ? (i.value || '') : ''; }") or "")
        except Exception:
            return ""

    def _has_widget(self, page) -> bool:
        try:
            return page.locator(
                'iframe[src*="challenges.cloudflare.com"], .cf-turnstile, '
                'input[name="cf-turnstile-response"]').count() > 0
        except Exception:
            return False

    def gate_active(self, page, ctx=None) -> bool:
        if "/auth/login" not in (page.url or ""):
            return browser.is_gated(page)
        return self._has_widget(page) and not self._turnstile_token(page)

    # ---------- token guard (padanan DB cartenz) ----------
    def _check_token(self, ctx: CrawlContext, token: str) -> bool:
        try:
            r = ctx.session._ctx.request.get(BRANCH_API, headers={
                "User-Agent": UA, "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest", "Token": token})
            body = r.text()
            if r.status == 401:
                return False
            low = body.lower()
            if "expired token" in low or '"err_code":"tex-01"' in low:
                return False
            if body.strip().startswith("<"):
                return False
            if r.status == 200:
                try:
                    d = json.loads(body)
                except Exception:
                    return False
                if isinstance(d, dict) and "status" in d:
                    return bool(d["status"])
                return True
            return False
        except Exception:
            return True  # error jaringan: anggap valid sementara (ala Java, hemat captcha)

    def _local_storage_token(self, page) -> str:
        try:
            return str(page.evaluate(
                "() => { for (let i = 0; i < localStorage.length; i++) {"
                " const k = localStorage.key(i);"
                " if (/token|auth/i.test(k)) { const v = localStorage.getItem(k) || '';"
                " if (v.length > 20) return v; } } return ''; }") or "")
        except Exception:
            return ""

    def login(self, ctx: CrawlContext, username: str, password: str) -> None:
        ctx.page.set_default_timeout(60000)
        acc_id = db.get_job(ctx.job_id)["account_id"]
        enc = db.get_session_token(acc_id)
        if enc:
            try:
                saved = crypto.decrypt(enc)
            except Exception:
                saved = ""
            if saved and self._check_token(ctx, saved):
                self.token = saved
                log.info("Majoo reuse token dari sessions (account %s) tanpa login browser", acc_id)
                return
        resp = ctx.session.goto(LOGIN_URL)  # Camoufox hanya untuk login (sesuai desain)
        ctx.after_nav(resp)
        page = ctx.page
        log.info("Majoo login page url=%s", page.url)
        page.wait_for_selector('input[type="password"]', timeout=30000)
        page.fill('input[name="email"], input[type="email"]', username)
        page.fill('input[type="password"]', password)
        # Turnstile latar belakang: beri waktu managed challenge lolos sendiri
        deadline = time.monotonic() + TURNSTILE_GRACE_S
        while time.monotonic() < deadline and self._has_widget(page) and not self._turnstile_token(page):
            page.wait_for_timeout(1000)
        if self.gate_active(page):
            ctx.check_gate()  # interaktif -> pause user di browser headed (W2)
        captured = {}

        def on_resp(r):
            try:
                if "/user/login" in r.url and r.request.method == "POST":
                    d = r.json()
                    if isinstance(d, dict) and d.get("token"):
                        captured["token"] = d["token"]
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            for attempt in range(2):
                page.click('button[type="submit"]')
                end = time.monotonic() + 20
                while time.monotonic() < end:
                    if captured.get("token") or "/auth/login" not in page.url:
                        break
                    page.wait_for_timeout(500)
                if captured.get("token"):
                    break
                if self.gate_active(page):
                    ctx.check_gate()
            token = captured.get("token") or self._local_storage_token(page)
        finally:
            page.remove_listener("response", on_resp)
        if not token:
            low = page.content().lower()
            if ("password" in low or "email" in low) and any(
                    w in low for w in ("salah", "invalid", "incorrect", "tidak ditemukan")):
                raise CrawlError("auth", "Login Majoo ditolak: email/password salah")
            raise CrawlError("ui_changed",
                             "Token Majoo tidak tertangkap dari respons login maupun localStorage")
        self.token = token
        # langkah wajib ala Java saveToDB(cartenz): token persisten per akun, terenkripsi
        db.save_session_token(acc_id, crypto.encrypt(token))
        log.info("Majoo token tersimpan ke sessions (account %s)", acc_id)

    # ---------- cabang ----------
    def list_branches(self, ctx: CrawlContext) -> List[Tuple[str, str]]:
        r = ctx.session._ctx.request.get(BRANCH_API, headers={
            "User-Agent": UA, "Accept": "*/*", "Token": self.token})
        if r.status in (401, 403):
            raise CrawlError("network", f"API cabang Majoo ditolak ({r.status}) — sesi perlu login ulang")
        body = r.text()
        if body.strip().startswith("<"):
            raise CrawlError("network", "API cabang Majoo membalas HTML (sesi putus / UA salah)")
        if r.status != 200:
            raise CrawlError("network", f"API cabang Majoo HTTP {r.status}")
        data = json.loads(body).get("data") or []
        pairs = [(str(b.get("id_cabang")), str(b.get("cabang") or b.get("id_cabang")))
                 for b in data if isinstance(b, dict) and b.get("id_cabang")]
        self._branch_map = {k: n for k, n in pairs}
        self._branch_by_name = {n.lower(): k for k, n in pairs}
        log.info("Majoo branches=%s", [n for _, n in pairs])
        if not pairs:
            raise CrawlError("ui_changed", "Daftar cabang Majoo kosong (data[] support_by_cabang)")
        return pairs

    def _resolve_branch(self, ctx: CrawlContext, branch_key: str) -> Tuple[str, str]:
        if not self._branch_map:
            self.list_branches(ctx)
        if branch_key in self._branch_map:
            return branch_key, self._branch_map[branch_key]
        bid = self._branch_by_name.get(branch_key.lower())
        if bid:
            return bid, self._branch_map[bid]
        avail = ", ".join(list(self._branch_map.values())[:12])
        raise CrawlError("ui_changed", f"Cabang Majoo tidak ditemukan: {branch_key}. Tersedia: {avail}")

    # ---------- transaksi ----------
    def scrape_branch(self, ctx: CrawlContext, branch_key: str,
                      date_from: str, date_to: str) -> Iterator[List[dict]]:
        bid, btext = self._resolve_branch(ctx, branch_key)
        headers = {"User-Agent": UA, "Accept": "*/*", "Authorization": "Bearer " + self.token}
        for a, b in date_chunks(date_from, date_to, self.max_range_days):
            rt = ctx.session._ctx.request.get(
                SVC + SALES_PATH + "/total-page?" + sales_params(a, b, bid, 1), headers=headers)
            if rt.status in (401, 403):
                raise CrawlError("network", f"API transaksi Majoo ditolak ({rt.status}) — sesi perlu login ulang")
            if rt.status != 200:
                raise CrawlError("network", f"API total-page Majoo HTTP {rt.status}")
            try:
                total = int(json.loads(rt.text()).get("total_page") or 1)
            except Exception as exc:
                raise CrawlError("ui_changed", f"Respons total-page Majoo bukan JSON: {exc}") from exc
            log.info("Majoo branch=%s total_page=%s", btext, total)
            for pn in range(1, total + 1):
                r = ctx.session._ctx.request.get(
                    SVC + SALES_PATH + "?" + sales_params(a, b, bid, pn), headers=headers)
                if r.status in (401, 403):
                    raise CrawlError("network", f"API transaksi Majoo ditolak ({r.status})")
                if r.status != 200:
                    raise CrawlError("network", f"API transaksi Majoo HTTP {r.status} (page {pn})")
                rows = json.loads(r.text()).get("data") or []
                if rows:
                    if pn == 1:
                        log.info("Majoo row keys=%s", sorted(rows[0].keys()))
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        row.setdefault("trx_id", str(row.get("transaction_no_nota") or row.get("id") or stable_trx_id(row)))
                        dkey = next((k for k in row if "date" in k.lower() or "tanggal" in k.lower()), None)
                        if dkey:
                            row.setdefault("trx_date", str(row[dkey]).replace("T", " ").rstrip("Z"))
                        gkey = next((k for k in ("grand_total", "total_sales", "total", "gross_sales") if k in row), None)
                        if gkey and "grand_total" not in row:
                            row["grand_total"] = row[gkey]
                    yield rows


def _self_check() -> None:
    p = sales_params("2026-05-01", "2026-05-11", "123", 2)
    assert "outlet_id=123" in p and "page=2" in p
    assert "start_date=2026-05-01%2000%3A00" in p and "end_date=2026-05-11%2023%3A59" in p
    assert "limit=100" in p and "is_payment=0" in p
    print("majoo crawler self_check ok")


if __name__ == "__main__":
    _self_check()