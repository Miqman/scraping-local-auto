# scraping-local-auto

Aplikasi web **localhost** untuk scraping transaksi POS di komputer operator — fallback job yang gagal di server
(IP diblok / captcha tak tertembus). Semi-otomatis: browser headed terbuka, manusia menyelesaikan captcha/OTP sekali,
aplikasi lanjut sendiri sampai Excel siap kirim.

Sumber kebenaran produk: [`prd.md`](prd.md) · checklist hidup: [`tasks.md`](tasks.md) · design system:
**Adminator Enterprise UI v3.1.0** (token terkunci di `tailwind/input.css`).

## Menjalankan (mesin developer, M0)

```bash
# sekali setup (Node hanya untuk build aset UI — tidak dibutuhkan operator):
npm install
npm run assets        # salin font woff2 + htmx + lucide ke static/
npm run build:css     # Tailwind v4 -> static/app.css

# runtime Python:
python -m pip install -r requirements.txt
python -m playwright install chromium     # fallback bila Camoufox belum di-fetch
python -m camoufox fetch                  # opsional: browser anti-detect utama (PRD §8)

python -m app           # -> http://127.0.0.1:8765 terbuka otomatis di browser
```

Acceptance run M0 (PRD §13 + edge cases E1–E18, app dikelola subprocess sendiri):

```bash
python scripts/acceptance.py    # port 8765 harus bebas; laporan PASS/FAIL per hook
```

## Direktori data

Default `./data` (override: env `SLA_DATA_DIR`), permission user-only:

| Isi | Lokasi |
|---|---|
| SQLite WAL (4 tabel PRD §9) | `data/app.db` |
| Profil browser per akun (S6) | `data/profiles/account_<id>/` |
| Hasil export Excel | `data/exports/` |
| Fallback keyfile (E16, hanya bila keychain absen) | `data/master.key` (0600) |

Kunci master AES-256-GCM hidup di **OS keychain** (`keyring`: Windows Credential Manager / macOS Keychain /
libsecret). Bila keychain tidak tersedia aplikasi tetap jalan dengan keyfile + banner peringatan di Dashboard.

## Demo POS (bukti end-to-end tanpa akun merchant nyata)

Situs POS lokal tertanam di `http://127.0.0.1:8765/demo-pos` — login `operator` / `demo1234`.
Dipakai untuk membuktikan W1–W5 di mesin dev (T09/T14); crawler produksi tetap SiberPOS dkk.
Saat pertama jalan aplikasi men-seed akun **Merchant Demo** + 3 riwayat job (done/partial/failed).

Skenario yang dimodelkan per cabang:

| Cabang | Perilaku | Bukti |
|---|---|---|
| Cabang Pusat | normal, paginasi | W1/W4 |
| Cabang Mall | HTTP 500 sekali → auto re-login → sukses | E3 |
| Cabang Bandara | UI rusak sekali (`#trx-table` hilang) → `[ui_changed]`, retry manual sukses | E15, W3 |
| Cabang OTP | gate captcha kedua di tengah scrape; biarkan 8 detik (env dev) → `[user_timeout]`, cabang lain lanjut | E1 |
| Cabang Sepi | 0 transaksi → `done` + "kosong ≠ gagal", Excel header saja | E8 |

Gate login muncul sekali per profil browser; selesaikan checkbox di browser scraper, atau tombol
**"Sudah selesai"** di Detail Job (fallback). Auto-resume terdeteksi ≤5 detik (polling 1s).

Env knob dev: `SLA_PORT` (default 8765), `SLA_GATE_TIMEOUT_SECONDS` (default 600 = 10 menit sesuai E1;
acceptance memakai 8), `SLA_DATA_DIR`.

## SiberPOS (target produksi)

Alur di-port dari projek referensi `scraping-efd-python/utils/` (**proxy diabaikan**, fokus alur lokal yang terbukti):
`siberposv2_crawler.py` + `siberpos_crawler.py` (API transaksi) + `siberpos_login.py` (sesi/login) + `turnstile_login.py` (gate).

