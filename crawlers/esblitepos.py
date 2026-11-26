"""Crawler ESB POS Lite (https://poslite.esb.co.id) — port alur terbukti dari
scraping-efd-python/utils/esb_lite_crawler.py (pool Selenium/2captcha diabaikan;
fokus alur lokal: page headed + fetch in-page ala esbv2).

Alur: login SPA Angular (reCAPTCHA ditangani Camoufox saat submit; interaktif ->
pause user) -> capture respons /auth/login (code 200 + data.authKey; fallback
localStorage "user"/"authKey") -> Bearer authKey + header CompanyGroupID +
X-Signature HMAC-SHA256(secret, "{ts}\\r\\n{METHOD}\\r\\n/v1{path}") ->
cabang /api/web/v1/statistic-filter/list-branch (grup brand -> data[]) ->
transaksi /api/web/v1/report/sales-detail (detail.data + _pagination.pageCount,
perPage 100). API lewat fetch in-page agar cookie sesi + fingerprint Camoufox
terpakai (pola esbv2: request Node-side terblokir).
"""
import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Iterator, List, Tuple
from urllib.parse import urlencode

from app import browser
from app.errors import CrawlError
from .base import Crawler, CrawlContext, date_chunks, stable_trx_id

log = logging.getLogger("sla.crawler.esblitepos")

ORIGIN = "https://poslite.esb.co.id"
LOGIN_URL = ORIGIN + "/login"
API_PREFIX = "/api/web/v1"
BRANCH_PATH = API_PREFIX + "/statistic-filter/list-branch"
SALES_DETAIL_PATH = API_PREFIX + "/report/sales-detail"
# ponytail: secret dari bundle Angular POS Lite (chunk API service); override via env jika ESB rotate
APP_SECRET = os.getenv("ESB_LITE_APP_SECRET", "SvQN9MR2")
PER_PAGE = 100
LOGIN_WAIT_S = 25
SUBMIT_WAIT_S = 60

LOGIN_USER = 'input[type="email"], input[name="email"]'
LOGIN_USER_ALT = 'input[placeholder*="email" i], input[id*="email"]'
LOGIN_PASS = 'input[type="password"], input[name="password"]'
LOGIN_PASS_ALT = 'input[placeholder*="password" i], input[id*="password"]'
LOGIN_SUBMIT = 'button[type="submit"], input[type="submit"]'
LOGIN_SUBMIT_ALT = '//button[contains(text(), "Masuk") or contains(text(), "Login")]'
LOGIN_ERROR = '.alert-danger, .error-message, .login-error, [role="alert"], .invalid-feedback, .text-danger'

# JS baca auth dari storage SPA (key "user" + legacy), port _BROWSER_AUTH_JS referensi
AUTH_JS = """() => {
    function parseUser(store) {
        try { const raw = store.getItem('user'); if (raw) return JSON.parse(raw); } catch (e) {}
        return null;
    }
    const user = parseUser(localStorage) || parseUser(sessionStorage);
    return {
        authKey: (user && user.authKey) || localStorage.getItem('authKey') || sessionStorage.getItem('authKey') || null,
        token: localStorage.getItem('token') || sessionStorage.getItem('token') || null,
        userID: (user && user.userID != null ? user.userID : null) || localStorage.getItem('userID') || null,
        companyGroupID: user && user.companyGroupID != null ? user.companyGroupID : null,
    };
}"""


def sign_path(api_path: str) -> str:
    """Path untuk HMAC: GET + /v1{suffix setelah /api/web/v1} (referensi _esb_sign_path)."""
    path = api_path.split("?", 1)[0]
    if path.startswith(API_PREFIX):
        path = path[len(API_PREFIX):]
    return f"/v1{path}"


