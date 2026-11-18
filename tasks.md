# Tasks — scraping-local-auto

Sumber kebenaran: `prd.md` v1 (§ referensi di tiap task). Design system: **Adminator Enterprise UI v3.1.0** (PRD §7.1; referensi `gemini-code-1794991265987.md` + `gemini-code-1794991233547.html`). Checkbox = checklist hidup; centang hanya jika acceptance hook terobservasi, bukan sekadar "kode jadi".
Ukuran: **S** ≤1 jam · **M** 1–2 jam · **L** 2–4 jam (dengan bantuan AI).

Urutan emas: **T09 (spike pause/resume captcha) dibuktikan SEBELUM crawler penuh selesai** — itu risiko terbesar MVP (PRD §14). Jangan sentuh "bagus kalau ada" sebelum W1–W5 lulus acceptance.

---

## M0 — Prototipe dev (2 hari, mesin developer)

### Hari 1 — Kerangka + bukti W2 + SiberPOS end-to-end

- [x] **T01 · Scaffold + aset design system** (M) — struktur `app/` (main, db, security, crypto, runner, export), `crawlers/` (registry + siberpos), `templates/`, `static/`; deps Python: fastapi, uvicorn, jinja2, cryptography, keyring, camoufox, xlsxwriter; **aset UI Adminator (§7.1)**: Inter + JetBrains Mono woff2 → `static/fonts/` (@font-face), Lucide lokal, htmx.min.js; setup Tailwind v4 CLI (Node hanya di mesin dev) dengan `@theme` token Adminator + token error ekstensi → build `static/app.css`; `python -m app` → uvicorn **127.0.0.1:8765** + browser terbuka otomatis.
  *Accept: Dashboard terbuka dari satu perintah; DevTools Network = nol request eksternal (S5).*
- [x] **T02 · Lapisan DB + recovery + retensi** (M) — sqlite3 stdlib mode WAL, koneksi per operasi; schema 4 tabel PRD §9 + UNIQUE(`job_branch_id`,`trx_id`) + FK (RESTRICT akun, CASCADE job) + index `jobs.status`, `job_branches.job_id`; startup recovery E5; purge job >90 hari E18. Dep: T01.
  *Accept: restart saat job running → `failed [interrupted]`; job tua terhapus, akun tidak.*
- [x] **T03 · Security baseline** (M) — bind loopback-only; middleware validasi `Host`/`Origin`; CSRF token (cookie + form/header) di semua POST/PUT/DELETE; direktori data `0700`; aset statis lokal tanpa CDN (S5). Dep: T01.
  *Accept: §13 keamanan — POST tanpa token → 403; Host non-loopback → 403; IP LAN tidak terjangkau.*
- [x] **T04 · Enkripsi kredensial + redaction** (M) — AES-256-GCM; kunci app di OS keychain (`keyring`); fallback keyfile `0600` + banner Dashboard E16; util `redact()` (field password, header Cookie/Authorization, query param token/key) dipakai logger + penyimpanan error + tombol salin (S2). Dep: T01.
  *Accept: inspeksi SQLite = nol plaintext; error login gagal tidak memuat password/token.*
- [x] **T05 · Shell Adminator + Dashboard + Akun** (M) — base Jinja + htmx: sidebar 240px (brand + nav grup: Dashboard, Job Baru, Akun Merchant; mobile off-canvas + backdrop), header sticky h-16 breadcrumb, main max-w-1440 (§7.1); Dashboard = kartu data table job (toolbar search + filter status/POS **server-side via htmx**, badge status, ID mono, tabular-nums, paginasi, empty state) + CTA "+ Job Baru"; Akun = data table + modal tambah/edit (password write-only) + modal konfirmasi hapus; hapus RESTRICT jika ber-job E17 + hapus profil browser S6. Dep: T02–T04.
  *Accept: tambah akun → ciphertext di DB; hapus akun ber-job → ditolak; profil browser ikut hilang; visual sesuai token §7.1 (radius 12/8, kontrol h-9, badge palet).*