1. Profil Camoufox per akun → buka `https://my.siberpos.com/` → **reuse sesi** bila cookie `XSRF-TOKEN` masih ada.
2. Gate Cloudflare/Turnstile: auto-click checkbox sekali + grace 15s; bila interaktif → `waiting_user`
   (operator menyelesaikan sekali di browser headed; auto-resume ≤5s; fallback "Sudah selesai"; timeout 10 menit).
3. Login React-Aria (`input[name=username]` + password) dengan interleave klik submit + klik Turnstile
   (stale-abort 12s seperti referensi); sukses = cookie `XSRF-TOKEN` terbit dan bukan halaman login.
4. Cabang = `all` ("Semua Outlet", agregat seperti referensi `outlet=all`).
5. Transaksi: `GET /api/summary/transactions?status=paid&page=N&outlet=all&brand=all&dates[start]=…&dates[end]=…`
   header `x-xsrf-token` + `x-requested-with: XMLHttpRequest`; paginasi `meta.current_page < meta.last_page`;
   rentang dipecah per 31 hari (E9); baris API disimpan utuh di `raw_json` (+ kunci preview `trx_id`/`trx_date`/`grand_total`).

Kredensial merchant disimpan terenkripsi via halaman Akun (file plaintext dari tim jangan di-commit — sudah di-.gitignore).
Dump DOM tersanitasi untuk diagnosa selector: env `SLA_DEBUG_DUMP=1` → `data/debug/siberpos_*.html` (tanpa nilai input/isi script).

## Registry POS (T23)

