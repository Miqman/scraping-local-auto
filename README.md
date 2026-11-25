# scraping-local-auto

Aplikasi web **localhost** untuk scraping transaksi POS di komputer operator — fallback job yang gagal di server
(IP diblok / captcha tak tertembus). Semi-otomatis: dev menampilkan browser headed agar manusia menyelesaikan
captcha/OTP sekali; build exe headless (tanpa jendela). Aplikasi lanjut sendiri sampai Excel siap kirim.

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
python -m camoufox fetch                  # wajib: browser anti-detect (Cloudflare Turnstile); tanpa fallback

python3 -m app          # -> http://127.0.0.1:8765 terbuka otomatis di browser
# Windows: bila `python` menunjuk Python 2.7 (C:\Python27 menang PATH), pakai `python3`
# (shim pyenv 3.9) atau geser folder shims pyenv ke atas C:\Python27 di PATH.
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
acceptance memakai 8), `SLA_DATA_DIR`, `SLA_HEADLESS` (`1` = scraper tanpa jendela browser; dev default
headed agar captcha bisa diselesaikan, build exe/Electron otomatis headless).

## SiberPOS (target produksi)

Alur di-port dari projek referensi `scraping-efd-python/utils/` (**proxy diabaikan**, fokus alur lokal yang terbukti):
`siberposv2_crawler.py` + `siberpos_crawler.py` (API transaksi) + `siberpos_login.py` (sesi/login) + `turnstile_login.py` (gate).

1. Profil Camoufox per akun → buka `https://my.siberpos.com/` → **reuse sesi** bila cookie `XSRF-TOKEN` masih ada.
2. Gate Cloudflare/Turnstile: auto-click checkbox sekali + grace 15s; bila interaktif → `waiting_user`
   (dev: operator menyelesaikan sekali di browser headed; exe: headless, auto-resume ≤5s bila gate lolos
   sendiri, fallback "Sudah selesai"; timeout 10 menit).
3. Login React-Aria (`input[name=username]` + password) dengan interleave klik submit + klik Turnstile
   (stale-abort 12s seperti referensi); sukses = cookie `XSRF-TOKEN` terbit dan bukan halaman login.
4. Cabang = `all` ("Semua Outlet", agregat seperti referensi `outlet=all`).
5. Transaksi: `GET /api/summary/transactions?status=paid&page=N&outlet=all&brand=all&dates[start]=…&dates[end]=…`
   header `x-xsrf-token` + `x-requested-with: XMLHttpRequest`; paginasi `meta.current_page < meta.last_page`;
   rentang dipecah per 31 hari (E9); baris API disimpan utuh di `raw_json` (+ kunci preview `trx_id`/`trx_date`/`grand_total`).

Kredensial merchant disimpan terenkripsi via halaman Akun (file plaintext dari tim jangan di-commit — sudah di-.gitignore).
Dump DOM tersanitasi untuk diagnosa selector: env `SLA_DEBUG_DUMP=1` → `data/debug/siberpos_*.html` (tanpa nilai input/isi script).

## Jadwal otomatis (T22, story 6 — v2 interval menit)

**Interval menit per akun (minimal 1, default 60)**, opt-in. UI: halaman **Jadwal Otomatis** (menu
sidebar Konfigurasi) berupa accordion per POS (`<details>` native) — klik POS → daftar akun +
input interval menit + toggle Aktif + tombol simpan. Scheduler in-process (`app/scheduler.py`,
tick 10 detik) membuat job `queued` yang **menyambung rentang job terakhir akun** (mulai H+1 setelah
`date_to` terakhir sampai hari ini; job pertama akun = kemarin..hari ini) dan **memakai ulang cabang
job terakhir** (`db.last_job_branches`; akun belum punya job → auto-fetch semua) — runner mengambilnya
seperti job manual: batas E6 (akun sama antri) / E7 (maks 2 paralel), retry, export, status live sama.
Override manual = **Job Baru**.

