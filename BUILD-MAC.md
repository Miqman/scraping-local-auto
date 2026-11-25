# Build Installer macOS — Panduan Lengkap

Panduan ini untuk membangun **`Scraping Lokal Otomatis Setup.dmg`** di mesin Mac.
Tidak perlu paham koding — cukup ikuti langkah berurutan, salin-tempel perintah.

> **Kenapa harus di Mac?** Sidecar Python dibekukan dengan PyInstaller, yang tidak bisa
> cross-compile dari Windows/Linux. Binary Mac hanya bisa dibuat di macOS. Browser Camoufox
> untuk Mac juga hanya bisa diunduh di Mac.

---

## 0. Yang dibutuhkan

- Mac (Intel atau Apple Silicon M1/M2/M3/M4 — keduanya bisa).
- macOS 12 atau lebih baru.
- Koneksi internet (unduh ±2 GB: Electron + Camoufox).
- ±5 GB ruang disk kosong.
- Aplikasi **Terminal** (bawaan Mac: Finder → Applications → Utilities → Terminal).

Cek arsitektur Mac Anda (untuk catatan hasil build):

```bash
uname -m
```

- `arm64` = Apple Silicon → hasil build app Apple Silicon.
- `x86_64` = Intel → hasil build app Intel.

> App hasil build hanya jalan di arsitektur yang sama dengan Mac pembuild. Bila operator
> pakai Mac Intel sedangkan Anda build di Apple Silicon (atau sebaliknya), build di Mac
> dengan arsitektur yang sesuai, atau lihat §7 (build universal).

---

## 1. Install alat dasar (sekali saja)

### 1a. Homebrew (package manager Mac)

Tempel di Terminal, tekan Enter, ikuti prompt (masukkan password Mac bila diminta):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Setelah selesai, bila Terminal menyuruh menjalankan perintah `eval ...` untuk menambahkan
brew ke PATH, jalankan perintah itu. Lalu cek:

```bash
brew --version
```

### 1b. Python 3.12, Node.js 22, Git

```bash
brew install python@3.12 node@22 git
```

Node versi 22 mungkin perlu ditambahkan ke PATH. Jalankan:

```bash
echo 'export PATH="/opt/homebrew/opt/node@22/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

> Mac Intel: path brew biasanya `/usr/local/opt/node@22/bin`. Bila perintah di atas tidak
> membuat `node` dikenali, ganti `/opt/homebrew` dengan `/usr/local`.

Cek semuanya terpasang:

```bash
python3.12 --version    # harus 3.12.x
node --version          # harus v22.x
git --version
```

> **Penting:** gunakan `python3.12` (bukan `python` polos) di semua langkah berikut, supaya
> versi Python konsisten dengan yang teruji.

---

## 2. Ambil kode projek

```bash
git clone https://github.com/Miqman/scraping-local-auto.git
cd scraping-local-auto
```

Bila sudah pernah clone sebelumnya, cukup perbarui:

```bash
cd scraping-local-auto
git pull origin main
```

---

## 3. Setup lingkungan Python

Buat virtual environment (ruang Python terisolasi untuk projek ini):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Setelah aktif, prompt Terminal biasanya diawali `(.venv)`. **Semua perintah Python berikut
dijalankan dengan `.venv` aktif.** Bila nanti membuka Terminal baru, aktifkan lagi dengan
`source .venv/bin/activate` dari folder projek.

Install dependensi + PyInstaller:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
```

> `requirements.txt` sudah mengunci `camoufox==0.4.11` (versi teruji). Jangan mengubahnya.

---

## 4. Unduh browser Camoufox (bundle Mac)

```bash
python -m camoufox fetch
```

Ini mengunduh browser Camoufox untuk Mac (±1 GB). Tunggu sampai muncul
`Camoufox successfully installed.`

Salin browser ke folder `vendor/camoufox` (yang akan dibundel ke installer), buang folder
fonts yang besar (opsional, sudah teruji launch tanpanya):

```bash
CACHE="$(python -m camoufox path)"
mkdir -p vendor
cp -R "$CACHE" vendor/camoufox
rm -rf vendor/camoufox/fonts
```

Verifikasi bundle Mac lengkap (harus mencetak `OK`):

```bash
test -f vendor/camoufox/Camoufox.app/Contents/MacOS/camoufox && echo OK
```

> Bila `camoufox fetch` gagal dengan error `403 rate limit`, itu karena batas API GitHub.
> Tunggu beberapa menit lalu ulangi, atau set token GitHub:
> `export GITHUB_TOKEN=<token_anda>` lalu ulangi `python -m camoufox fetch`.

---

## 5. Build sidecar Python (PyInstaller)

Dari root projek (dengan `.venv` aktif):

```bash
python scripts/build_sidecar.py
```

Tunggu sampai `Build complete!`. Hasil ada di `sidecar/dist/sla-sidecar/`.

Verifikasi (harus mencetak `OK`):

```bash
test -f sidecar/dist/sla-sidecar/sla-sidecar && echo OK
```

### 5a. Uji cepat sidecar (opsional tapi disarankan)

Jalankan sidecar sebentar untuk memastikan ia hidup:

```bash
SLA_PORT=8765 SLA_NO_BROWSER=1 SLA_HEADLESS=1 \
  SLA_DATA_DIR="$PWD/data-smoke" \
  SLA_CAMOUFOX_DIR="$PWD/vendor/camoufox" \
  ./sidecar/dist/sla-sidecar/sla-sidecar &
sleep 12
curl -s http://127.0.0.1:8765/healthz
```

Bila mencetak `{"ok":true}` berarti sidecar sehat. Hentikan prosesnya:

```bash
kill %1
rm -rf data-smoke
```

---

