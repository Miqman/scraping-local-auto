# Build Installer Windows — Panduan Lengkap

Panduan ini untuk membangun **`Scraping Lokal Otomatis Setup 0.1.0.exe`** di mesin Windows.
Tidak perlu paham koding — cukup ikuti langkah berurutan, salin-tempel perintah.

> **Kenapa harus di Windows?** Sidecar Python dibekukan dengan PyInstaller, yang tidak bisa
> cross-compile dari Mac/Linux. Binary `.exe` Windows hanya bisa dibuat di Windows. Browser
> Camoufox untuk Windows juga hanya bisa diunduh di Windows.

---

## 0. Yang dibutuhkan

- Windows 10/11 64-bit.
- Koneksi internet (unduh ±2 GB: Electron + Camoufox + dependensi).
- ±5 GB ruang disk kosong.
- **PowerShell** (bawaan Windows: klik Start → ketik "PowerShell" → buka).
- Git (untuk clone repo). Bila belum ada: https://git-scm.com/download/win (install, next-next).

> Semua perintah di bawah dijalankan di **PowerShell**, dari folder repo projek.
> Bila perintah `python` menunjuk Python 2.7 (cek `python --version`), pakai `py -3` atau
> path Python 3 Anda. Panduan ini memakai `python` dengan asumsi sudah Python 3.9+.

---

## 1. Install alat dasar (sekali saja)

### 1a. Python 3.12

Unduh installer dari https://www.python.org/downloads/windows/ (Python 3.12.x, "Windows installer (64-bit)").

Saat install: **centang "Add python.exe to PATH"** di layar pertama, lalu "Install Now".

Cek di PowerShell (buka PowerShell baru setelah install):

```powershell
python --version    # harus 3.12.x (atau 3.9+ ; proyek teruji di 3.9–3.12)
```

> Bila `python` masih menunjuk versi lain, pakai `py -3.12` sebagai ganti `python` di semua langkah.

### 1b. Node.js 22

Unduh installer dari https://nodejs.org/ (versi 22.x LTS, "Windows Installer (.msi)", 64-bit).
Install (next-next, default).

Cek:

```powershell
node --version    # harus v22.x
npm --version
```

### 1c. Git

Bila belum ada: https://git-scm.com/download/win → install default.

```powershell
git --version
```

---

## 2. Ambil kode projek

```powershell
git clone https://github.com/Miqman/scraping-local-auto.git
cd scraping-local-auto
```

Bila sudah pernah clone sebelumnya, cukup perbarui:

```powershell
cd scraping-local-auto
git pull origin main
```

---

## 3. Setup lingkungan Python

Buat virtual environment (ruang Python terisolasi untuk projek ini):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> Bila Activate.ps1 ditolak ("running scripts is disabled"), jalankan sekali:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` lalu ulangi Activate.
> Alternatif tanpa ubah policy: `.\.venv\Scripts\activate.bat` (di cmd, bukan PowerShell).

Setelah aktif, prompt PowerShell biasanya diawali `(.venv)`. **Semua perintah Python berikut
dijalankan dengan `.venv` aktif.** Bila nanti membuka PowerShell baru, aktifkan lagi dengan
`.\.venv\Scripts\Activate.ps1` dari folder projek.

Install dependensi + PyInstaller:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
```

> `requirements.txt` sudah mengunci `camoufox==0.4.11` (versi teruji). Jangan mengubahnya.

---

## 4. Unduh browser Camoufox (bundle Windows)

```powershell
python -m camoufox fetch
```

Ini mengunduh browser Camoufox untuk Windows (±400 MB tanpa fonts). Tunggu sampai muncul
`Camoufox successfully installed.`

Salin browser ke folder `vendor/camoufox` (yang akan dibundel ke installer), buang folder
fonts yang besar (2.1 GB, opsional — sudah teruji launch tanpanya):

```powershell
$cache = python -m camoufox path
New-Item -ItemType Directory -Force -Path vendor | Out-Null
Copy-Item -Recurse -Force $cache vendor\camoufox
Remove-Item -Recurse -Force vendor\camoufox\fonts -ErrorAction SilentlyContinue
```

Verifikasi bundle Windows lengkap (harus mencetak `OK`):

```powershell
if (Test-Path vendor\camoufox\camoufox.exe) { echo OK }
```

> Bila `camoufox fetch` gagal dengan error `403 rate limit`, itu batas API GitHub.
> Tunggu beberapa menit lalu ulangi, atau set token GitHub:
> `$env:GITHUB_TOKEN="<token_anda>"` lalu ulangi `python -m camoufox fetch`.

---

## 5. Build sidecar Python (PyInstaller)

Dari root projek (dengan `.venv` aktif):

```powershell
python scripts/build_sidecar.py
```

Tunggu sampai `Build complete!`. Hasil ada di `sidecar/dist/sla-sidecar/` (~227 MB).

Verifikasi (harus mencetak `OK`):

```powershell
if (Test-Path sidecar\dist\sla-sidecar\sla-sidecar.exe) { echo OK }
```

### 5a. Uji cepat sidecar (opsional tapi disarankan)

Jalankan sidecar sebentar untuk memastikan ia hidup:

```powershell
$env:SLA_PORT="8765"; $env:SLA_NO_BROWSER="1"; $env:SLA_HEADLESS="1"
$env:SLA_DATA_DIR="$PWD\data-smoke"; $env:SLA_CAMOUFOX_DIR="$PWD\vendor\camoufox"
Start-Process -NoNewWindow .\sidecar\dist\sla-sidecar\sla-sidecar.exe
Start-Sleep -Seconds 12
Invoke-RestMethod http://127.0.0.1:8765/healthz
```