def compute_signature(method: str, api_path: str, ts: int = None) -> str:
    """X-Signature '{ts}:{hmac_sha256(secret, ts\\r\\nMETHOD\\r\\n/v1path)}' (referensi terbukti)."""
    ts = str(int(time.time()) if ts is None else ts)
    message = f"{ts}\r\n{method.upper()}\r\n{sign_path(api_path)}"
    digest = hmac.new(APP_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{ts}:{digest}"


def auth_key_from_login_api(api_response) -> str:
    """authKey hanya dari respons login code 200 (referensi _auth_key_from_login_api)."""
    if not isinstance(api_response, dict) or api_response.get("code") != 200:
        return ""
    data = api_response.get("data")
    if isinstance(data, dict) and data.get("authKey"):
        return str(data["authKey"])
    return ""


def flatten_branches(response_data) -> List[dict]:
    """list-branch: grup brand [{parentID, parentName, data: [branch...]}] -> baris datar."""
    if isinstance(response_data, dict):
        response_data = response_data.get("data")
    out = []
    if not isinstance(response_data, list):
        return out
    for item in response_data:
        if isinstance(item, dict) and isinstance(item.get("data"), list):
            for branch in item["data"]:
                if isinstance(branch, dict):
                    out.append({
                        "branchID": branch.get("branchID"),
                        "branchName": branch.get("branchName"),
                        "parentID": item.get("parentID"),
                        "parentName": item.get("parentName"),
                    })
    return out


def report_params(a: str, b: str, branch_id: str = "", page: int = 1,
                  per_page: int = PER_PAGE) -> dict:
    """Field filter persis referensi (kosong = tanpa filter)."""
    return {
        "startDate": a, "endDate": b,
        "companyID": "", "brandID": "", "branchID": branch_id,
        "visitPurposeID": "", "paymentMethodID": "", "salesNum": "",
        "cashierName": "", "salesTypeIDs": "", "sort": "",
        "page": str(page), "perPage": str(per_page),
    }


def _norm(v) -> str:
    """Uang/angka API (int/float/string) -> string desimal exact untuk kolom preview."""
    if v is None or v == "":
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _parse_dt(v) -> str:
    """'15/11/2026' (DD/MM/YYYY, opsional jam) -> ISO; selain itu diteruskan apa adanya."""
    s = str(v or "").strip()
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})(.*)", s)
    if m:
        d, mo, y, rest = m.groups()
        return f"{y}-{mo}-{d}{rest.rstrip()}"
    return s