- **Hanya saat aplikasi terbuka** — scheduler hidup di proses app; tutup = tidak jalan, tanpa backfill.
- **At-most-once per interval:** `last_run_at` ditandai sebelum job dibuat; due bila `now >= last_run_at + interval`.
- **Catch-up:** interval sudah lewat saat app dibuka → langsung due sekali (bukan backfill hari terlewat).
- **Skip akun busy** (ada job queued/running/waiting) tanpa menandai — jangan menumpuk antrian pada interval pendek.
- **Tahan bila job terakhir gagal `[auth]`** sampai retry/manual — cegah lockout (1440 login/hari pada interval 1 menit).
- **Purge retensi 90 hari** dijalankan scheduler sekali per ganti hari (interval pendek = banyak job).
- POS yang crawler-nya belum tersedia (mis. `kasirstaff`) dilewati + log peringatan.
- Hapus akun → jadwal ikut terhapus (CASCADE). Zona waktu = lokal mesin.
- **Migrasi:** DB lama (`run_at`/`last_run_date`) → `interval_minutes`/`last_run_at` otomatis saat `init()`;
  jadwal harian lama aktif dipertahankan sebagai interval 1440 menit.
- Self-check logika: `python -m app.scheduler`.

## Desktop installer (T24, Win-first)

Wrapper Electron (`desktop/`) membungkus UI localhost yang ada; sidecar Python dibundle
PyInstaller onedir + browser Camoufox terbundel — operator tidak perlu install Python/browser.

Build (mesin developer, berurutan):

```bash
python -m pip install pyinstaller
python scripts/build_sidecar.py          # -> sidecar/dist/sla-sidecar/ (~259 MB)
# Camoufox: salin cache browser (python -m camoufox path) ke vendor/camoufox;
# folder fonts/ (2.1 GB) opsional runtime — boleh dihapus dari bundle (launch teruji tanpanya)
cd desktop && npm install
npm run dist:win                         # -> dist/Scraping Lokal Otomatis Setup 0.1.0.exe (~302 MB)
```

- Install: dobel-klik setup (NSIS oneClick per-user, tanpa admin) atau silent `setup.exe /S`.
  Data app terpasang: `%APPDATA%/scraping-local-auto-desktop/data` (terpisah dari repo).
- `desktop/main.js`: precheck port dipakai → dialog; single-instance lock; spawn sidecar
  (`SLA_NO_BROWSER=1`, `SLA_HEADLESS=1`, `SLA_CAMOUFOX_DIR=resources/camoufox`); tunggu `/healthz`; buka window;
  sidecar mati → dialog + recovery E5 saat buka ulang.
- Catatan build Windows: installer **unsigned** (`signAndEditExecutable:false`); cache
  winCodeSign electron-builder gagal extract symlink tanpa privilege Windows → pre-extract
  manual `7za x -y` (tanpa `-snld`) ke `%LOCALAPPDATA%/electron-builder/Cache/winCodeSign/<hash>/`;
  NSIS `runAfterFinish:false` agar install silent tidak menjalankan app di port default.
- **Mac**: panduan build lengkap di [`BUILD-MAC.md`](BUILD-MAC.md) (untuk dijalankan di mesin
  Mac — bisa minta tolong rekan yang punya Mac). Kode sudah portabel (resolusi Camoufox/sidecar
  per OS, `build_sidecar.py` separator `--add-data` + keyring backend per OS, `package.json`
  target mac dmg/zip + `npm run dist:mac`). **Build wajib di macOS** — PyInstaller tidak bisa
  cross-compile dari Windows. `camoufox` di-pin `==0.4.11` (versi teruji; 0.5.x menarik model
  fpgen tambahan yang gagal di CI). Unsigned (`identity:null`): Gatekeeper memperingatkan —
  klik-kanan→Open atau `xattr -d com.apple.quarantine <app>`; signing Apple Developer ID menyusul (M2).
  **CI alternatif**: `.github/workflows/build-mac.yml` (runner `macos-latest`) — trigger manual
  atau tag `v*`; upload artifact dmg+zip. Bila CI rewel (rate limit/jaringan), pakai build manual di Mac.
- Linux: menyusul (owner Win-first, PRD §12); jalur resolusi `camoufox-bin` sudah disiapkan.
- Panduan operator: [`PANDUAN-OPERATOR.md`](PANDUAN-OPERATOR.md).

## Registry POS (T23)

