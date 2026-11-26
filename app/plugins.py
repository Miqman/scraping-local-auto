"""Import POS manual: file crawler .py dari developer disimpan di data/plugins/
dan dimuat runtime (importlib) — tanpa update aplikasi. Kontrak file plugin:
- import absolut (from crawlers.base import ...), hanya stdlib + dependensi app;
- PLUGIN_API = <cocok crawlers.base.PLUGIN_API>, opsional PLUGIN_VERSION = "x.y";
- tepat satu subclass Crawler dengan pos_code unik (huruf kecil/angka/-/_).
pos_code bentrok builtin ditolak; sesama plugin = overwrite (update file).
File rusak dicatat sebagai error, tidak menggagalkan boot.
"""
import ast
import importlib.util
import logging
import re
import sys
from pathlib import Path

import crawlers
from crawlers.base import PLUGIN_API, Crawler

from .paths import plugins_dir

log = logging.getLogger("sla.plugins")

MAX_PLUGIN_BYTES = 512 * 1024
_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

# code -> {"code","name","filename","version","api","error"}; error "" = terdaftar aktif
PLUGINS = {}


def _static_meta(source: str) -> dict:
    """Parse tanpa eksekusi: PLUGIN_API/PLUGIN_VERSION + pos_code kelas + import relatif."""
    tree = ast.parse(source)
    meta = {"api": None, "version": "", "codes": set()}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            raise ValueError("Plugin wajib import absolut (from crawlers.base import ...), bukan relatif (from .base)")
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    if t.id == "PLUGIN_API":
                        meta["api"] = node.value.value
                    elif t.id == "PLUGIN_VERSION":
                        meta["version"] = str(node.value.value)
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    for t in stmt.targets:
                        if isinstance(t, ast.Name) and t.id == "pos_code" and stmt.value.value:
                            meta["codes"].add(str(stmt.value.value))
    return meta


def _validate(source: str) -> dict:
    meta = _static_meta(source)
    if meta["api"] != PLUGIN_API:
        raise ValueError(f"PLUGIN_API {meta['api']!r} tidak cocok (app ini API {PLUGIN_API}) — "
                         "minta file versi terbaru ke developer, atau update aplikasi")
    if len(meta["codes"]) != 1:
        raise ValueError("Plugin harus punya tepat satu subclass Crawler dengan pos_code")
    code = next(iter(meta["codes"]))
    if not _CODE_RE.match(code):
        raise ValueError(f"pos_code '{code}' tidak valid (awali huruf kecil; huruf/angka/-/_; 2-32 karakter)")
    if code in crawlers.BY_CODE and code not in PLUGINS:
        raise ValueError(f"pos_code '{code}' bentrok dengan POS bawaan aplikasi")
    meta["code"] = code
    return meta


def _unregister(code: str, drop_module: bool = True) -> None:
    crawlers.REGISTRY[:] = [p for p in crawlers.REGISTRY if p.code != code]
    crawlers.BY_CODE.pop(code, None)
    if drop_module:
        sys.modules.pop(f"sla_plugin_{code}", None)


def _register(path: Path, meta: dict) -> dict:
    code = meta["code"]
    spec = importlib.util.spec_from_file_location(f"sla_plugin_{code}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        cls = next((v for v in vars(mod).values()
                    if isinstance(v, type) and issubclass(v, Crawler) and v is not Crawler
                    and getattr(v, "pos_code", "") == code), None)
        if cls is None:
            raise ValueError(f"Tidak ada subclass Crawler dengan pos_code '{code}' setelah dimuat")
        cls()  # instantiate sekali: konstruktor rusak ketahuan di sini, bukan saat job
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    _unregister(code, drop_module=False)  # update: ganti entri lama; modul baru tetap di sys.modules
    crawlers.REGISTRY.append(crawlers.PosMeta(
        code, getattr(cls, "name", "") or code, getattr(cls, "base_url", "") or "",
        getattr(cls, "max_range_days", 31), getattr(cls, "gate_kind", "") or "tidak diketahui",
        True, cls, milestone="import"))
    crawlers.BY_CODE[code] = crawlers.REGISTRY[-1]
    return {"code": code, "name": getattr(cls, "name", "") or code, "filename": path.name,
            "version": meta["version"], "api": meta["api"], "error": ""}