class EsbLitePosCrawler(Crawler):
    pos_code = "esblitepos"
    name = "ESB POS Lite"
    base_url = ORIGIN
    max_range_days = 31
    has_branch_api = True
    gate_kind = "reCAPTCHA (Camoufox; pause user bila interaktif)"

    def __init__(self):
        self.auth_key = ""
        self.api_token = ""
        self.company_group_id = ""
        self._branch_map = {}
        self._branch_by_name = {}
        self._branch_brand = {}

    # ---------- fetch in-page (fingerprint Camoufox + cookie sesi, pola esbv2) ----------
    def _fetch(self, ctx: CrawlContext, url: str, headers: dict) -> dict:
        return ctx.page.evaluate(
            """async (a) => {
                const r = await fetch(a.url, {method: 'GET', headers: a.headers, credentials: 'include'});
                return {status: r.status, text: await r.text()};
            }""",
            {"url": url, "headers": headers},
        )

    def _api_get(self, ctx: CrawlContext, api_path: str, params: dict,
                 referer: str = None) -> dict:
        ctx.session.check_alive()
        bearer = self.api_token or self.auth_key
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": referer or ORIGIN + "/",
            "Origin": ORIGIN,
            "X-Signature": compute_signature("GET", api_path),
        }
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        if self.company_group_id:
            headers["CompanyGroupID"] = str(self.company_group_id)
        url = f"{ORIGIN}{api_path}?{urlencode(params)}"
        r = self._fetch(ctx, url, headers)
        if r["status"] in (401, 403):
            raise CrawlError("network", f"API ESB Lite ditolak ({r['status']}) — sesi perlu login ulang")
        if r["status"] != 200:
            raise CrawlError("network", f"API ESB Lite {api_path} HTTP {r['status']}")
        try:
            data = json.loads(r["text"])
        except Exception as exc:
            raise CrawlError("ui_changed", f"Respons {api_path} ESB Lite bukan JSON: {exc}") from exc
        if isinstance(data, dict) and data.get("code") not in (None, 200):
            raise CrawlError("ui_changed",
                             f"API ESB Lite {api_path} code {data.get('code')}: {str(data.get('message'))[:120]}")
        return data

    # ---------- gate (reCAPTCHA invisible vs interaktif, pola loyverse) ----------
    def _recaptcha_interactive(self, page) -> bool:
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

    def gate_active(self, page) -> bool:
        url = (page.url or "").lower()
        if "poslite.esb.co.id" in url and "/login" in url:
            return self._recaptcha_interactive(page)
        return browser.is_gated(page)

    # ---------- login ----------
    def _read_auth(self, page) -> dict:
        try:
            data = page.evaluate(AUTH_JS)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _adopt_auth(self, page, api_data: dict = None) -> bool:
        """authKey dari respons login / storage; token Bearer = localStorage token, fallback authKey."""
        storage = self._read_auth(page)
        auth_key = auth_key_from_login_api({"code": 200, "data": api_data}) if api_data else ""
        auth_key = auth_key or str(storage.get("authKey") or "")
        if not auth_key:
            return False
        self.auth_key = auth_key
        self.api_token = str(storage.get("token") or "") or auth_key
        cgid = (api_data or {}).get("companyGroupID")
        if cgid is None:
            cgid = storage.get("companyGroupID")
        self.company_group_id = str(cgid) if cgid is not None else ""
        return True

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
        page = ctx.page
        resp = ctx.session.goto(LOGIN_URL)
        ctx.after_nav(resp)
        page.wait_for_timeout(1500)
        if "/login" not in page.url.lower() and self._adopt_auth(page):
            log.info("ESB Lite reuse sesi profil (authKey di storage)")
            return
        # reset sesi ala referensi (_reset_browser_session_for_login): login fresh per job
        page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
        resp = ctx.session.goto(LOGIN_URL)
        ctx.after_nav(resp)
        try:
            page.wait_for_selector(LOGIN_PASS, timeout=30000)
        except Exception as exc:
            raise CrawlError("ui_changed",
                             f"Form login ESB Lite tidak tampil (input password absen): {exc}") from exc
        user_loc = page.locator(LOGIN_USER).first
        if not user_loc.count():
            user_loc = page.locator(LOGIN_USER_ALT).first
        if not user_loc.count():
            raise CrawlError("ui_changed", "Input email login ESB Lite tidak ditemukan")
        user_loc.fill(username)
        pass_loc = page.locator(LOGIN_PASS).first
        if not pass_loc.count():
            pass_loc = page.locator(LOGIN_PASS_ALT).first
        pass_loc.fill(password)

        captured = {}

        def on_resp(r):
            try:
                if "/auth/login" in r.url and r.request.method == "POST":
                    captured["status"] = r.status
                    try:
                        captured["json"] = r.json()
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", on_resp)
        try:
            btn = page.locator(LOGIN_SUBMIT).first
            if not btn.count():
                btn = page.locator(LOGIN_SUBMIT_ALT).first
            if not btn.count():
                raise CrawlError("ui_changed", "Tombol submit login ESB Lite tidak ditemukan")
            btn.click()
            deadline = time.monotonic() + SUBMIT_WAIT_S
            api = None
            while time.monotonic() < deadline:
                api = captured.get("json")
                if isinstance(api, dict) and api.get("code") is not None:
                    break
                if self._read_auth(page).get("authKey"):
                    break
                page.wait_for_timeout(300)
            if isinstance(api, dict) and api.get("code") not in (None, 200):
                raise CrawlError("auth", f"Login ESB Lite ditolak: {api.get('message') or 'code ' + str(api.get('code'))}")
            data = api.get("data") if isinstance(api, dict) and api.get("code") == 200 else None
            if not self._adopt_auth(page, data if isinstance(data, dict) else None):
                if self.gate_active(page):
                    ctx.check_gate()  # captcha interaktif -> pause user (W2), lalu submit ulang
                    deadline = time.monotonic() + LOGIN_WAIT_S
                    while time.monotonic() < deadline and not self._adopt_auth(page):
                        page.wait_for_timeout(300)
                if not self._adopt_auth(page):
                    err = self._login_error(page)
                    raise CrawlError("auth", f"Login ESB Lite ditolak: {err or 'authKey tidak muncul'}")
        finally:
            page.remove_listener("response", on_resp)
        log.info("ESB Lite login ok companyGroupID=%s", self.company_group_id)

    # ---------- cabang ----------
    def list_branches(self, ctx: CrawlContext) -> List[Tuple[str, str]]:
        today = time.strftime("%Y-%m-%d")
        data = self._api_get(ctx, BRANCH_PATH, report_params(today, today, per_page=10))
        branches = flatten_branches(data)
        pairs, seen = [], set()
        for b in branches:
            key = str(b.get("branchID") or "")
            name = str(b.get("branchName") or "") or key
            if not key or key in seen:
                continue
            seen.add(key)
            pairs.append((key, name))
            self._branch_map[key] = name
            self._branch_by_name[name.lower()] = key
            self._branch_brand[key] = str(b.get("parentID") or "")
        if not pairs:
            raise CrawlError("ui_changed", "Daftar cabang ESB Lite kosong (statistic-filter/list-branch)")
        log.info("ESB Lite branches=%s", [n for _, n in pairs])
        return pairs

    def _resolve_branch(self, ctx: CrawlContext, branch_key: str) -> Tuple[str, str, str]:
        """key boleh ID atau nama (input manual / kandidat job lama) -> (ID, nama, brandID)."""
        if branch_key == "all":
            return "", "All Branch", ""
        if not self._branch_map:
            self.list_branches(ctx)
        if branch_key in self._branch_map:
            return branch_key, self._branch_map[branch_key], self._branch_brand.get(branch_key, "")
        bid = self._branch_by_name.get(branch_key.lower())
        if bid:
            return bid, self._branch_map[bid], self._branch_brand.get(bid, "")
        avail = ", ".join(list(self._branch_map.values())[:12])
        raise CrawlError("ui_changed",
                         f"Cabang ESB Lite tidak ditemukan: {branch_key}. Tersedia: {avail}")

    # ---------- transaksi ----------
    def scrape_branch(self, ctx: CrawlContext, branch_key: str,
                      date_from: str, date_to: str) -> Iterator[List[dict]]:
        bid, bname, brand = self._resolve_branch(ctx, branch_key)
        for a, b in date_chunks(date_from, date_to, self.max_range_days):
            page_n, total_pages = 1, 1
            while page_n <= total_pages:
                params = report_params(a, b, bid, page_n)
                params["brandID"] = brand
                data = self._api_get(ctx, SALES_DETAIL_PATH, params)
                detail = data.get("detail") or {}
                rows = detail.get("data") or []
                pagination = detail.get("_pagination") or {}
                try:
                    total_pages = int(pagination.get("pageCount") or 1)
                except (TypeError, ValueError):
                    total_pages = 1
                if page_n == 1:
                    log.info("ESB Lite chunk %s..%s branch=%s pages=%s total=%s",
                             a, b, bname or "all", total_pages, pagination.get("totalCount"))
                if rows:
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        r.setdefault("trx_id", str(r.get("salesID") or r.get("salesNum") or stable_trx_id(r)))
                        r.setdefault("trx_date", _parse_dt(r.get("salesDate") or r.get("createdDate") or ""))
                        if "grand_total" not in r:
                            g = _norm(r.get("grandTotal") or r.get("nettSales") or r.get("totalSales"))
                            if g:
                                r["grand_total"] = g
                        if "total_tax" not in r:
                            t = _norm(r.get("taxTotal"))
                            if t:
                                r["total_tax"] = t
                        r.setdefault("branch_name", bname)
                    yield [r for r in rows if isinstance(r, dict)]
                if not rows:
                    break
                page_n += 1
                if page_n <= total_pages:
                    ctx.page.wait_for_timeout(500)  # jeda antar halaman ala referensi