## 6. Build installer Electron (.dmg)

```bash
cd desktop
npm install
npm run dist:mac
```

`npm install` mengunduh Electron (±100 MB). `npm run dist:mac` mengemas semuanya menjadi
installer. Tunggu sampai selesai (beberapa menit).

Hasil ada di folder `desktop/dist/`:

```bash
ls -la dist/*.dmg dist/*.zip
```

Anda akan melihat berkas seperti:

- `Scraping Lokal Otomatis-0.1.0-arm64.dmg` (Apple Silicon) **atau**
  `Scraping Lokal Otomatis-0.1.0.dmg` (Intel)
- berkas `.zip` dengan nama serupa.

**`.dmg` inilah installer yang dikirim ke operator Mac.**

---

## 7. (Opsional) Build universal — jalan di Intel & Apple Silicon

Bila operator Mac campur arsitektur, build universal agar satu installer jalan di keduanya.
Ini menggabungkan dua build, jadi lebih lama dan butuh kedua arsitektur tersedia.

Cara paling andal: build di masing-masing arsitektur lalu gabung dengan `lipo`, **atau**
minta electron-builder membuat universal:

```bash
cd desktop
npm run dist:mac -- --universal
```

> Catatan: sidecar PyInstaller tetap per-arsitektur. Build universal penuh memerlukan
> Python universal2 dan Camoufox untuk kedua arsitektur — di luar cakupan panduan dasar ini.
> Untuk kebutuhan tim sekarang, build per-arsitektur (§6) sudah cukup; cocokkan dengan Mac operator.

---

## 8. Install & jalankan di Mac operator

Karena installer **belum di-sign** (tanpa Apple Developer ID), macOS Gatekeeper akan
memperingatkan saat pertama dibuka. Cara membuka:

1. Dobel-klik `.dmg`, seret app **Scraping Lokal Otomatis** ke folder **Applications**.
2. Di Finder → Applications, **klik-kanan** (atau Control-klik) app → **Open** → dialog
   konfirmasi → **Open** lagi. (Jangan dobel-klik biasa pertama kali — akan ditolak.)
3. Bila masih ditolak, buka Terminal dan jalankan:

   ```bash
   xattr -d com.apple.quarantine "/Applications/Scraping Lokal Otomatis.app"
   ```

   lalu buka app seperti biasa.

Data aplikasi (database, profil browser, export) tersimpan di:
`~/Library/Application Support/scraping-local-auto-desktop/data`

---

## 9. Verifikasi hasil build (checklist untuk pembuild)

Sebelum mengirim `.dmg` ke operator, pastikan:

- [ ] `test -f vendor/camoufox/Camoufox.app/Contents/MacOS/camoufox` → OK
- [ ] `test -f sidecar/dist/sla-sidecar/sla-sidecar` → OK
- [ ] Uji cepat sidecar (§5a) → `{"ok":true}`
- [ ] `.dmg` ada di `desktop/dist/`
- [ ] Install `.dmg` di Mac ini, buka app (klik-kanan → Open), jendela aplikasi muncul.
- [ ] Di app: tambah akun merchant, jalankan satu job, pastikan data transaksi masuk &
      Export Excel menghasilkan file. (Ini uji scraping nyata — penting sebelum disebar.)

---

## 10. Bila ada masalah

| Gejala | Penyebab / solusi |
|---|---|
| `python3.12: command not found` | §1b belum selesai / PATH belum di-refresh. Buka Terminal baru, atau `source ~/.zshrc`. |
| `node: command not found` | PATH node@22 belum masuk (§1b). Mac Intel: pakai `/usr/local/opt/node@22/bin`. |
| `camoufox fetch` 403 rate limit | Batas API GitHub. Tunggu / set `GITHUB_TOKEN` (§4). |
| `camoufox fetch` gagal unduh model / jaringan | Ulangi perintah; bila berulang, cek koneksi / coba jaringan lain. |
| PyInstaller error `ModuleNotFoundError` | `.venv` belum aktif, atau `pip install -r requirements.txt` belum lengkap. Ulangi §3. |
| `npm install` gagal / lambat | Cek koneksi; coba `npm cache clean --force` lalu ulangi. |
| App ditolak Gatekeeper | Klik-kanan → Open, atau `xattr -d com.apple.quarantine` (§8). |
| App terbuka tapi job gagal `[browser]` | Bundle Camoufox kurang lengkap — pastikan §4 verifikasi OK dan `vendor/camoufox` ikut ter-build. |
| Sidecar mati / dialog "Sidecar berhenti" | Jalankan uji §5a untuk lihat error; kirim log ke developer pusat. |

Bila mentok, kirim **pesan error lengkap** (salin dari Terminal) + langkah yang sedang
dijalankan ke developer pusat.

---

## Ringkasan perintah (salin-tempel berurutan)

```bash
# 1. alat dasar (sekali)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python@3.12 node@22 git
echo 'export PATH="/opt/homebrew/opt/node@22/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc

# 2. kode
git clone https://github.com/Miqman/scraping-local-auto.git
cd scraping-local-auto

# 3. lingkungan Python
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

# 4. browser Camoufox
python -m camoufox fetch
CACHE="$(python -m camoufox path)"
mkdir -p vendor && cp -R "$CACHE" vendor/camoufox && rm -rf vendor/camoufox/fonts
test -f vendor/camoufox/Camoufox.app/Contents/MacOS/camoufox && echo OK

# 5. sidecar
python scripts/build_sidecar.py
test -f sidecar/dist/sla-sidecar/sla-sidecar && echo OK

# 6. installer
cd desktop && npm install && npm run dist:mac
ls -la dist/*.dmg
```

Hasil akhir: `desktop/dist/*.dmg` — kirim ke operator Mac.