| POS | Status | Batas rentang (Q6) | API cabang | Gate |
|---|---|---|---|---|
| `siberpos` | M0 — teruji situs nyata (650 trx → Excel) | API terima rentang penuh; chunk 31 hari (E9) | tidak ada — `outlet=all` agregat ("Semua Outlet") | Cloudflare Turnstile (pause user sekali) |
| `esbpos` | M1 — teruji situs nyata (job #16: 2.184 trx; job #17 satu cabang: 269 trx → Excel 270 baris); port `esb_posv2.py` → `crawlers/esbv2.py` (page-headed + fetch in-page + popup paksa-login). Auto-fetch = per-cabang (tanpa agregat, total tidak dobel); agregat = kandidat "Semua Branch" atau key manual `all` | report terima rentang penuh DD-MM-YYYY; chunk 31 hari | ada — `/branch/browse-modal` (checkbox `data-result`, kutip tunggal); nama manual di-resolve ke ID | Cloudflare bila muncul (pause user) + CSRF Yii + popup paksa-login (auto-Yes) |
| `demo` | harness uji lokal (bukan POS produksi) | 31 hari per request (400 bila lebih) | ada — 5 cabang statis | checkbox simulasi Turnstile/OTP |
| `majoo` | M1 — teruji situs nyata (job #18: 378 trx → Excel 379 baris; #19 reuse token 3 detik); port `MaphyMerchantApps/MajooV2POSSvc.java` → `crawlers/majoo.py`. Camoufox hanya untuk login (Turnstile latar belakang, tanpa 2captcha); token wajib disimpan terenkripsi di tabel `sessions` (padanan DB cartenz) + reuse via checkToken; data via API `services.majoo.id` Bearer | rentang penuh start/end_date jam 00:00/23:59; chunk 31 hari | ada — `support_by_cabang` (header Token); per-cabang `outlet_id` | Turnstile latar belakang (managed auto-pass; interaktif → pause user) |
| `loyverse` | M1 belum — referensi Go/Java di `MaphyMerchantApps` | ditemukan saat build | ditemukan saat build | reCAPTCHA |
| `kasirstaff` | **BLOCKED Q3** (URL + akun contoh dari tim) | belum diketahui | belum diketahui | belum diketahui |

## Menambah crawler baru (pola registry)

1. Tulis `crawlers/<pos>.py` subclass `crawlers.base.Crawler`:
   - `login(ctx, username, password)` — navigasi + isi form; panggil `ctx.after_nav()` setelah tiap navigasi
     (deteksi browser mati, HTTP ≥500, gate captcha). Kredensial ditolak → `CrawlError("auth", ...)` (tanpa auto-retry, S7).
   - `list_branches(ctx)` → `[(key, name)]`; gagal → `CrawlError("ui_changed", ...)` (user fallback input manual, E14).
   - `scrape_branch(ctx, key, from, to)` → generator batch `list[dict]`; pakai `date_chunks(...)` sesuai
     `max_range_days` (E9); `trx_id` stabil atau `stable_trx_id(row)` (E11); simpan baris utuh — `raw_json`
     sumber kebenaran, uang sebagai string exact.
2. Daftarkan `PosMeta(...)` di `crawlers/__init__.py` (`available=True` bila sudah teruji).
3. Selector yang hilang harus disebut di pesan `ui_changed` — itu laporan ke developer pusat (E15).

Runner mengurus sisanya: antrian per akun (E6), maks 2 paralel (E7), attempt ≤3 + re-login ≤2 (E3/S7),
pause/resume captcha (W2), commit per cabang + dedup UNIQUE (W3/E10), recovery restart (E5).

## Keamanan (PRD §11)

- Bind **hanya 127.0.0.1**; middleware menolak `Host`/`Origin` non-loopback (anti DNS-rebinding).
- **CSRF double-submit** di semua POST/PUT/DELETE (header `X-CSRF-Token` atau field `csrf_token`).
- Password **write-only** di UI; AES-256-GCM di DB; `redact()` menutup password/Cookie/Authorization/token
  di log, pesan error, dan tombol "Salin detail error".
- Nol request eksternal saat runtime: font/CSS/htmx/lucide semua lokal (`static/`).
- Hapus akun → profil browser ikut terhapus (S6); hapus akun ber-job ditolak RESTRICT (E17).
- Retensi job 90 hari saat startup (E18). SQLite tidak dienkripsi at-rest — risiko residual S4,
  SQLCipher dievaluasi saat M2; aktifkan disk encryption OS.

## Troubleshooting

| Gejala | Penyebab / solusi |
|---|---|
| Banner kuning "OS keychain tidak tersedia" | E16 normal di Linux minimal; keyfile 0600 dipakai. Pasang libsecret/gnome-keyring bila ingin keychain. |
| Job `failed [interrupted]` setelah restart | E5 by design — klik **Retry** per cabang; data cabang selesai tetap utuh. |
| Job lama `antri` | E6 akun sama sedang dipakai, atau E7 sudah 2 job paralel. Tunggu atau pakai akun lain. |
| Cabang `[auth]` | Password merchant berubah — edit akun (password write-only), lalu retry manual. Tidak ada auto-retry agar akun tidak lockout (S7). |
| Cabang `[ui_changed]` | Website POS berubah — **Salin detail error** (redacted) dan kirim ke developer pusat. |
| Browser tidak terbuka | Jalankan `python -m playwright install chromium` (atau `python -m camoufox fetch`). MVP selalu headed. |
| Port 8765 dipakai | Set `SLA_PORT` lain, atau matikan proses lama. |

## Status milestone

- **M0** (repo ini): kerangka W1–W5 + crawler SiberPOS port alur referensi (login Turnstile + API transaksi)
  + Demo POS end-to-end + acceptance run 37/37. ESB = stretch (T15, belum).
- **M1**: ESB, Majoo, Loyverse, KasirStaffPOS (Q3), persist sesi, scheduler (Q5).
- **M2**: installer 3 OS, auto-update bertanda tangan, FTP (Q1/Q2), SQLCipher go/no-go.