def _self_check() -> None:
    assert sign_path("/api/web/v1/report/sales-detail?x=1") == "/v1/report/sales-detail"
    assert sign_path("/api/web/v1/statistic-filter/list-branch") == "/v1/statistic-filter/list-branch"
    # known-answer HMAC (vektor dihitung independen dari formula referensi)
    assert compute_signature("GET", "/api/web/v1/report/sales-detail", ts=1765770559) == (
        "1765770559:6630f7195dee5b160d1f8ab843dd557973df76aa74c7ed32412e097a74c6b63f")
    assert auth_key_from_login_api({"code": 200, "data": {"authKey": "x"}}) == "x"
    assert auth_key_from_login_api({"code": 200, "data": {}}) == ""
    assert auth_key_from_login_api({"code": 401, "data": {"authKey": "x"}}) == ""
    groups = [{"parentID": "7", "parentName": "Brand", "data": [
        {"branchID": "9", "branchName": "Alpha"}, {"branchID": "10", "branchName": "Beta"}]}]
    flat = flatten_branches({"data": groups})
    assert [b["branchID"] for b in flat] == ["9", "10"] and flat[0]["parentID"] == "7"
    assert flatten_branches(groups) == flat
    assert flatten_branches(None) == []
    p = report_params("2026-10-01", "2026-10-31", "9", 2)
    assert p["startDate"] == "2026-10-01" and p["branchID"] == "9" and p["page"] == "2" and p["perPage"] == "100"
    assert _norm(1250000.0) == "1250000" and _norm("1250.50") == "1250.50" and _norm(None) == ""
    assert _parse_dt("15/11/2026 10:00") == "2026-11-15 10:00"
    assert _parse_dt("2026-11-15") == "2026-11-15"
    print("esblitepos crawler self_check ok")


if __name__ == "__main__":
    _self_check()