- [x] **T06 · Halaman Job Baru** (S) — kartu form kontrol h-9 radius-8: select POS dari registry → select akun terfilter → rentang tanggal (zona waktu akun POS, §6) → daftar cabang checkbox dengan selected-state `#EFF6FF` (auto-fetch setelah login atau input manual E14); CTA "Jalankan" → job `queued`. Dep: T05, T08 (fetch cabang butuh browser).
  *Accept: job muncul di Dashboard berstatus queued; baris cabang terpilih berlatar `#EFF6FF`.*
- [x] **T07 · Job runner** (M) — asyncio task in-process; antrian; maks 2 job paralel E7; lock per akun E6; status DB = sumber kebenaran; commit per cabang; attempt maks 3, auto re-login maks 2, `auth` tanpa retry E3/E4/S7. Dep: T02.
  *Accept: 2 job akun sama → kedua antri; kredensial salah → gagal tanpa retry.*
- [x] **T08 · Camoufox launcher** (S) — selalu headed; direktori profil per akun; Playwright sync di thread executor; deteksi browser mati E2. Dep: T01.
  *Accept: browser terlihat terbuka; user tutup browser → cabang `failed [browser]`, aplikasi hidup.*
- [x] **T09 · SPIKE W2: pause/resume captcha** (L) — deteksi gate Cloudflare/turnstile/captcha (contoh `turnstile_login.py` di scraping-efd-python); cabang `waiting_user` + browser tampil ke depan; polling deteksi lolos → lanjut otomatis ≤5 detik; tombol fallback "Sudah selesai"; timeout 10 menit → `failed [user_timeout]`, job lanjut E1. Buktikan di login SiberPOS. Dep: T07, T08.
  *Accept: §13 W2 penuh — pause, auto-resume tanpa klik, fallback, timeout.*
  *Bukti terobservasi: Demo POS lokal (auto-resume 0,8–1,6s) DAN SiberPOS nyata — job #5 pause `waiting_user` saat Turnstile interaktif, operator menyelesaikan sekali di browser headed, scrape lanjut otomatis; retry memakai reuse sesi cookie XSRF-TOKEN tanpa captcha ulang.*
- [x] **T10 · Crawler SiberPOS** (L) — tulis ulang mencontoh `siberpos_crawler.py`: login → daftar cabang → transaksi per cabang; chunking rentang tanggal E9; `trx_id` fallback sha256 E11; upsert dedup; simpan `raw_json` utuh; kode error `ui_changed`/`network`/`auth`. Dep: T09 (pola pause/resume dipakai crawler).
  *Accept: job SiberPOS nyata dari UI → transaksi masuk DB per cabang.*
  *Bukti terobservasi: job SiberPOS nyata #5 (akun tim, 2026-11-09→10) dari UI → 650 transaksi masuk DB cabang "Semua Outlet" via API /api/summary/transactions (paginasi meta); Export Excel 651 baris, nilai uang cocok raw_json. Alur port dari utils/siberposv2_crawler.py + siberpos_crawler.py (proxy diabaikan).*

### Hari 2 — Hasil, export, hardening, acceptance

- [x] **T11 · Detail Job + retry** (M) — polling htmx 2 detik; banner warning MENUNGGU USER (`#FFFBEB`/`#D97706`) + tombol fallback "Sudah selesai"; tabel cabang: badge status, trx_count tabular-nums, kode error mono, attempts, aksi **Retry per cabang + "Retry semua gagal"** W3; preview transaksi paginasi server-side; tombol "Salin detail error" (redacted) + toast; Export Excel = tombol sekunder ikon download. Dep: T10.
  *Accept: §13 W3 — job 2 cabang 1 gagal: data sukses utuh, retry tidak mendobel baris.*
