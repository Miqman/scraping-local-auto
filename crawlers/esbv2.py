"""Crawler ESB POS v2 (https://erp.esb.co.id) — port alur terbukti dari
scraping-efd-python/utils/esb_posv2.py (proxy diabaikan; fokus alur lokal).

Semua navigasi lewat page headed (operator bisa menonton + menyelesaikan captcha);
request XHR (browse-modal, submit report, get-data-report) lewat fetch in-page
agar fingerprint Camoufox + cookie sesi terpakai (context.request Node-side
terblokir Cloudflare — terbukti job #13).

Alur: login Yii (_csrf-esb-fnb-backend + LoginForm[...]) -> cookie _jwt-token ->
halaman report (meta csrf-token, POST_USER_SESSION, companyIDHead; fallback URL
recapitulation) -> cabang /branch/browse-modal (checkbox data-result) -> POST
report (SalesReport[...] / SalesRecapitulationDetailReport[...], DD-MM-YYYY) ->
JSON {status:200, data:url} -> POST /report_service/main/get-data-report
(multipart url+page; halaman 1 page="", halaman N page=N-1) -> tabel
kv-grid-table + pagination ul.pagination a[data-page] (0-based).
"""
import html as html_mod
import json
import logging
import re
import time
from html.parser import HTMLParser
from typing import Iterator, List, Tuple
from urllib.parse import quote, urlsplit

from app import crypto
from app.errors import CrawlError
from .base import Crawler, CrawlContext, date_chunks, stable_trx_id

log = logging.getLogger("sla.crawler.esbv2")

ORIGIN = "https://erp.esb.co.id"
LOGIN_URL = ORIGIN + "/site/login"
REPORT_URLS = (ORIGIN + "/report/report-sales",
               ORIGIN + "/report/report-sales-recapitulation-detail")
GET_DATA_URL = ORIGIN + "/report_service/main/get-data-report"
FIRST_PAGE_RETRIES = 20  # referensi 12x1s; jendela 40s untuk report lambat siap


