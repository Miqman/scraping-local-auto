"""Crawler SiberPOS (https://my.siberpos.com) — port alur terbukti dari
scraping-efd-python/utils/siberposv2_crawler.py + siberpos_crawler.py +
siberpos_login.py + turnstile_login.py (proxy diabaikan; fokus alur lokal).

Alur: profil Camoufox per akun -> home -> reuse sesi bila cookie XSRF-TOKEN ada
-> gate Cloudflare/Turnstile (auto-click sekali + pause user bila interaktif)
-> login React-Aria -> API /api/summary/transactions (status=paid, outlet=all,
paginasi meta.current_page/last_page) dengan header x-xsrf-token.
"""
import re
import time
from typing import Iterator, List, Tuple

from app.errors import CrawlError
from .base import Crawler, CrawlContext, date_chunks, stable_trx_id

ORIGIN = "https://my.siberpos.com"
LOGIN_USER = 'input[name="username"], input[name="email"], input[type="email"]'
LOGIN_PASS = 'input[name="password"], input[type="password"]'
LOGIN_SUBMIT = 'button[type="submit"], input[type="submit"]'
LOGIN_ERROR = '.alert-danger, .error-message, .login-error, [role="alert"]'
CF_KEYS = ("cloudflare", "verify you are human", "security verification", "cf-turnstile", "__cf_chl")
TOKEN_MIN_LEN = 20
GATE_GRACE_S = 15
SUBMIT_WAIT_S = 20
STALE_ABORT_S = 12


def _rp_to_num(v) -> str:
    """'Rp 12.345,00' / '12345.00' -> string desimal exact untuk kolom preview."""
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    s = re.sub(r"[^\d,.\-]", "", s)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):  # id-ID: 12.345,00
            s = s.replace(".", "").replace(",", ".")
        else:  # en-US: 1,250,000.00
            s = s.replace(",", "")
    elif "," in s:
        tail = s.rsplit(",", 1)[1]
        s = s.replace(",", ".") if len(tail) <= 2 else s.replace(",", "")
    return s


def _parse_dt(v) -> str:
    """'10/11/2026, 22:40:59 WIB' -> '2026-11-10 22:40:59' (zona waktu akun POS)."""
    s = str(v or "").strip()
    m = re.match(r"(\d{2})/(\d{2})/(\d{4}),?\s+(\d{2}:\d{2}:\d{2})", s)
    if m:
        d, mo, y, t = m.groups()
        return f"{y}-{mo}-{d} {t}"
    return s


