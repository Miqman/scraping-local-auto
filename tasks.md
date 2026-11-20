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
- [x] **T19 · Crawler Loyverse** (L) — contoh `scraper-service/app/loyverse` (Go) + `LoyversePOSSvc` (Java); semi-otomatis menggantikan 2captcha yang saldo-dependen. Dep: M0.
  *Bukti terobservasi (referensi aktual: LoyversePOSSvc.java getAllTrxRAW + Go loyverse_service.go): login Camoufox headed akun contoh tim (Oshe Coffee) → cabinetlogin 200 loginResult=ok, reCAPTCHA invisible lolos TANPA 2captcha & tanpa pause user (challenge interaktif tetap terdeteksi gate_active → pause W2); cookie auth ownercub-lls/JSESSIONID (path /data, HttpOnly) disimpan terenkripsi ke sessions (padanan saveToDB cartenz) + reuse portabel terbukti (clear_cookies 2× → reuse ~1 detik tanpa login browser, guard getreceiptsarchive 200); guard cookies palsu → invalidasi → login ulang; kredensial salah → [auth] "Wrong email or password" (HTTP 200 + loginResult=user_password_not_found) tanpa retry (E4/S7); job #1–#2 & #9 (akun OSHC) → done trx_count=0 (E8 kosong≠gagal) → Excel header saja; retry dedup count tetap. **Data nyata: job #11 (akun tim "warung family", dijalankan owner via UI, 2026-10-21→11-19) → 147 trx SALE masuk DB → Excel 147 baris + header (80 kolom), mismatch trx_id/trx_date/grand_total vs raw_json = 0** (contoh: totalAmount 10000000 cents → 100000.00; date UTC 12:23:45 → 19:23:45 +7 jam; trx_id 1-22729). Akun OSHC sendiri nol receipt (probe Jun–Nov 2026, merchantsIds/outletsIds eksplisit, body Java string-date & Go millis = 0; total.saleReceipts=0 eksplisit di Nov) → jalur kosong E8 teruji di akun itu. Q6: chunk 31 hari (E9); rentang di luar jendela arsip akun → total/pages kosong (result ok) diperlakukan kosong. 2captcha/proxy/limit captcha harian tidak diport (tidak relevan lokal). Lingkungan: Python 3.9.0 butuh cryptography 41.x (marker python_version di requirements; PyCMethod_New belum ada di python3.dll 3.9.0).*
- [ ] **T20 · Crawler KasirStaffPOS** (L) — **SKIP sementara (keputusan owner 2026-11-19, PRD §12)**; sebelumnya BLOCKED Q3 (URL website + akun contoh dari tim; nol referensi di kedua codebase, sudah diverifikasi grep). Crawler baru dari nol. Dep: Q3 terjawab + owner minta dilanjutkan.
- [x] **T21 · Persist sesi/cookie** (M, bagus kalau ada) — tabel sessions + reuse antar-run; invalidasi saat `auth` gagal. Dep: M0.
  *Dicakup profil Camoufox persisten per akun (cookie antar-run; terbukti retry SiberPOS tanpa captcha ulang) PLUS tabel `sessions` (token terenkripsi per akun) untuk Majoo — padanan langkah wajib saveToDB cartenz di Java; reuse terbukti job #19 tanpa login browser; invalidasi via checkToken (401/expired tex-01) memicu login ulang. Loyverse (T19): cookies ownercub-lls/JSESSIONID path /data disimpan terenkripsi; reuse portabel terbukti (context bersih → guard 200 → ~1 detik tanpa login browser); cookies palsu/401 → invalidasi → login ulang.*
- [x] **T22 · Scheduler** (M, bagus kalau ada) — Q5 dijawab default developer (PRD §12, 2026-11-19): harian 06:00 waktu lokal mesin, rentang kemarin (D-1), per akun configurable + nonaktif; job harian otomatis + override manual (Job Baru); story 6.
  *Bukti terobservasi: harness HTTP+DB → 15/15 PASS — kartu "Jadwal otomatis" di /accounts (default 06:00, opt-in nonaktif); POST schedule tersimpan (enabled/run_at); job terjadwal dibuat ≤1 tick (30s) rentang D-1 → `done` via reuse cookies Loyverse → tampil di Dashboard → export 200; tick kedua tidak mendobel (at-most-once per hari, last_run_date ditandai sebelum job); nonaktif → tidak jalan; catch-up hari sama (last_run digeser kemarin) → job kedua `done`; POST tanpa CSRF → 403; reset default nonaktif 06:00. Self-check due-logic: `python -m app.scheduler` ok. Implementasi: app/scheduler.py (tick asyncio 30s) + tabel schedules (CASCADE hapus akun) + route /accounts/{id}/schedule + kartu _schedules.html; runner/E6/E7 tidak berubah. CSS Tailwind di-rebuild (kelas baru), aset ?v=6.*
- [x] **T23 · Registry POS terdokumentasi** (S) — per POS: batas tanggal (Q6), ada/tidak API cabang, jenis gate captcha. Dep: T17–T20 (T20 = SKIP owner, PRD §12).
  *Terpenuhi: README §Registry POS memuat baris per POS (Q6 dari bukti build T15/T17/T18/T19, ada/tidak API cabang, jenis gate) dan cocok dengan kode — diverifikasi grep: PosMeta.max_range_days/gate_kind + Crawler.has_branch_api (siberpos/loyverse agregat False; esbv2/majoo/demo True). KasirStaffPOS tetap terdaftar "belum diketahui" + catatan SKIP.*