class _TableParser(HTMLParser):
    """thead th -> headers; tbody tr td -> baris (stdlib, pengganti BeautifulSoup)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headers: List[str] = []
        self.rows: List[List[str]] = []
        self._in_thead = False
        self._in_tbody = False
        self._in_cell = False
        self._cell: List[str] = []
        self._row: List[str] = []
        self._table_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table_depth += 1
        elif self._table_depth != 1:
            return
        elif tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_tbody = True
        elif tag == "tr":
            self._row = []
        elif tag in ("th", "td"):
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._table_depth = max(0, self._table_depth - 1)
        elif self._table_depth != 1:
            return
        elif tag == "thead":
            self._in_thead = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag in ("th", "td"):
            text = "".join(self._cell).strip()
            if self._in_thead and tag == "th":
                self.headers.append(text)
            elif self._in_tbody:
                self._row.append(text)
            self._in_cell = False
        elif tag == "tr":
            if self._in_tbody and self._row:
                self.rows.append(self._row)
            self._row = []

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def _parse_table(html_text: str) -> List[dict]:
    p = _TableParser()
    p.feed(html_text)
    out = []
    for cells in p.rows:
        if not cells:
            continue
        row = {}
        for i, v in enumerate(cells):
            key = p.headers[i] if i < len(p.headers) and p.headers[i] else f"Column_{i + 1}"
            row[key] = v
        # skip baris summary ala referensi (Sum/Total dengan <=2 sel terisi)
        non_empty = [v for v in row.values() if v.strip()]
        if len(non_empty) <= 2 and any(("sum" in v.lower() or "total" in v.lower()) for v in non_empty):
            continue
        # baris summary grid: label Total/Sum di sel mana pun, atau tanpa identitas transaksi
        if any(v.strip().lower() in ("total", "sum", "grand total", "jumlah") for v in row.values()):
            continue
        date_vals = [v for k, v in row.items() if "date" in k.lower() or "tanggal" in k.lower()]
        id_vals = [v for k, v in row.items() if any(h in k.lower() for h in ("bill", "nota", "number", "trx"))]
        if date_vals and id_vals and not any(v.strip() for v in date_vals) and not any(v.strip() for v in id_vals):
            continue
        out.append(row)
    return out


def _total_pages(html_text: str) -> int:
    m = re.search(r'<ul[^>]*class="[^"]*pagination[^"]*"[^>]*>(.*?)</ul>', html_text, re.S)
    if not m:
        return 1
    pages = [int(x) + 1 for x in re.findall(r'data-page="(\d+)"', m.group(1))]
    return max(pages) if pages else 1


def _page_links(html_text: str) -> dict:
    m = re.search(r'<ul[^>]*class="[^"]*pagination[^"]*"[^>]*>(.*?)</ul>', html_text, re.S)
    if not m:
        return {}
    out = {}
    for href, num in re.findall(r'<a[^>]*href="([^"]+)"[^>]*data-page="(\d+)"', m.group(1)):
        out[int(num) + 1] = html_mod.unescape(href)
    for num, href in re.findall(r'<a[^>]*data-page="(\d+)"[^>]*href="([^"]+)"', m.group(1)):
        out[int(num) + 1] = html_mod.unescape(href)
    return out


def _attr(html_text: str, name: str) -> str:
    m = re.search(r'name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', html_text)
    if not m:
        m = re.search(r'value="([^"]*)"[^>]*name="' + re.escape(name) + r'"', html_text)
    return html_mod.unescape(m.group(1)) if m else ""


def _parse_branches(html_text: str) -> List[Tuple[str, str]]:
    out = []
    for tag in re.findall(r"<input[^>]*ModalDialogCheckbox[^>]*>", html_text):
        m = re.search(r"data-result=(['\"])(.*?)\1", tag, re.S)
        if not m:
            continue
        try:
            d = json.loads(html_mod.unescape(m.group(2)))
            if d.get("branchID"):
                out.append((str(d["branchID"]), str(d.get("branchName") or d["branchID"])))
        except json.JSONDecodeError:
            continue
    return out


def _unwrap_json(text: str):
    data = json.loads(text)
    if isinstance(data, str):  # double encoding ala referensi
        data = json.loads(data)
    return data


def _ddmmyyyy(d: str) -> str:
    y, m, dd = d.split("-")
    return f"{dd}-{m}-{y}"


def _num(v: str) -> str:
    s = re.sub(r"[^\d,.\-]", "", str(v or ""))
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):  # id-ID: 1.250.000,00
            s = s.replace(".", "").replace(",", ".")
        else:  # en-US: 1,250,000.00
            s = s.replace(",", "")
    elif "," in s:
        tail = s.rsplit(",", 1)[1]
        s = s.replace(",", ".") if len(tail) <= 2 else s.replace(",", "")
    return s


def _parse_esb_dt(v) -> str:
    """'15/11/2026' (DD/MM/YYYY, opsional jam) -> ISO untuk sorting/preview."""
    s = str(v or "").strip()
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})(.*)", s)
    if m:
        d, mo, y, rest = m.groups()
        return f"{y}-{mo}-{d}{rest.rstrip()}"
    return s


def _norm_money(row: dict, keys) -> str:
    for k in keys:
        if k in row and str(row[k]).strip():
            return _num(row[k])
    return ""


class EsbV2Crawler(Crawler):
    pos_code = "esbpos"
    name = "ESB POS"
    base_url = ORIGIN
    max_range_days = 31
    has_branch_api = True
    gate_kind = "Cloudflare bila muncul (pause user) + CSRF Yii"

    def __init__(self):
        self.report_kind = 0
        self.csrf = ""
        self.post_user_session = ""
        self.company_id = ""

    # ---------- fetch in-page (fingerprint Camoufox + cookie sesi) ----------
    def _fetch(self, ctx: CrawlContext, url: str, method: str = "GET",
               headers: dict = None, body: str = None, multipart: dict = None) -> dict:
        return ctx.page.evaluate(
            """async (a) => {
                const opts = {method: a.method, headers: a.headers || {}, credentials: 'include'};
                if (a.multipart) {
                    const fd = new FormData();
                    for (const [k, v] of Object.entries(a.multipart)) fd.append(k, v);
                    opts.body = fd;
                } else if (a.body != null) {
                    opts.body = a.body;
                }
                const r = await fetch(a.url, opts);
                return {status: r.status, text: await r.text()};
            }""",
            {"url": url, "method": method, "headers": headers or {},
             "body": body, "multipart": multipart},
        )

    def _headers(self, referer: str) -> dict:
        return {"Accept": "*/*", "Referer": referer,
                "X-CSRF-Token": self.csrf, "X-Requested-With": "XMLHttpRequest"}

    def _cookies(self, ctx: CrawlContext) -> dict:
        return {c["name"]: c["value"] for c in ctx.session._ctx.cookies()}

    def _force_login_button(self, page):
        """Tombol afirmatif popup paksa-login (SweetAlert / Bootstrap modal / DOM biasa)."""
        try:
            loc = page.locator(".swal2-confirm").first
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            pass
        try:
            loc = page.get_by_role(
                "button", name=re.compile(r"^(ya|iya|yes|ok|oke|paksa|force|lanjut|continue|confirm)$", re.I)
            ).first
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            pass
        for sel in (".modal.show button.btn-primary", ".modal.in button.btn-primary",
                    "#confirmModal button.btn-primary"):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    # ---------- login & halaman report (page headed, terlihat operator) ----------
    def _load_report(self, ctx: CrawlContext) -> None:
        report_html = ""
        for kind, url in enumerate(REPORT_URLS):
            rr = ctx.session.goto(url)
            ctx.after_nav(rr)  # pause user bila Cloudflare challenge
            log.info("ESB report page kind=%s url=%s status=%s", kind, ctx.page.url,
                     rr.status if rr else "?")
            if rr is not None and (rr.status == 401 or "unauthorized" in ctx.page.content().lower()):
                continue
            if rr is not None and rr.status != 200:
                raise CrawlError("network", f"Halaman report ESB HTTP {rr.status}")
            self.report_kind = kind
            report_html = ctx.page.content()
            break
        else:
            raise CrawlError("auth", "401 Unauthorized di kedua URL report ESB")
        m = re.search(r'<meta[^>]*name="csrf-token"[^>]*content="([^"]*)"', report_html)
        self.csrf = html_mod.unescape(m.group(1)) if m else _attr(report_html, "_csrf-esb-fnb-backend")
        if not self.csrf:
            raise CrawlError("ui_changed", "CSRF token report ESB tidak ditemukan (meta csrf-token)")
        self.post_user_session = _attr(report_html, "POST_USER_SESSION")
        if not self.post_user_session:
            raise CrawlError("ui_changed", "Input POST_USER_SESSION tidak ditemukan di halaman report ESB")
        # nilai live select (Yii/JS memilih option setelah load; atribut selected bisa absen)
        self.company_id = ctx.page.evaluate(
            "() => { const s = document.querySelector('#companyIDHead');"
            " return s ? (s.value || (s.selectedOptions[0] || {}).value || '') : ''; }") or ""
        if not self.company_id:
            mc = re.search(r'<select[^>]*id="companyIDHead"[^>]*>(.*?)</select>', report_html, re.S)
            if mc:
                mo = re.search(r'<option[^>]*value="([^"]+)"[^>]*selected', mc.group(1))
                self.company_id = mo.group(1) if mo else ""
        log.info("ESB company_id=%s", self.company_id)

    def login(self, ctx: CrawlContext, username: str, password: str) -> None:
        ctx.page.set_default_timeout(60000)  # report ESB bisa lambat generate
        resp = ctx.session.goto(LOGIN_URL)  # browser headed masuk ke halaman ESB
        ctx.after_nav(resp)
        log.info("ESB login page url=%s", ctx.page.url)
        page_html = ctx.page.content()
        csrf_login = _attr(page_html, "_csrf-esb-fnb-backend")
        cookies = self._cookies(ctx)
        if not csrf_login and ("_jwt-token" in cookies or "_identity" in cookies):
            self._load_report(ctx)  # sesi profil masih valid -> reuse
            return
        if not csrf_login:
            raise CrawlError("ui_changed",
                             "CSRF token login ESB tidak ditemukan (_csrf-esb-fnb-backend) — halaman login tidak tampil")
        ctx.page.fill('input[name="LoginForm[username]"]', username)
        ctx.page.fill('input[name="LoginForm[password]"]', password)
        # Popup "akun sedang login, paksa login?" muncul setelah submit:
        # native confirm() -> auto-accept (Ya); modal DOM -> klik tombol afirmatif.
        # Tanpa ini Playwright auto-dismiss confirm (= Tidak) -> login dibatalkan situs.
        dialog_handler = lambda d: d.accept()
        ctx.page.on("dialog", dialog_handler)
        try:
            ctx.page.click('button[type="submit"]')
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline and "site/login" in ctx.page.url:
                btn = self._force_login_button(ctx.page)
                if btn is not None:
                    log.info("ESB force-login popup: klik %s", btn.inner_text()[:30])
                    btn.click()
                    break
                ctx.page.wait_for_timeout(500)
            try:
                ctx.page.wait_for_url(lambda u: "site/login" not in u, timeout=30000)
            except Exception:
                pass
        finally:
            ctx.page.remove_listener("dialog", dialog_handler)
        if "Oops! Invalid username" in ctx.page.content():
            raise CrawlError("auth", "Login ESB ditolak: username/password salah")
        ctx.after_nav()
        if "_jwt-token" not in self._cookies(ctx):
            ctx.session.goto(ORIGIN + "/site/index")  # trigger JWT ala referensi
        self._load_report(ctx)

    # ---------- cabang ----------
    def list_branches(self, ctx: CrawlContext) -> List[Tuple[str, str]]:
        primary = "multipleLinkUserBranchID" if self.report_kind == 0 else "multipleBranchID"
        alternate = "multipleBranchID" if primary == "multipleLinkUserBranchID" else "multipleLinkUserBranchID"
        pairs = self._fetch_branches(ctx, primary)
        if not pairs:
            pairs = self._fetch_branches(ctx, alternate)  # fallback tipe URL 2 ala referensi
        self._branch_map = {k: n for k, n in pairs}
        self._branch_by_name = {n.lower(): k for k, n in pairs}
        if not pairs:
            raise CrawlError("ui_changed", f"Daftar cabang ESB kosong (browse-modal; company={self.company_id})")
        # auto-fetch = per-cabang (tanpa agregat) agar total job/Excel tidak dobel;
        # agregat tetap bisa dipilih: kandidat "Semua Branch" dari riwayat atau key manual "all"
        return pairs

    def _fetch_branches(self, ctx: CrawlContext, btype: str) -> List[Tuple[str, str]]:
        referer = REPORT_URLS[self.report_kind]
        company = self.company_id if btype == "multipleLinkUserBranchID" else ""
        url = (f"{ORIGIN}/branch/browse-modal?type={btype}"
               f"&companyID={company}&selectedBranches=")
        out: List[Tuple[str, str]] = []
        seen = set()
        current = url
        page = 1
        for _ in range(50):  # paginasi browse-modal
            r = self._fetch(ctx, current, headers=self._headers(referer))
            if r["status"] != 200:
                raise CrawlError("network", f"Browse-modal cabang ESB HTTP {r['status']}")
            text = r["text"]
            log.info("ESB browse-modal type=%s status=%s len=%s head=%s", btype, r["status"],
                     len(text), crypto.redact(text[:200]).replace("\n", " "))
            if "site/login" in text.lower() or "sign in" in text.lower():
                raise CrawlError("network", "Sesi ESB putus saat fetch cabang (halaman login)")
            for key, name in _parse_branches(text):
                if key not in seen:
                    seen.add(key)
                    out.append((key, name))
            links = _page_links(text)
            if page + 1 in links:
                href = links[page + 1]
                current = href if href.startswith("http") else ORIGIN + href
                page += 1
            else:
                break
        if not out:
            from app.paths import data_dir
            d = data_dir() / "debug"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"esb_browse_modal_{btype}.html").write_text(crypto.redact(text), encoding="utf-8")
        log.info("ESB browse-modal branches=%s company=%s pages=%s", [n for _, n in out], self.company_id, page)
        return out

    def _resolve_branch(self, ctx: CrawlContext, branch_key: str) -> Tuple[str, str]:
        """key boleh ID atau nama (input manual / kandidat job lama) -> (ID, nama)."""
        if branch_key == "all":
            return "", "All Branch"
        if not getattr(self, "_branch_map", None):
            self.list_branches(ctx)  # mengisi _branch_map/_branch_by_name + fallback tipe URL
        if branch_key in self._branch_map:
            return branch_key, self._branch_map[branch_key]
        bid = self._branch_by_name.get(branch_key.lower())
        if bid:
            return bid, self._branch_map[bid]
        avail = ", ".join(list(self._branch_map.values())[:12])
        raise CrawlError("ui_changed",
                         f"Cabang ESB tidak ditemukan: {branch_key}. Tersedia: {avail}")

    # ---------- report + data ----------
    def _payload(self, branch_id: str, branch_text: str, a: str, b: str) -> dict:
        dfrom, dto = _ddmmyyyy(a), _ddmmyyyy(b)
        if self.report_kind == 0:
            p = {
                "_csrf-esb-fnb-backend": self.csrf,
                "SalesReport[language]": "en",
                "SalesReport[reportDate]": f"{dfrom} - {dto}",
                "SalesReport[dateFrom]": dfrom,
                "SalesReport[dateTo]": dto,
                "SalesReport[salesNum]": "", "SalesReport[billNum]": "",
                "SalesReport[externalMembershipTypeID]": "",
                "SalesReport[companyID]": "", "SalesReport[companyID][]": self.company_id,
                "SalesReport[areaID]": "", "SalesReport[cityID]": "", "SalesReport[brandID]": "",
                "SalesReport[selectedBranchText]": branch_text,
                "SalesReport[branchID]": "",
                "SalesReport[memberCode]": "", "SalesReport[memberExternalCode]": "",
                "SalesReport[customerName]": "", "SalesReport[tableID]": "",
                "SalesReport[paymentMethodTypeID]": "", "SalesReport[paymentMethodID]": "",
                "SalesReport[visitPurposeGroupID]": "", "SalesReport[visitPurposeID]": "",
                "SalesReport[salesTransType]": "",
                "SalesReport[salesReportType]": "Bill Report",
                "SalesReport[salesType]": "", "SalesReport[salesType][]": "Sales",
                "SalesReport[visitorType]": "", "SalesReport[isPreviewBill]": "1",
                "POST_USER_SESSION": self.post_user_session,
            }
            if branch_id:
                p["SalesReport[branchID][]"] = branch_id
        else:
            p = {
                "_csrf-esb-fnb-backend": self.csrf,
                "SalesRecapitulationDetailReport[language]": "",
                "SalesRecapitulationDetailReport[reportDate]": f"{dfrom} - {dto}",
                "SalesRecapitulationDetailReport[dateFrom]": dfrom,
                "SalesRecapitulationDetailReport[dateTo]": dto,
                "SalesRecapitulationDetailReport[salesNum]": "",
                "SalesRecapitulationDetailReport[billNum]": "",
                "SalesRecapitulationDetailReport[tableID]": "",
                "SalesRecapitulationDetailReport[tableSectionID]": "",
                "SalesRecapitulationDetailReport[cityID]": "",
                "SalesRecapitulationDetailReport[selectedBranchText]": branch_text,
                "SalesRecapitulationDetailReport[branchID]": "",
                "SalesRecapitulationDetailReport[areaID]": "",
                "SalesRecapitulationDetailReport[brandID]": "",
                "SalesRecapitulationDetailReport[menuName]": "",
                "SalesRecapitulationDetailReport[memberCode]": "",
                "SalesRecapitulationDetailReport[customerName]": "",
                "SalesRecapitulationDetailReport[visitPurposeGroupID]": "",
                "SalesRecapitulationDetailReport[visitPurposeID]": "",
                "SalesRecapitulationDetailReport[salesType]": "",
                "SalesRecapitulationDetailReport[salesType][]": "Sales",
                "SalesRecapitulationDetailReport[menuCategory]": "",
                "SalesRecapitulationDetailReport[menuCategoryDetail]": "",
                "SalesRecapitulationDetailReport[menuCode]": "",
                "SalesRecapitulationDetailReport[customMenuName]": "",
                "POST_USER_SESSION": self.post_user_session,
            }
            if branch_id:
                p["SalesRecapitulationDetailReport[branchID][]"] = branch_id
        return p

    def scrape_branch(self, ctx: CrawlContext, branch_key: str,
                      date_from: str, date_to: str) -> Iterator[List[dict]]:
        from urllib.parse import urlencode
        referer = REPORT_URLS[self.report_kind]
        bid, btext = self._resolve_branch(ctx, branch_key)
        for a, b in date_chunks(date_from, date_to, self.max_range_days):
            r = self._fetch(ctx, referer + f"?_t={int(time.time() * 1000)}", method="POST",
                            headers={**self._headers(referer),
                                     "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                                     "Origin": ORIGIN},
                            body=urlencode(self._payload(bid, btext, a, b)))
            if r["status"] in (401, 403):
                raise CrawlError("network", f"Submit report ESB ditolak ({r['status']}) — sesi perlu login ulang")
            if r["status"] != 200:
                raise CrawlError("network", f"Submit report ESB HTTP {r['status']}")
            try:
                jd = json.loads(r["text"])
            except Exception as exc:
                raise CrawlError("ui_changed", f"Respons submit report ESB bukan JSON: {exc}") from exc
            report_url = jd.get("data") if jd.get("status") == 200 else None
            if not report_url or not str(report_url).startswith("http"):
                raise CrawlError("ui_changed", f"URL report ESB tidak valid: {str(report_url)[:120]}")
            log.info("ESB submit report ok, path=%s", urlsplit(str(report_url)).path)
            encoded = quote(str(report_url), safe=":/?&=%")
            page_param = ""
            total = 1
            for page_num in range(1, 1000):
                resp = None
                for attempt in range(FIRST_PAGE_RETRIES if page_num == 1 else 3):
                    resp = self._fetch(ctx, GET_DATA_URL, method="POST",
                                       headers={**self._headers(referer), "Origin": ORIGIN},
                                       multipart={"url": encoded, "page": page_param})
                    if resp["status"] in (401, 403):
                        raise CrawlError("network", f"get-data-report ESB ditolak ({resp['status']})")
                    if resp["status"] == 404 and page_num == 1:
                        time.sleep(2)  # report belum siap sesaat setelah submit (referensi)
                        continue
                    if resp["status"] == 200 and page_num == 1:
                        try:
                            peek = _unwrap_json(resp["text"])
                        except Exception:
                            peek = None
                        if isinstance(peek, dict) and peek.get("code") == 404:
                            time.sleep(2)  # export belum siap (code 404 dalam HTTP 200)
                            continue
                    break
                if resp is None or resp["status"] != 200:
                    body = (resp["text"][:200] if resp else "").replace("\n", " ")
                    raise CrawlError("network", f"get-data-report ESB HTTP {resp['status'] if resp else '?'}: {body}")
                try:
                    data = _unwrap_json(resp["text"])
                except Exception as exc:
                    raise CrawlError("ui_changed", f"Respons get-data-report ESB bukan JSON: {exc}") from exc
                if not isinstance(data, dict) or data.get("code") != 200:
                    if page_num == 1:
                        raise CrawlError("network", f"get-data-report ESB code {data.get('code') if isinstance(data, dict) else '?'}")
                    break
                html_table = data.get("data") or ""
                if page_num == 1:
                    total = _total_pages(html_table)
                rows = _parse_table(html_table)
                if rows:
                    for row in rows:
                        row.setdefault("trx_id", str(row.get("id") or row.get("Bill Number") or stable_trx_id(row)))
                        row.setdefault("trx_date", _parse_esb_dt(row.get("Sales Date") or row.get("Sales In Date") or ""))
                        if "grand_total" not in row:
                            g = _norm_money(row, ("Grand Total", "grand_total", "Net Sales", "Subtotal"))
                            if not g:
                                g = next((_num(row[k]) for k in row
                                          if "grand" in k.lower() or "amount" in k.lower()), "")
                            if g:
                                row["grand_total"] = g
                        if "total_tax" not in row:
                            t = _norm_money(row, ("Tax Total", "VAT Total"))
                            if t:
                                row["total_tax"] = t
                    yield rows
                if page_num >= total:
                    break
                page_param = str(page_num)  # halaman 2 -> "1", dst. (offset -1 ala referensi)


def _self_check() -> None:
    table = """<table class="kv-grid-table"><thead><tr><th>No</th><th>Date</th><th>Bill</th><th>Grand Total</th></tr></thead>
    <tbody><tr><td>1</td><td>09-11-2026</td><td>B-001</td><td>1,250,000.00</td></tr>
    <tr><td>Sum</td><td></td><td></td><td>1,250,000.00</td></tr></tbody></table>
    <ul class="pagination"><li class="active"><a data-page="0" href="/x?page=0">1</a></li>
    <li><a data-page="1" href="/x?page=1">2</a></li></ul>"""
    rows = _parse_table(table)
    assert len(rows) == 1 and rows[0]["Bill"] == "B-001", rows
    assert _total_pages(table) == 2
    assert _page_links(table)[2] == "/x?page=1"
    branch_html = ('<input type="checkbox" class="ModalDialogCheckbox kv-row-checkbox" '
                   'data-result="{&quot;branchID&quot;:&quot;7&quot;,&quot;branchName&quot;:&quot;Cabang A&quot;}">')
    assert _parse_branches(branch_html) == [("7", "Cabang A")]
    single_quoted = ("<input type='checkbox' class='ModalDialogCheckbox kv-row-checkbox' "
                     "data-result='{\"branchID\":\"81-fnb_ctr\",\"branchName\":\"THSN Citylink - Bandung\"}'>")
    assert _parse_branches(single_quoted) == [("81-fnb_ctr", "THSN Citylink - Bandung")]
    login_html = '<input type="hidden" name="_csrf-esb-fnb-backend" value="tok123">'
    assert _attr(login_html, "_csrf-esb-fnb-backend") == "tok123"
    assert _ddmmyyyy("2026-11-09") == "09-11-2026"
    assert _num("1,250,000.00") == "1250000.00"
    assert _num("1.250.000,00") == "1250000.00"
    print("esbv2 crawler self_check ok")


if __name__ == "__main__":
    _self_check()