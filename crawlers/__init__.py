"""Registry POS (PRD §9: hidup di kode crawler). M0: siberpos + demo.
POS pain lain terdaftar sebagai jaga-jaga (PRD §2) — crawler menyusul di M1.
"""
from dataclasses import dataclass
from typing import Optional, Type

from .base import Crawler
from .demo import DemoCrawler
from .siberpos import SiberPosCrawler
from .esbv2 import EsbV2Crawler
from .majoo import MajooCrawler


@dataclass(frozen=True)
class PosMeta:
    code: str
    name: str
    base_url: str
    max_range_days: int
    gate_kind: str
    available: bool
    crawler: Optional[Type[Crawler]] = None
    milestone: str = "M0"


REGISTRY = [
    PosMeta("siberpos", "SiberPOS", "https://my.siberpos.com", 31,
            "Cloudflare Turnstile", True, SiberPosCrawler),
    PosMeta("demo", "Demo POS (uji lokal)", "http://127.0.0.1:8765/demo-pos", 31,
            "Checkbox (simulasi captcha)", True, DemoCrawler),
    PosMeta("esbpos", "ESB POS", "https://erp.esb.co.id", 31,
            "Cloudflare bila muncul (pause user) + CSRF Yii", True, EsbV2Crawler, milestone="M1"),
    PosMeta("majoo", "Majoo", "https://dashboard.majoo.id", 31,
            "Turnstile latar belakang (Camoufox; pause user bila interaktif)", True, MajooCrawler, milestone="M1"),
    PosMeta("loyverse", "Loyverse", "https://loyverse.com", 31, "reCAPTCHA", False, milestone="M1"),
    PosMeta("kasirstaff", "KasirStaffPOS", "(URL menunggu Q3)", 31, "belum diketahui", False, milestone="M1"),
]

BY_CODE = {p.code: p for p in REGISTRY}


def get_crawler(pos_code: str) -> Crawler:
    meta = BY_CODE.get(pos_code)
    if meta is None or not meta.available or meta.crawler is None:
        raise KeyError(f"Crawler {pos_code} belum tersedia")
    return meta.crawler()


def available_pos() -> list:
    return [p for p in REGISTRY if p.available]