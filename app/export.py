"""T12: Export Excel streaming (E13) dari raw_json — presisi uang exact (W4).
xlsxwriter constant_memory + baca DB per batch; nama file E12; idempotent.
"""
import json
import re
from datetime import datetime
from pathlib import Path

import xlsxwriter

from . import db
from .paths import exports_dir

STANDARD_COLS = ["trx_id", "trx_date", "branch_name"]
MONEY_HINTS = ("total", "price", "tax", "amount", "grand", "subtotal", "diskon", "discount")
BATCH = 500


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", part).strip("_") or "x"


def export_filename(job) -> str:
    return (f"{_safe(job['pos_code'])}_{_safe(job['account_label'])}_"
            f"{job['date_from']}_{job['date_to']}_{job['id']}.xlsx")


def _money_cols(keys: list) -> set:
    return {k for k in keys if any(h in k.lower() for h in MONEY_HINTS)}


def export_job(job_id: int) -> Path:
    job = db.get_job(job_id)
    if job is None:
        raise FileNotFoundError(job_id)
    path = exports_dir() / export_filename(job)

    # Pass 1: union key raw_json (streaming, tanpa menahan baris di RAM)
    extra_keys = []
    seen = set(STANDARD_COLS)
    n_rows = 0
    for r in db.iter_transaction_batches(job_id, BATCH):
        n_rows += 1
        raw = json.loads(r["raw_json"])
        for k in raw:
            if k not in seen:
                seen.add(k)
                extra_keys.append(k)
    extra_keys.sort()
    columns = STANDARD_COLS + extra_keys
    money = _money_cols(columns)

    wb = xlsxwriter.Workbook(str(path), {"constant_memory": True, "strings_to_numbers": False})
    # E12 idempotent: timestamp dokumen tetap dari job, bukan now()
    created = datetime.strptime(job["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    wb.set_properties({"created": created, "title": f"Job {job_id} {job['pos_code']}"})
    ws = wb.add_worksheet("Transaksi")
    header_fmt = wb.add_format({"bold": True, "bg_color": "#F1F5F9", "border": 1,
                                "border_color": "#E2E8F0", "font_name": "Inter"})
    money_fmt = wb.add_format({"num_format": "#,##0.00", "font_name": "Inter"})
    text_fmt = wb.add_format({"font_name": "Inter"})
    ws.set_row(0, 22)
    for ci, col in enumerate(columns):
        ws.write(0, ci, col, header_fmt)
        ws.set_column(ci, ci, 24 if col in money else 18)
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, n_rows, len(columns) - 1)

    # Pass 2: tulis streaming per batch dari DB
    ri = 1
    for r in db.iter_transaction_batches(job_id, BATCH):
        raw = json.loads(r["raw_json"])
        merged = {"trx_id": r["trx_id"], "trx_date": r["trx_date"],
                  "branch_name": r["branch_name"]}
        merged.update(raw)
        for ci, col in enumerate(columns):
            v = merged.get(col, "")
            if col in money and v not in ("", None):
                try:
                    ws.write_number(ri, ci, float(v), money_fmt)
                    continue
                except (TypeError, ValueError):
                    pass
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            ws.write(ri, ci, "" if v is None else str(v), text_fmt)
        ri += 1
    wb.close()
    return path