class SiberPosCrawler(Crawler):
    pos_code = "siberpos"
    name = "SiberPOS"
    base_url = ORIGIN
    max_range_days = 31
    has_branch_api = False  # referensi memakai outlet=all (agregat semua outlet)
    gate_kind = "Cloudflare Turnstile"

    # ---------- predikat halaman (port turnstile_login.py) ----------
    def _token(self, page) -> str:
        try:
            return str(page.evaluate(
                """() => {
                    for (const el of document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]')) {
                        const v = (el.value || '').trim();
                        if (v) return v;
                    }
                    return '';
                }"""
            ) or "").strip()
        except Exception:
            return ""

    def _token_valid(self, token: str) -> bool:
        return bool(token) and token not in ("0.", "0") and len(token) >= TOKEN_MIN_LEN

    def _widget_success(self, page) -> bool:
        try:
            return bool(page.evaluate(
                """() => {
                    const w = document.querySelector('.cf-turnstile, [data-turnstile-widget]');
                    if (!w) return false;
                    const st = (w.getAttribute('data-state') || '').toLowerCase();
                    return st === 'success' || st === 'solved';
                }"""
            ))
        except Exception:
            return False

    def _login_form_visible(self, page) -> bool:
        try:
            return page.locator(LOGIN_PASS).count() > 0
        except Exception:
            return False

    def _is_login_like(self, page) -> bool:
        url = (page.url or "").lower()
        return "login" in url or "signin" in url or self._login_form_visible(page)

    def _cf_detected(self, page) -> bool:
        try:
            combined = (page.content() + "\n" + (page.locator("body").inner_text() or "")).lower()
            return any(k in combined for k in CF_KEYS)
        except Exception:
            return False

    def _has_widget(self, page) -> bool:
        if self._token_valid(self._token(page)):
            return False
        if self._cf_detected(page):
            return True
        try:
            return page.locator(
                'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], '
                '.cf-turnstile, input[name="cf-turnstile-response"]'
            ).count() > 0
        except Exception:
            return False

    def _satisfied(self, page) -> bool:
        if self._token_valid(self._token(page)):
            return True
        if self._is_login_like(page):
            return self._widget_success(page) or not self._has_widget(page)
        return not self._has_widget(page)

    def _is_cf_gate(self, page) -> bool:
        if self._login_form_visible(page):
            return False
        return self._cf_detected(page) or self._has_widget(page)

    def gate_active(self, page, ctx=None) -> bool:
        """Pause user hanya bila challenge belum lolos: halaman challenge, atau
        form login dengan widget Turnstile yang belum satisfied. Sesi yang sudah
        login (cookie XSRF + bukan halaman login) bukan gate — widget Turnstile sisa
        di dashboard tidak boleh menahan job (issue: stuck MENUNGGU USER pasca-login)."""
        if ctx is not None and self._authenticated(ctx):
            return False
        if self._login_form_visible(page):
            return self._has_widget(page) and not self._satisfied(page)
        return self._is_cf_gate(page)

    def _click_turnstile(self, page) -> None:
        for sel in ("iframe[src*='challenges.cloudflare.com']", "iframe[src*='turnstile']", ".cf-turnstile iframe"):
            try:
                iframe = page.locator(sel).first
                iframe.wait_for(state="visible", timeout=2500)
                box = iframe.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    page.mouse.move(box["x"] + min(28, box["width"] * 0.18), box["y"] + box["height"] / 2)
                    page.wait_for_timeout(150)
                    page.mouse.click(box["x"] + min(28, box["width"] * 0.18), box["y"] + box["height"] / 2)
                    return
            except Exception:
                continue
        for frame in page.frames:
            fu = (frame.url or "").lower()
            if "challenges.cloudflare.com" not in fu and "turnstile" not in fu:
                continue
            for sel in ("input[type='checkbox']", "label", ".ctp-checkbox-label", "span.mark", "body"):
                try:
                    frame.locator(sel).first.click(timeout=2000)
                    return
                except Exception:
                    continue
    def gate_assist(self, page) -> None:
        """Auto-click checkbox Turnstile selama pause (cepat, tanpa wait panjang)."""
        try:
            if self._token_valid(self._token(page)):
                return
            for sel in ("iframe[src*='challenges.cloudflare.com']", "iframe[src*='turnstile']", ".cf-turnstile iframe"):
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    box = loc.bounding_box()
                    if box and box["width"] > 0 and box["height"] > 0:
                        page.mouse.click(box["x"] + min(28, box["width"] * 0.18),
                                         box["y"] + box["height"] / 2)
                        return
        except Exception:
            pass


    def _click_submit(self, page) -> bool:
        try:
            btn = page.locator(LOGIN_SUBMIT).first
            if btn.count() and btn.is_enabled():
                btn.click(timeout=3000)
                return True
        except Exception:
            pass
        return False

    # ---------- sesi & login (port siberpos_login.py) ----------
    def _xsrf(self, ctx: CrawlContext) -> str:
        for cookie in ctx.session._ctx.cookies():
            if cookie.get("name") == "XSRF-TOKEN":
                return cookie.get("value") or ""
        return ""

    def _authenticated(self, ctx: CrawlContext) -> bool:
        return bool(self._xsrf(ctx)) and not self._is_login_like(ctx.page)

    def _pass_cloudflare_gate(self, ctx: CrawlContext) -> None:
        page = ctx.page
        page.wait_for_timeout(1500)
        deadline = time.monotonic() + GATE_GRACE_S
        while time.monotonic() < deadline:
            if self._login_form_visible(page) or not self._is_cf_gate(page):
                return
            self._click_turnstile(page)
            page.wait_for_timeout(1500)
        if self._is_cf_gate(page):
            ctx.check_gate()  # interaktif -> pause user di browser headed (W2)

    def _login_after_fill(self, ctx: CrawlContext) -> bool:
        """Interleave klik submit + klik Turnstile (port login_after_fill)."""
        page = ctx.page
        deadline = time.monotonic() + SUBMIT_WAIT_S
        stale_since = None
        self._click_submit(page)
        last_submit = time.monotonic()
        last_ts = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if self._authenticated(ctx):
                return True
            widget = self._has_widget(page)
            if widget and not self._token_valid(self._token(page)):
                if stale_since is None:
                    stale_since = now
                elif now - stale_since >= STALE_ABORT_S:
                    break
            else:
                stale_since = None
            if widget and now - last_ts >= 0.45:
                last_ts = now
                self._click_turnstile(page)
            if self._satisfied(page) and now - last_submit >= 0.35:
                last_submit = now
                self._click_submit(page)
            page.wait_for_timeout(200)
        return self._authenticated(ctx)

    def login(self, ctx: CrawlContext, username: str, password: str) -> None:
        page = ctx.page
        ctx.after_nav(ctx.session.goto(self.base_url + "/"))
        page.wait_for_timeout(2000)
        if self._authenticated(ctx):
            return  # sesi profil masih valid (reuse, seperti referensi)
        self._pass_cloudflare_gate(ctx)
        if self._authenticated(ctx):
            return
        if not self._login_form_visible(page):
            ctx.after_nav(ctx.session.goto(self.base_url + "/login"))
            page.wait_for_timeout(1500)
        if not self._login_form_visible(page):
            raise CrawlError("ui_changed", "Form login SiberPOS tidak tampil (input password absen)")
        page.locator(LOGIN_USER).first.fill(username)
        page.locator(LOGIN_PASS).first.fill(password)
        self._login_after_fill(ctx)
        page.wait_for_timeout(2500)
        if not self._authenticated(ctx) and self.gate_active(page):
            ctx.check_gate()  # user selesaikan captcha, lalu submit ulang
            self._login_after_fill(ctx)
            page.wait_for_timeout(2500)
        if not self._authenticated(ctx):
            err = page.query_selector(LOGIN_ERROR)
            if err is not None and err.inner_text().strip():
                raise CrawlError("auth", f"Login SiberPOS ditolak: {err.inner_text().strip()[:200]}")
            raise CrawlError("auth", "Login SiberPOS ditolak (cookie XSRF-TOKEN tidak terbit)")

    # ---------- data (port siberpos_crawler.py _scrape_my_siberpos_api) ----------
    def list_branches(self, ctx: CrawlContext) -> List[Tuple[str, str]]:
        return [("all", "Semua Outlet")]

    def scrape_branch(self, ctx: CrawlContext, branch_key: str,
                      date_from: str, date_to: str) -> Iterator[List[dict]]:
        for a, b in date_chunks(date_from, date_to, self.max_range_days):
            page_n = 1
            while True:
                ctx.session.check_alive()
                xsrf = self._xsrf(ctx)
                if not xsrf:
                    raise CrawlError("network", "Cookie XSRF-TOKEN hilang di tengah scrape (sesi putus)")
                url = (f"{self.base_url}/api/summary/transactions?status=paid&page={page_n}"
                       f"&outlet={branch_key}&brand=all&dates[start]={a}&dates[end]={b}")
                resp = ctx.session._ctx.request.get(url, headers={
                    "accept": "application/json, text/plain, */*",
                    "referer": f"{self.base_url}/summary/transactions?success=",
                    "x-requested-with": "XMLHttpRequest",
                    "x-xsrf-token": xsrf,
                })
                if resp.status in (401, 403):
                    raise CrawlError("network", f"API transaksi menolak ({resp.status}) — sesi perlu login ulang")
                if resp.status != 200:
                    raise CrawlError("network", f"API transaksi HTTP {resp.status}")
                data = resp.json()
                rows = data.get("data") or []
                if rows:
                    for r in rows:
                        if isinstance(r, dict):
                            r.setdefault("trx_id", str(r.get("id") or r.get("notaNumber") or stable_trx_id(r)))
                            r.setdefault("trx_date", _parse_dt(r.get("createdAt") or r.get("created_at") or ""))
                            if "grand_total" not in r:
                                num = _rp_to_num(r.get("total") or r.get("total_rp"))
                                if num:
                                    r["grand_total"] = num
                            outlet = r.get("outlet")
                            if isinstance(outlet, dict):
                                r.setdefault("outlet_name", outlet.get("name") or "")
                            cashier = r.get("cashier")
                            if isinstance(cashier, dict):
                                r.setdefault("cashier_name", (cashier.get("user") or {}).get("name") or "")
                            ch = r.get("orderChannelDisplay")
                            if isinstance(ch, dict):
                                r.setdefault("channel", ch.get("label") or r.get("orderChannel") or "")
                    yield rows
                meta = data.get("meta") or {}
                if meta.get("current_page", page_n) < meta.get("last_page", page_n):
                    page_n += 1
                else:
                    break


