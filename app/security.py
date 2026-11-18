"""S1: loopback-only, validasi Host/Origin (anti DNS-rebinding), CSRF double-submit.
/demo-pos/* dimodelkan sebagai situs POS pihak ketiga -> exempt CSRF (Host tetap dicek).
"""
import secrets
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

CSRF_COOKIE = "sla_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
CSRF_EXEMPT_PREFIXES = ("/demo-pos/",)


def _host_ok(request: Request) -> bool:
    host = (request.headers.get("host") or "").split(":")[0].strip("[]")
    if host not in LOOPBACK_HOSTS:
        return False
    origin = request.headers.get("origin")
    if origin:
        if urlparse(origin).hostname not in LOOPBACK_HOSTS:
            return False
    return True


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not _host_ok(request):
            return JSONResponse({"detail": "Host/Origin non-loopback ditolak"}, status_code=403)
        token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        request.state.csrf_token = token
        if request.method in ("POST", "PUT", "DELETE", "PATCH") and \
                not request.url.path.startswith(CSRF_EXEMPT_PREFIXES):
            sent = request.headers.get(CSRF_HEADER)
            if not sent:
                form = await request.form()
                sent = form.get(CSRF_FIELD)
            if not sent or not secrets.compare_digest(str(token), str(sent)):
                return JSONResponse({"detail": "CSRF token tidak valid"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"  # revalidasi ETag; aset lokal tetap segar
        if not request.cookies.get(CSRF_COOKIE):
            response.set_cookie(CSRF_COOKIE, token, httponly=False, samesite="strict", path="/")
        return response


def csrf_token(request: Request) -> str:
    return getattr(request.state, "csrf_token", "") or request.cookies.get(CSRF_COOKIE, "")