## M2 — Rollout tim (20 operator)

- [x] **T24 · Wrapper desktop + installer** (L) — **Q4 = Electron (owner 2026-11-19, PRD §12); Win-first**: bungkus UI localhost yang ada; bundle sidecar Python (PyInstaller) + Camoufox; sekali klik → web localhost terbuka; story 7. Dep: M1 ✓, Q4 ✓.
  *Bukti terobservasi (Win): `desktop/dist/Scraping Lokal Otomatis Setup 0.1.0.exe` (302 MB, NSIS oneClick per-user) → install silent `/S` exit 0 tanpa UAC/bantuan developer → app terbuka: Electron window memuat UI dari sidecar PyInstaller onedir (259 MB, `scripts/build_sidecar.py`) + Camoufox bundle 412 MB (`vendor/camoufox`, fonts 2.1 GB di-trim — launch teruji tanpa fonts). Di app TERPASANG: akun ditambah via modal (terenkripsi), job demo pause `waiting_user` + banner MENUNGGU USER + fallback "Sudah selesai" terobservasi; job Loyverse #5 & #6 login situs nyata via Camoufox bundle (reCAPTCHA invisible auto-pass, reuse cookies) → done → Export Excel tertulis di `%APPDATA%/scraping-local-auto-desktop/data/exports`. Data terisolasi per-user (userData), bukan repo. Hardening: precheck port dipakai → dialog pesan jelas; single-instance lock; sidecar mati → dialog + E5 interrupted saat buka ulang; SLA_NO_BROWSER (Electron yang tampil). Acceptance M0 ulang pasca-semua perubahan: **37/37 PASS**. Catatan build: unsigned (`signAndEditExecutable:false`); winCodeSign cache di-pre-extract 7za tanpa `-snld` (Windows tanpa privilege symlink); NSIS `runAfterFinish:false`. Mac/Linux = menyusul (mesin uji belum ada, keputusan owner Win-first).*
- [ ] **T25 · Auto-update + notifikasi** (M) — HTTPS + verifikasi tanda tangan/checksum S8; reminder sebelum terap; story 8. Dep: T24.
- [ ] **T26 · Upload FTP otomatis** (M) — **BLOCKED Q1+Q2** (kolom Excel final disetujui tim server + host/folder/kredensial FTP); tombol kirim + retry + status. Dep: Q1, Q2.
- [x] **T27 · Panduan install operator** (S) — sekali klik, rekomendasi disk encryption OS (BitLocker/FileVault/LUKS) S4, troubleshooting umum. Dep: T24 ✓.
  *Terpenuhi: `PANDUAN-OPERATOR.md` — install sekali klik (NSIS per-user tanpa admin, sesuai bukti T24), alur akun/job/captcha (pause W2 + fallback + timeout 10 menit), export + lokasi file app terpasang, jadwal harian, keamanan (disk encryption OS, localhost-only, folder data), tabel troubleshooting 7 gejala + jalur lapor "Salin detail error" redacted. Isi dicocokkan dengan perilaku terobservasi app terpasang (T24).*
- [x] **T28 · Evaluasi SQLCipher** (S) — go/no-go enkripsi DB at-rest (risiko residual S4); keputusan dicatat di PRD §12. Dep: T24 ✓.
  *Keputusan: **GO, adopsi saat rollout M2** (PRD §12 2026-11-19). Bukti empiris: wheel sqlcipher3 0.6.5 cp39-win_amd64 (SQLite 3.54.1) bersih; WAL terenkripsi jalan; tanpa kunci → DatabaseError; header terenkripsi + nol plaintext baris di file; overhead micro-bench 2000 insert tidak signifikan. pysqlcipher3 sdist-only → tidak dipilih. Implementasi + migrasi sqlcipher_export dijadwalkan bersama M2 (DB operator sudah berisi); wheel Mac/Linux dicek saat build M2.*
- [x] **T29 · Audit konsistensi design system + responsive** (M) — sisir semua halaman terhadap token §7.1 (spacing, radius konsentris, badge, tabular-nums, overline); uji mobile sidebar + backdrop; poles hover/focus ring/transisi. **Hanya setelah acceptance lulus.** Dep: T14.
  *Bukti terobservasi (browser headless 1440×900 + 390×844, computed styles + screenshot): card radius 12px, control/btn h-9 (36px) radius 8px, sidebar 240px, header 64px, tabular-nums aktif, overline uppercase + tracking 0.05em, font Inter, kanvas #F8FAFC; badge done #ECFDF5/#059669 & neutral #F1F5F9/#475569 (waiting/failed via utility badge-waiting/badge-failed terkompilasi + terpakai di _job_live, teruji live acceptance W2/E1). 4 halaman disisir visual (Dashboard, Job Baru, Detail #11 preview 147 baris + paginasi, Akun + kartu Jadwal). Mobile: sidebar off-canvas translate -100% (left -240) + backdrop hidden; hamburger membuka (left 0, backdrop block, screenshot dimmed); klik backdrop menutup; tabel akun overflow-x scroll; form jadwal flex-wrap. Focus input: border #2563EB + ring 2px primary/15 terukur; hover CTA: #1D4ED8 terukur; transisi control/btn/sidebar terkompilasi. Tidak ada celah token → nol perubahan kode.*

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