- [x] **T12 · Export Excel** (M) — xlsxwriter `constant_memory`, baca DB per batch E13; render dari `raw_json` (presisi uang exact); nama file `{pos}_{label}_{from}_{to}_{jobid}.xlsx`; kosong = header saja E8; tombol aktif jika ≥1 cabang done E12; export ulang idempotent. Dep: T11.
  *Accept: §13 W4 — jumlah baris = DB, nilai uang cocok raw_json, file kosong ber-header.*
- [x] **T13 · Edge-case pass** (M) — uji checklist E1–E18 terhadap implementasi; tambal yang bolong (terutama E5 crash, E6 antrian, E8 kosong≠gagal, E10 dedup paginasi). Dep: T11, T12.
  *Accept: tiap baris E punya perilaku terobservasi atau catatan alasan.*
- [x] **T14 · Acceptance run M0** (M) — jalankan seluruh §13 (W1–W5 + keamanan) pada job SiberPOS nyata; perbaiki yang gagal. Dep: T13.
  *Accept: semua lulus = **M0 selesai**.*
  *Bukti terobservasi: `python scripts/acceptance.py` → 37/37 PASS (W1–W5, S1/S2/S5, E1–E18) + uji browser sungguhan (dashboard, akun, job, captcha, retry, export, mobile) + job SiberPOS nyata 650 transaksi → Excel. Syarat keluar M0 terpenuhi di mesin ini.*
- [x] **T15 · STRETCH: crawler ESB** (L) — contoh `esb_posv2.py`/`esb_lite`; pola sama T10. **Dipotong pertama jika waktu meleset — bukan acceptance.** Dep: T14.
  *Accept: job ESB nyata → Excel.*
  *Bukti terobservasi: job ESB nyata #16 (akun CTRTAXBDG, 2026-11-15→16) → 2.184 transaksi, 5 cabang (all + 4 cabang asli) done; Excel 2.185 baris, uang cocok raw_json. Popup paksa-login tertangani (log "force-login popup: klik Yes"); companyID dari nilai live select; data-result single-quote terparse; baris summary grid dibuang; nama cabang manual di-resolve ke ID browse-modal.*
- [x] **T16 · README dev** (S) — cara run, lokasi direktori data, troubleshooting, cara tambah crawler baru (pola registry). Dep: T14.
  *Accept: clone segar → jalan mengikuti README tanpa bertanya.*

## M1 — POS pain lengkap (setelah M0)

- [x] **T17 · ESB penuh** (L) — jika T15 belum; dokumentasikan batas rentang tanggal (Q6). Dep: M0.
  *Teruji nyata bersama T15. Q6 ESB: report menerima rentang penuh DD-MM-YYYY (chunk 31 hari sebagai pengaman E9); paginasi get-data-report offset-1 (halaman 1 page="", halaman N page=N-1) + retry 404/code-404 sampai 40s.*
- [x] **T18 · Crawler Majoo** (L) — contoh `majoo_scraping()` di report_routes scraping-efd-python; POS captcha-heavy = pembuktian W2 di produksi. Dep: M0.
  *Bukti terobservasi (referensi aktual: MaphyMerchantApps/MajooV2POSSvc.java, bukan scraping-efd-python): job #18 nyata 378 trx (cabang BELIKOPI. 650881, 2026-05-01→11) → Excel 379 baris; Turnstile latar belakang lolos via Camoufox tanpa 2captcha; token ditangkap dari respons login lalu WAJIB disimpan ke tabel `sessions` terenkripsi (padanan DB cartenz); job #19 reuse token tanpa login browser (3 detik vs 34 detik). Proxy & limit captcha harian 2captcha tidak diport (tidak relevan lokal).*
