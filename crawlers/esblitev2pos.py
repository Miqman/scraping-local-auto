"""Crawler ESB POS Lite V2 — port alur terbukti dari
scraping-efd-python/utils/esb_lite_v2_crawler.py. Login + cabang sama seperti
esblitepos (subclass); data dari sales-summary (dashboard), satu request per hari
tanpa pagination. Referer wajib halaman /report/sales-summary/index.
"""
import logging
from datetime import date, timedelta
from typing import Iterator, List, Optional

from app.errors import CrawlError
from .base import CrawlContext, date_chunks, stable_trx_id
from .esblitepos import ORIGIN, EsbLitePosCrawler, _norm

log = logging.getLogger("sla.crawler.esblitev2pos")

SALES_SUMMARY_PATH = "/api/web/v1/report/sales-summary"
SALES_SUMMARY_PAGE = ORIGIN + "/report/sales-summary/index"


def iter_days(a: str, b: str) -> List[str]:
    start = date.fromisoformat(a)
    end = date.fromisoformat(b)
    if end < start:
        raise CrawlError("unknown", "Rentang tanggal terbalik")
    days, d = [], start
    while d <= end:
        days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def extract_summary(body) -> Optional[dict]:
    """summary bisa di root atau di dalam data (referensi _extract_summary_payload)."""
    if isinstance(body, dict):
        if isinstance(body.get("summary"), dict):
            return body["summary"]
        data = body.get("data")
        if isinstance(data, dict) and isinstance(data.get("summary"), dict):
            return data["summary"]
    return None


class EsbLiteV2PosCrawler(EsbLitePosCrawler):
    pos_code = "esblitev2pos"
    name = "ESB POS Lite V2"
    base_url = ORIGIN
    max_range_days = 31
    has_branch_api = True
    gate_kind = "reCAPTCHA (Camoufox; pause user bila interaktif)"

    def scrape_branch(self, ctx: CrawlContext, branch_key: str,
                      date_from: str, date_to: str) -> Iterator[List[dict]]:
        bid, bname, brand = self._resolve_branch(ctx, branch_key)
        for a, b in date_chunks(date_from, date_to, self.max_range_days):
            rows = []
            for day in iter_days(a, b):
                params = {"startDate": day, "endDate": day, "companyID": "",
                          "brandID": brand, "branchID": bid}
                data = self._api_get(ctx, SALES_SUMMARY_PATH, params, referer=SALES_SUMMARY_PAGE)
                summary = extract_summary(data)
                if summary is None:
                    raise CrawlError("ui_changed",
                                     f"Key summary sales-summary ESB Lite tidak ditemukan ({day})")
                row = {"date": day, **summary}
                row.setdefault("trx_id", stable_trx_id(row))
                row.setdefault("trx_date", day)
                if "grand_total" not in row:
                    g = _norm(row.get("nettSales") or row.get("grossSales"))
                    if g:
                        row["grand_total"] = g
                row.setdefault("branch_name", bname)
                rows.append(row)
            log.info("ESB Lite V2 chunk %s..%s branch=%s days=%s", a, b, bname or "all", len(rows))
            if rows:
                yield rows


def _self_check() -> None:
    assert iter_days("2026-10-01", "2026-10-03") == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert iter_days("2026-10-01", "2026-10-01") == ["2026-10-01"]
    assert extract_summary({"summary": {"totalBill": 1}})["totalBill"] == 1
    assert extract_summary({"data": {"summary": {"a": 1}}})["a"] == 1
    assert extract_summary({"data": []}) is None
    assert extract_summary(None) is None
    row = {"date": "2026-10-01", **extract_summary({"summary": {"totalBill": 2, "nettSales": 5000}})}
    assert row["date"] == "2026-10-01" and row["totalBill"] == 2
    print("esblitev2pos crawler self_check ok")


if __name__ == "__main__":
    _self_check()
