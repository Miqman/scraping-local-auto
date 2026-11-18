"""Crawler Demo POS — membuktikan kerangka W1-W5 terhadap situs POS lokal."""
import os
from typing import Iterator, List, Tuple

from app.errors import CrawlError
from .base import Crawler, CrawlContext, date_chunks

PORT = int(os.environ.get("SLA_PORT", "8765"))
CELL_KEYS = ["trx_id", "trx_date", "customer", "items", "payment_method",
             "total_price", "total_tax", "grand_total"]


class DemoCrawler(Crawler):
    pos_code = "demo"
    name = "Demo POS (uji lokal)"
    base_url = f"http://127.0.0.1:{PORT}/demo-pos"
    max_range_days = 31
    has_branch_api = True
    gate_kind = "checkbox (simulasi turnstile)"

    def login(self, ctx: CrawlContext, username: str, password: str) -> None:
        ctx.after_nav(ctx.session.goto(self.base_url + "/login"))
        page = ctx.page
        try:
            page.fill('input[name="username"]', username)
            page.fill('input[name="password"]', password)
            page.click('button[type="submit"]')
            page.wait_for_load_state("domcontentloaded")
        except Exception as exc:
            raise CrawlError("ui_changed", f"Form login Demo POS tidak ditemukan: {exc}") from exc
        ctx.after_nav()
        if page.query_selector(".login-error"):
            raise CrawlError("auth", "Kredensial ditolak Demo POS (username/password salah)")
        ctx.require_logged_in("/login")

    def list_branches(self, ctx: CrawlContext) -> List[Tuple[str, str]]:
        resp = ctx.session.goto(self.base_url + "/branches")
        ctx.after_nav(resp)
        ctx.require_logged_in("/login")
        try:
            data = ctx.page.evaluate("() => JSON.parse(document.body.innerText)")
            return [(b["key"], b["name"]) for b in data]
        except Exception as exc:
            raise CrawlError("ui_changed", f"API cabang Demo POS berubah: {exc}") from exc

    def scrape_branch(self, ctx: CrawlContext, branch_key: str,
                      date_from: str, date_to: str) -> Iterator[List[dict]]:
        for a, b in date_chunks(date_from, date_to, self.max_range_days):
            page_n = 1
            while True:
                url = (f"{self.base_url}/transactions?branch={branch_key}"
                       f"&date_from={a}&date_to={b}&page={page_n}")
                resp = ctx.session.goto(url)
                ctx.after_nav(resp)
                ctx.require_logged_in("/login")
                if resp is not None and resp.status == 400:
                    raise CrawlError("network", "Rentang tanggal ditolak POS (batas chunk)")
                rows = self._parse_table(ctx)
                if rows:
                    yield rows
                nxt = ctx.page.query_selector('a[rel="next"]')
                if nxt is None:
                    break
                page_n += 1

    def _parse_table(self, ctx: CrawlContext) -> List[dict]:
        table = ctx.page.query_selector("#trx-table")
        if table is None:
            raise CrawlError("ui_changed", "Tabel transaksi #trx-table tidak ditemukan")
        out = []
        for tr in table.query_selector_all("tbody tr"):
            cells = [td.inner_text().strip() for td in tr.query_selector_all("td")]
            if len(cells) != len(CELL_KEYS):
                raise CrawlError("ui_changed",
                                 f"Kolom tabel berubah: harap {len(CELL_KEYS)}, dapat {len(cells)}")
            row = dict(zip(CELL_KEYS, cells))
            row["branch_key"] = ctx.page.query_selector("#trx-table").get_attribute("data-branch") or ""
            row["branch_name"] = ctx.page.query_selector("#branch-name").inner_text().strip()
            row["currency"] = "IDR"
            out.append(row)
        return out