- [ ] **T19 · Crawler Loyverse** (L) — contoh `scraper-service/app/loyverse` (Go) + `LoyversePOSSvc` (Java); semi-otomatis menggantikan 2captcha yang saldo-dependen. Dep: M0.
- [ ] **T20 · Crawler KasirStaffPOS** (L) — **BLOCKED Q3** (URL website + akun contoh dari tim; nol referensi di kedua codebase, sudah diverifikasi grep). Crawler baru dari nol. Dep: Q3 terjawab.
- [x] **T21 · Persist sesi/cookie** (M, bagus kalau ada) — tabel sessions + reuse antar-run; invalidasi saat `auth` gagal. Dep: M0.
  *Dicakup profil Camoufox persisten per akun (cookie antar-run; terbukti retry SiberPOS tanpa captcha ulang) PLUS tabel `sessions` (token terenkripsi per akun) untuk Majoo — padanan langkah wajib saveToDB cartenz di Java; reuse terbukti job #19 tanpa login browser; invalidasi via checkToken (401/expired tex-01) memicu login ulang.*
- [ ] **T22 · Scheduler** (M, bagus kalau ada) — **BLOCKED Q5** (jam/zona/hari); job harian otomatis + override manual; story 6. Dep: Q5 terjawab.
- [ ] **T23 · Registry POS terdokumentasi** (S) — per POS: batas tanggal (Q6), ada/tidak API cabang, jenis gate captcha. Dep: T17–T20.

## M2 — Rollout tim (20 operator)

- [ ] **T24 · Wrapper desktop + installer 3 OS** (L) — putuskan Q4 (kandidat kuat: Electron membungkus UI localhost yang ada — diskusi terakhir); bundle sidecar Python + Camoufox; sekali klik → web localhost terbuka; story 7. Dep: M1, Q4.
  *Accept: user semi-teknis install tanpa bantuan developer di Win/Mac/Linux.*
- [ ] **T25 · Auto-update + notifikasi** (M) — HTTPS + verifikasi tanda tangan/checksum S8; reminder sebelum terap; story 8. Dep: T24.
- [ ] **T26 · Upload FTP otomatis** (M) — **BLOCKED Q1+Q2** (kolom Excel final disetujui tim server + host/folder/kredensial FTP); tombol kirim + retry + status. Dep: Q1, Q2.
- [ ] **T27 · Panduan install operator** (S) — sekali klik, rekomendasi disk encryption OS (BitLocker/FileVault/LUKS) S4, troubleshooting umum. Dep: T24.
- [ ] **T28 · Evaluasi SQLCipher** (S) — go/no-go enkripsi DB at-rest (risiko residual S4); keputusan dicatat di PRD §12. Dep: T24.
- [ ] **T29 · Audit konsistensi design system + responsive** (M) — sisir semua halaman terhadap token §7.1 (spacing, radius konsentris, badge, tabular-nums, overline); uji mobile sidebar + backdrop; poles hover/focus ring/transisi. **Hanya setelah acceptance lulus.** Dep: T14.

---

## Blocking terbuka (PRD §12)

| Q | Pertanyaan | Task tertahan | Kapan dibutuhkan |
|---|---|---|---|
| Q1 | Kolom Excel final | T26 | Sebelum M2 |
| Q2 | Target FTP | T26 | Sebelum M2 |
| Q3 | KasirStaffPOS URL + akun | T20 | Sebelum M1 item itu |
| Q4 | Teknologi wrapper | T24 | Awal M2 |
| Q5 | Spesifikasi jadwal | T22 | Sebelum scheduler |
| Q6 | Batas tanggal per POS | T17–T20 | Ditemukan saat build |

**Tidak ada Q yang memblokir M0.** Mulai dari T01.

## Aturan main

1. Risiko dulu: T09 sebelum crawler penuh — kalau pause/resume rapuh, ketahuan di hari 1 bukan hari 2.
2. Satu task = satu acceptance hook terobservasi; centang hanya setelah bukti.
3. Waktu meleset → potong T15/stretch, **jangan pernah potong acceptance §13 atau keamanan S1–S7**.
4. Fitur "bagus kalau ada" (T21, T22, T29) haram disentuh sebelum T14 lulus.
5. Perubahan cakupan → catat di PRD §12 dulu, baru task berubah.
6. UI wajib token Adminator (§7.1 PRD) — dilarang impor CDN/font eksternal; Tailwind build hanya di mesin developer, output CSS dibundel lokal.
