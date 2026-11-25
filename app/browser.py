"""T08: browser Camoufox per job + profil per akun, Playwright sync di thread executor.
T09: deteksi gate captcha/OTP + pause/resume (poll 1s -> auto-resume <=5s).
Camoufox wajib (anti-detect Cloudflare Turnstile); tanpa fallback — gagal launch = job gagal.
"""
import os
import time

from playwright.sync_api import Error as PWError

from .errors import CrawlError
from .paths import profile_dir

GATE_TIMEOUT_S = int(os.environ.get("SLA_GATE_TIMEOUT_SECONDS", "600"))  # E1: 10 menit
HEADLESS = os.environ.get("SLA_HEADLESS", "") == "1"  # produksi (exe) tanpa jendela; dev headed utk captcha

GATE_URL_MARKERS = (
    "challenges.cloudflare.com", "/cdn-cgi/challenge-platform", "turnstile",
    "captcha", "recaptcha", "hcaptcha", "/gate", "one-time-code", "/otp",
)
GATE_SELECTORS = (
    "iframe[src*='challenges.cloudflare.com']", "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']", ".cf-turnstile", "#turnstile-wrapper",
    ".g-recaptcha", "#challenge-form", "#cf-challenge-running",
    "input[autocomplete='one-time-code']", "input[name*='otp']",
)

_CLOSED_MARKERS = ("closed", "crashed", "target page", "browser has been", "connection closed")


class BrowserDead(Exception):
    """E2: user menutup browser / driver mati."""


def _wrap_pw(exc: Exception) -> Exception:
    msg = str(exc).lower()
    if any(m in msg for m in _CLOSED_MARKERS):
        return BrowserDead("Browser ditutup atau crash di tengah job")
    return exc


def is_gated(page) -> bool:
    # location.href dari dalam page: binding page.url bisa tertinggal saat navigasi (Camoufox)
    try:
        state = page.evaluate(
            "sels => ({href: location.href, hit: sels.some(s => !!document.querySelector(s))})",
            list(GATE_SELECTORS),
        )
        url = str(state.get("href") or page.url).lower()
        hit = bool(state.get("hit"))
    except PWError as exc:
        wrapped = _wrap_pw(exc)
        if isinstance(wrapped, BrowserDead):
            raise wrapped from exc
        return True  # transisi navigasi; poll berikutnya menilai ulang
    if any(m in url for m in GATE_URL_MARKERS):
        return True
    return hit


class Session:
    """Satu browser per job; profil persisten per akun (S6); headless bila SLA_HEADLESS=1."""

    def __init__(self, account_id: int):
        self.account_id = account_id
        self._ctx = None
        self._camoufox_cm = None
        self.page = None

    def __enter__(self):
        prof = str(profile_dir(self.account_id))
        last = None
        for attempt in range(2):
            try:
                self._launch(prof)
                break
            except Exception as exc:
                last = exc
                self._teardown()
                if attempt == 0:
                    time.sleep(3)  # profil mungkin masih dikunci proses browser sebelumnya
        else:
            raise CrawlError("browser", f"Browser tidak bisa dibuka: {last}")
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.page.set_default_timeout(20000)
        return self

    def _launch(self, prof):
        from camoufox.sync_api import Camoufox  # noqa: delay import
        kwargs = dict(headless=HEADLESS, persistent_context=True, user_data_dir=prof,
                      humanize=False, geoip=False, timeout=25000)
        bundle = os.environ.get("SLA_CAMOUFOX_DIR")
        if bundle:
            # T24: Camoufox terbundel installer — tanpa unduh runtime. UBO di-exclude:
            # addons default resolve ke cache user (INSTALL_DIR), bukan ke bundle.
            # Layout per OS: win/lin executable di root bundle; mac di Camoufox.app/Contents/MacOS.
            import json
            import sys
            from camoufox.addons import DefaultAddons
            if sys.platform == "darwin":
                exe = os.path.join(bundle, "Camoufox.app", "Contents", "MacOS", "camoufox")
                ver = os.path.join(bundle, "Camoufox.app", "Contents", "Resources", "version.json")
            elif sys.platform == "win32":
                exe = os.path.join(bundle, "camoufox.exe")
                ver = os.path.join(bundle, "version.json")
            else:
                exe = os.path.join(bundle, "camoufox-bin")
                ver = os.path.join(bundle, "version.json")
            with open(ver, encoding="utf-8") as f:
                ff_version = json.load(f)["version"].split(".", 1)[0]
            kwargs.update(executable_path=exe,
                          ff_version=ff_version, exclude_addons=[DefaultAddons.UBO])
        self._camoufox_cm = Camoufox(**kwargs)
        self._ctx = self._camoufox_cm.__enter__()

    def _teardown(self):
        try:
            if self._camoufox_cm is not None:
                self._camoufox_cm.__exit__(None, None, None)
        except Exception:
            pass
        self._camoufox_cm = None
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:
            pass
        self._ctx = None

    def __exit__(self, *exc):
        self._teardown()
        return False

    @property
    def alive(self) -> bool:
        try:
            if self.page is None or self.page.is_closed():
                return False
            b = getattr(self._ctx, "browser", None)
            return b.is_connected() if b is not None else True
        except Exception:
            return False

    def check_alive(self):
        if not self.alive:
            raise BrowserDead("Browser ditutup atau crash di tengah job")

    def goto(self, url: str, wait: str = "domcontentloaded"):
        self.check_alive()
        try:
            resp = self.page.goto(url, wait_until=wait)
            return resp
        except PWError as exc:
            wrapped = _wrap_pw(exc)
            if isinstance(wrapped, BrowserDead):
                raise wrapped from exc
            raise CrawlError("network", f"Gagal memuat {url}: {exc}") from exc

    def bring_to_front(self):
        if HEADLESS:
            return  # tanpa jendela; pause gate tetap terdeteksi, user retry via UI
        try:
            self.page.bring_to_front()
        except PWError as exc:
            raise _wrap_pw(exc) from exc


def wait_gate_clear(session: Session, on_wait, on_resume, resume_event,
                    timeout_s: int = GATE_TIMEOUT_S, gate_fn=None, assist_fn=None) -> None:
    """T09: pause saat gate; polling deteksi lolos 1s; fallback event 'Sudah selesai';
    timeout -> CrawlError user_timeout (E1). on_wait/on_resume menulis status DB."""
    on_wait()
    session.bring_to_front()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if resume_event is not None and resume_event.is_set():
            resume_event.clear()
            time.sleep(1.0)  # beri halaman waktu redirect setelah user klik fallback
        session.check_alive()
        if assist_fn is not None:
            try:
                assist_fn(session.page)  # auto-click aman ala referensi (checkbox turnstile)
            except Exception:
                pass
        gated = gate_fn(session.page) if gate_fn else is_gated(session.page)
        if not gated:
            on_resume()
            return
        time.sleep(1.0)
    durata = f"{timeout_s // 60} menit" if timeout_s >= 60 else f"{timeout_s} detik"
    raise CrawlError("user_timeout", f"Captcha/OTP tidak diselesaikan dalam {durata}")