Bila mencetak `ok : True` berarti sidecar sehat. Hentikan prosesnya:

```powershell
Stop-Process -Name sla-sidecar -Force -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force data-smoke -ErrorAction SilentlyContinue
```

---

## 6. Build installer Electron (NSIS .exe)

```powershell
cd desktop
npm install
npm run dist:win
```

`npm install` mengunduh Electron (±100 MB). `npm run dist:win` mengemas semuanya menjadi
installer NSIS. Tunggu sampai selesai (beberapa menit).

Hasil ada di folder `desktop/dist/`:

```powershell
Get-ChildItem dist\*.exe
```

Anda akan melihat berkas:

- **`Scraping Lokal Otomatis Setup 0.1.0.exe`** (~287 MB) — inilah installer untuk operator.
- `win-unpacked/` — versi terurai (untuk uji cepat tanpa install).

---

## 7. Install & jalankan di Windows operator

Installer **belum di-sign** (tanpa sertifikat code-signing), jadi Windows SmartScreen mungkin
memperingatkan saat pertama dibuka.

1. Dobel-klik **`Scraping Lokal Otomatis Setup 0.1.0.exe`**.
   - Bila SmartScreen muncul: klik **"More info"** → **"Run anyway"**.
2. Install otomatis (NSIS oneClick per-user, **tanpa hak admin**, tanpa pilih folder).
3. Buka aplikasi dari Start Menu / shortcut desktop. Jendela aplikasi terbuka sendiri.

> Install silent (untuk deployment massal): `setup.exe /S`
> Data app terpasang: `%APPDATA%\scraping-local-auto-desktop\data` (terpisah dari repo).

---

## 8. Verifikasi hasil build (checklist untuk pembuild)

Sebelum mengirim installer ke operator, pastikan:

- [ ] `Test-Path vendor\camoufox\camoufox.exe` → OK
- [ ] `Test-Path sidecar\dist\sla-sidecar\sla-sidecar.exe` → OK
- [ ] Uji cepat sidecar (§5a) → `ok : True`
- [ ] Installer `.exe` ada di `desktop/dist/`
- [ ] Install di mesin ini, buka app, jendela muncul.
- [ ] Di app: tambah akun merchant, jalankan satu job, pastikan data transaksi masuk &
      Export Excel menghasilkan file. (Ini uji scraping nyata — penting sebelum disebar.)

---

## 9. Bila ada masalah

| Gejala | Penyebab / solusi |
|---|---|
| `python` menunjuk Python 2.7 | PATH lama. Pakai `py -3.12` atau path Python 3 penuh; atau hapus `C:\Python27` dari PATH. |
| `python : command not found` | §1a belum selesai / PATH belum di-refresh. Buka PowerShell baru. |
| `Activate.ps1` ditolak | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, atau pakai `activate.bat` di cmd. |
| `node`/`npm` not found | §1b belum selesai. Buka PowerShell baru setelah install Node. |
| `camoufox fetch` 403 rate limit | Batas API GitHub. Tunggu / set `$env:GITHUB_TOKEN` (§4). |
| PyInstaller `ModuleNotFoundError` | `.venv` belum aktif, atau `pip install -r requirements.txt` belum lengkap. Ulangi §3. |
| `npm install` gagal / lambat | Cek koneksi; coba `npm cache clean --force` lalu ulangi. |
| Build error `winCodeSign` symlink | Cache electron-builder gagal extract symlink tanpa privilege. Pre-extract manual: `7za x -y` (tanpa `-snld`) ke `%LOCALAPPDATA%\electron-builder\Cache\winCodeSign\<hash>\`. Lihat catatan di README. |
| Installer ditolak SmartScreen | "More info" → "Run anyway" (§7). Normal untuk app unsigned. |
| App terbuka tapi job gagal `[browser]` | Bundle Camoufox kurang lengkap — pastikan §4 verifikasi OK dan `vendor/camoufox` ikut ter-build. |
| Dialog "Port dipakai" | Aplikasi lain memakai port 8765. Tutup aplikasi itu, atau set `$env:SLA_PORT` ke port bebas. |
| Dialog "Sidecar berhenti" | Jalankan uji §5a untuk lihat error; kirim log ke developer pusat. |

Bila mentok, kirim **pesan error lengkap** (salin dari PowerShell) + langkah yang sedang
dijalankan ke developer pusat.

---

## Ringkasan perintah (salin-tempel berurutan, PowerShell)

```powershell
# 1. alat dasar: install Python 3.12, Node 22, Git (lihat §1, via installer web)

# 2. kode
git clone https://github.com/Miqman/scraping-local-auto.git
cd scraping-local-auto

# 3. lingkungan Python
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

# 4. browser Camoufox
python -m camoufox fetch
$cache = python -m camoufox path
New-Item -ItemType Directory -Force -Path vendor | Out-Null
Copy-Item -Recurse -Force $cache vendor\camoufox
Remove-Item -Recurse -Force vendor\camoufox\fonts -ErrorAction SilentlyContinue
if (Test-Path vendor\camoufox\camoufox.exe) { echo OK }

# 5. sidecar
python scripts/build_sidecar.py
if (Test-Path sidecar\dist\sla-sidecar\sla-sidecar.exe) { echo OK }

# 6. installer
cd desktop; npm install; npm run dist:win
Get-ChildItem dist\*.exe
```

Hasil akhir: `desktop/dist/Scraping Lokal Otomatis Setup 0.1.0.exe` — kirim ke operator Windows.