| POS | Status | Batas rentang (Q6) | API cabang | Gate |
|---|---|---|---|---|
| `siberpos` | M0 — teruji situs nyata (650 trx → Excel) | API terima rentang penuh; chunk 31 hari (E9) | tidak ada — `outlet=all` agregat ("Semua Outlet") | Cloudflare Turnstile (pause user sekali) |
| `esbpos` | M1 — teruji situs nyata (job #16: 2.184 trx; job #17 satu cabang: 269 trx → Excel 270 baris); port `esb_posv2.py` → `crawlers/esbv2.py` (page-headed + fetch in-page + popup paksa-login). Auto-fetch = per-cabang (tanpa agregat, total tidak dobel); agregat = kandidat "Semua Branch" atau key manual `all` | report terima rentang penuh DD-MM-YYYY; chunk 31 hari | ada — `/branch/browse-modal` (checkbox `data-result`, kutip tunggal); nama manual di-resolve ke ID | Cloudflare bila muncul (pause user) + CSRF Yii + popup paksa-login (auto-Yes) |
| `demo` | harness uji lokal (bukan POS produksi) | 31 hari per request (400 bila lebih) | ada — 5 cabang statis | checkbox simulasi Turnstile/OTP |
| `majoo` | M1 — teruji situs nyata (job #18: 378 trx → Excel 379 baris; #19 reuse token 3 detik); port `MaphyMerchantApps/MajooV2POSSvc.java` → `crawlers/majoo.py`. Camoufox hanya untuk login (Turnstile latar belakang, tanpa 2captcha); token wajib disimpan terenkripsi di tabel `sessions` (padanan DB cartenz) + reuse via checkToken; data via API `services.majoo.id` Bearer | rentang penuh start/end_date jam 00:00/23:59; chunk 31 hari | ada — `support_by_cabang` (header Token); per-cabang `outlet_id` | Turnstile latar belakang (managed auto-pass; interaktif → pause user) |
| `loyverse` | M1 — teruji situs nyata + data nyata: job #11 (akun tim "warung family", via UI, 2026-10-21→11-19) → **147 trx → Excel 148 baris, uang cocok raw_json (mismatch 0)**; login Camoufox: cabinetlogin 200 `loginResult=ok`, reCAPTCHA invisible lolos tanpa 2captcha/pause user; job #1–#2, #9 (akun OSHC kosong) `done` E8 → Excel header; kredensial salah → `[auth]` tanpa retry; reuse cookies `sessions` portabel 1 detik. Port `LoyversePOSSvc.java` (getAllTrxRAW) + Go `loyverse_service.go` → `crawlers/loyverse.py`. Cookie auth `ownercub-lls`/`JSESSIONID` (path `/data`) wajib disimpan terenkripsi (padanan saveToDB cartenz) | customPeriod `YYYY-MM-DD 00:00:00`/`23:59:59`; chunk 31 hari (E9); rentang di luar jendela arsip → `total`/`pages` kosong (`result ok`) = kosong | tidak ada — agregat ("all", "Semua Outlet") `outletsIds=all`; `outletName` per baris (`fillSelectData.outlets` ada di respons) | reCAPTCHA v2 invisible (Camoufox auto-pass terobservasi; challenge interaktif → pause user W2) |
| `kasirstaff` | **SKIP sementara** (keputusan owner 2026-11-19, PRD §12; sebelumnya BLOCKED Q3 — URL + akun contoh dari tim) | belum diketahui | belum diketahui | belum diketahui |

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
pause/resume captcha (W2), commit per cabang + dedup per akun lintas job (W3/E10), recovery restart (E5).

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
| Browser tidak terbuka | Jalankan `python -m camoufox fetch` (Camoufox wajib; tak ada fallback Chromium). |
| Port 8765 dipakai | Set `SLA_PORT` lain, atau matikan proses lama. |
| `ImportError: DLL load failed ... _rust` (Windows, Python 3.9.0) | `python3.dll` 3.9.0 belum ekspor `PyCMethod_New` yang dibutuhkan cryptography ≥42. `requirements.txt` sudah memakai marker `python_version` (3.9 → cryptography 41.x). Bila terlanjur: `pip install cryptography==41.0.7` atau upgrade Python ≥3.10 (PRD §8: 3.12). |

## Status milestone

- **M0** (repo ini): kerangka W1–W5 + crawler SiberPOS port alur referensi (login Turnstile + API transaksi)
  + Demo POS end-to-end + acceptance run 37/37 (ulang 2026-11-19 pasca-T19).
- **M1**: ESB ✓ (T15/T17), Majoo ✓ (T18), Loyverse ✓ (T19), persist sesi ✓ (T21), scheduler ✓ (T22),
  registry terdokumentasi ✓ (T23); KasirStaffPOS = SKIP sementara (owner 2026-11-19, Q3 belum terjawab).
- **M2**: installer 3 OS, auto-update bertanda tangan, FTP (Q1/Q2), SQLCipher go/no-go.