def _self_check() -> None:
    """Regresi issue stuck MENUNGGU USER pasca-login: widget Turnstile sisa di
    dashboard bukan gate bila sesi sudah login; challenge/login asli tetap gate."""
    class Loc:
        def __init__(self, n): self._n = n
        @property
        def first(self): return self
        def count(self): return self._n

    class Page:
        def __init__(self, url, html, pw=0, widget=0, token=""):
            self.url, self._html, self._pw, self._widget, self._token = url, html, pw, widget, token
        def content(self): return self._html
        def locator(self, sel):
            return Loc(self._pw if "password" in sel else self._widget)
        def evaluate(self, script, *a):
            if "cf-turnstile-response" in script: return self._token
            if "data-state" in script: return False
            return ""

    class Sess:
        def __init__(self, xsrf): self._xsrf = xsrf
        @property
        def _ctx(self): return self
        def cookies(self):
            return [{"name": "XSRF-TOKEN", "value": self._xsrf}] if self._xsrf else []

    class Ctx:
        def __init__(self, page, xsrf=""): self.page, self.session = page, Sess(xsrf)

    c = SiberPosCrawler()
    cf_html = "<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script>"
    dash = Page("https://my.siberpos.com/summary/transactions", cf_html, pw=0, widget=1)
    login = Page("https://my.siberpos.com/login", "<form></form>", pw=1, widget=1)
    challenge = Page("https://my.siberpos.com/cdn-cgi/challenge-platform/x", "<html>cloudflare</html>", pw=0, widget=1)
    # dashboard + sesi login -> BUKAN gate (fix issue)
    assert c.gate_active(dash, Ctx(dash, xsrf="valid")) is False
    # tanpa sesi, widget di dashboard tetap gate (perilaku lama terjaga)
    assert c.gate_active(dash, Ctx(dash, xsrf="")) is True
    # form login dengan widget belum satisfied -> gate
    assert c.gate_active(login, Ctx(login, xsrf="")) is True
    # halaman challenge asli -> gate
    assert c.gate_active(challenge, Ctx(challenge, xsrf="")) is True
    # tanpa ctx (signature lama) tetap jalan
    assert c.gate_active(dash) is True
    print("siberpos gate self_check ok")


if __name__ == "__main__":
    _self_check()