def import_bytes(data: bytes) -> dict:
    """Validasi + simpan + daftarkan satu file plugin; ValueError bila ditolak."""
    if not data or len(data) > MAX_PLUGIN_BYTES:
        raise ValueError(f"File kosong atau > {MAX_PLUGIN_BYTES // 1024} KB")
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("File bukan teks UTF-8 (.py)")
    try:
        meta = _validate(source)
    except SyntaxError as exc:
        raise ValueError(f"Sintaks Python tidak valid: {exc}")
    path = plugins_dir() / f"{meta['code']}.py"  # nama kanonik dari pos_code, bukan nama upload
    path.write_text(source, encoding="utf-8")
    try:
        entry = _register(path, meta)
    except Exception as exc:
        path.unlink(missing_ok=True)  # jangan tinggalkan file yang gagal dimuat
        raise ValueError(f"Plugin gagal dimuat: {exc}")
    PLUGINS[entry["code"]] = entry
    log.info("plugin POS %s diimport (%s)", entry["code"], entry["filename"])
    return entry


def delete(code: str) -> bool:
    entry = PLUGINS.pop(code, None)
    if entry is None:
        return False
    (plugins_dir() / entry["filename"]).unlink(missing_ok=True)
    _unregister(code)
    log.info("plugin POS %s dihapus", code)
    return True


def load_all() -> None:
    """Scan data/plugins saat startup; file rusak -> entri error, boot tetap jalan."""
    for path in sorted(plugins_dir().glob("*.py")):
        key = path.stem
        try:
            source = path.read_text(encoding="utf-8")
            meta = _validate(source)
            PLUGINS[meta["code"]] = _register(path, meta)
        except Exception as exc:
            PLUGINS[key] = {"code": key, "name": key, "filename": path.name,
                            "version": "", "api": None, "error": str(exc)}
            log.warning("plugin %s gagal dimuat: %s", path.name, exc)


def list_plugins() -> list:
    return [PLUGINS[k] for k in sorted(PLUGINS)]


def _self_check() -> None:
    import os
    import tempfile
    os.environ["SLA_DATA_DIR"] = tempfile.mkdtemp(prefix="sla-plugins-check-")
    import crawlers as cr
    good = (
        "from crawlers.base import Crawler\n"
        "PLUGIN_API = 1\nPLUGIN_VERSION = '1.0'\n"
        "class TokoX(Crawler):\n"
        "    pos_code = 'tokox'\n    name = 'Toko X'\n    base_url = 'https://x.example'\n"
        "    def login(self, ctx, u, p): pass\n"
        "    def list_branches(self, ctx): return [('all', 'Semua')]\n"
        "    def scrape_branch(self, ctx, k, a, b): yield []\n"
    ).encode()
    entry = import_bytes(good)
    assert entry["code"] == "tokox" and entry["error"] == "" and entry["version"] == "1.0"
    assert cr.get_crawler("tokox").name == "Toko X" and cr.BY_CODE["tokox"].available
    # update: pos_code sama, nama baru -> entri lama diganti, bukan dobel
    entry2 = import_bytes(good.replace(b"Toko X", b"Toko X v2"))
    assert entry2["name"] == "Toko X v2" and cr.get_crawler("tokox").name == "Toko X v2"
    assert sum(p.code == "tokox" for p in cr.REGISTRY) == 1
    # reload startup: file persisten dimuat lagi tanpa import ulang
    cr.REGISTRY[:] = [p for p in cr.REGISTRY if p.code != "tokox"]
    cr.BY_CODE.pop("tokox")
    PLUGINS.clear()
    load_all()
    assert cr.get_crawler("tokox").name == "Toko X v2" and PLUGINS["tokox"]["error"] == ""
    # file rusak saat startup -> entri error, tidak crash
    (plugins_dir() / "rusak.py").write_text("PLUGIN_API = 1\npos_code = 'x'\n", encoding="utf-8")
    load_all()
    assert PLUGINS["rusak"]["error"]
    # tolak: API salah, relatif, bentrok builtin, sintaks rusak, tanpa kelas
    for bad, frag in [
        (good.replace(b"PLUGIN_API = 1", b"PLUGIN_API = 99"), "PLUGIN_API"),
        (good.replace(b"from crawlers.base import Crawler", b"from .base import Crawler"), "absolut"),
        (good.replace(b"tokox", b"demo"), "bentrok"),
        (b"def (", "Sintaks"),
        (b"PLUGIN_API = 1\n", "subclass"),
        (b"", "kosong"),
    ]:
        try:
            import_bytes(bad)
            raise AssertionError("seharusnya ditolak: " + frag)
        except ValueError as exc:
            assert frag in str(exc), (frag, str(exc))
    assert "tokox" in cr.BY_CODE  # penolakan tidak merusak plugin aktif
    # hapus: registry + file hilang; akun lama tetap ada (guard available=False di create_job)
    assert delete("tokox") and not delete("tokox")
    assert "tokox" not in cr.BY_CODE and not (plugins_dir() / "tokox.py").exists()
    print("plugins self_check ok")


if __name__ == "__main__":
    _self_check()
