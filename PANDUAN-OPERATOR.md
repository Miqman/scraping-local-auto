# Panduan Operator — Scraping Lokal Otomatis

Aplikasi ini menarik data transaksi merchant dari website POS di komputer Anda, lalu membuat
file Excel siap kirim. Scraping berjalan di latar belakang (tanpa jendela browser); kebanyakan
captcha/OTP lolos otomatis — sisanya berjalan sendiri. Semua data tersimpan lokal di komputer Anda.

## 1. Install (Windows)

1. Dobel-klik **`Scraping Lokal Otomatis Setup 0.1.0.exe`**.
2. Tunggu sampai selesai — tidak perlu pilih folder, tidak perlu hak admin, tidak perlu
   install Python atau browser apa pun (semua runtime sudah terbundel).
3. Buka aplikasi dari Start Menu / shortcut desktop. Jendela aplikasi terbuka sendiri.

> Mac: installer `.dmg` belum dirilis (build harus di mesin Mac — hubungi developer pusat).
> Linux: belum tersedia.
> Bila muncul dialog **"Port dipakai"**: ada aplikasi lain memakai port 8765 di komputer ini.
> Tutup aplikasi itu, lalu buka ulang aplikasi ini. Bila bingung, hubungi developer.

## 2. Tambah akun merchant (sekali per merchant)

1. Menu **Akun Merchant** → **Tambah Akun**.
2. Pilih POS, isi label bebas (mis. nama toko), username, dan password merchant.
3. Simpan. Password **terenkripsi** dan tidak pernah ditampilkan ulang — bila password merchant
   berubah, edit akun dan isi password baru.

## 3. Jalankan job

1. Menu **Job Baru** → pilih POS → pilih akun → rentang tanggal → cabang.
   - Tanggal otomatis terisi **menyambung job terakhir akun itu**; ada tombol preset
     (Kemarin / 7 hari / 30 hari / Bulan ini / Lanjut job terakhir) bila ingin rentang lain.
   - Cabang: gunakan **Pilih semua / Kosongkan**, atau centang satu per satu
     (daftar terisi otomatis setelah login pertama; bila kosong, isi manual).
2. Klik **Jalankan**. Status terlihat live di **Detail Job** (antri / berjalan / menunggu user /
   selesai / gagal). Maks 2 job bersamaan; job dengan akun sama mengantri.
3. Bila website POS meminta **captcha / OTP / verifikasi Cloudflare**:
   - scraper mencoba menyelesaikannya otomatis; bila belum lolos, status menjadi **MENUNGGU USER**;
   - tunggu sebentar lalu klik **Retry** pada cabang itu — sesi sering sudah lolos pada percobaan berikutnya;
   - bila dibiarkan 10 menit, cabang itu gagal `[user_timeout]` — cabang lain tetap jalan,
     dan cabang gagal bisa di-**Retry**;
   - captcha yang terus berulang: hubungi developer pusat (versi developer menampilkan browser
     agar captcha bisa diselesaikan manual).
4. Hasil parsial tetap tersimpan: cabang sukses tidak hilang bila cabang lain gagal.
   Klik **Retry** per cabang atau **Retry semua gagal** (maks 3 attempt per cabang).
5. **Dashboard menampilkan job terbaru per akun** (default) — run berulang tidak membuat daftar
   terlihat dobel. Klik **"Tampilkan semua riwayat"** di bawah tabel untuk melihat semua job lama.
   Data transaksi tidak diduplikasi: transaksi yang sama antar-run disimpan sekali (versi terbaru);
   Excel per job hanya berisi transaksi yang belum pernah terkirim di job sebelumnya.

## 4. Ambil Excel

1. **Detail Job** → lihat preview transaksi → klik **Export Excel**.
2. File langsung terunduh ke folder **Downloads** komputer Anda (Explorer terbuka menunjuk file itu).
   Nama file: `{pos}_{label}_{dari}_{sampai}_{idjob}.xlsx`. Salinan juga tersimpan di folder export
   aplikasi: `%APPDATA%\scraping-local-auto-desktop\data\exports`.
3. Kirim file ke server sesuai instruksi tim (upload FTP otomatis menyusul di update berikutnya).
   Job kosong (0 transaksi) tetap menghasilkan Excel ber-header — itu normal, bukan gagal.

## 5. Jadwal otomatis (opsional)

Menu **Jadwal Otomatis** (sidebar Konfigurasi) → klik nama POS untuk membuka daftar akunnya →
isi **interval menit** (minimal 1, default 60) + centang **Aktif** → simpan (ikon check).
Aplikasi menarik data **menyambung job terakhir akun itu** — mulai sehari setelah tanggal akhir job
terakhir sampai hari ini (job pertama: kemarin–hari ini), dan **memakai cabang yang sama dengan job
terakhir** (bila job terakhir hanya 1 cabang, jadwal juga hanya 1 cabang itu — bukan semua cabang).
Jadi tidak selalu tanggal yang sama; rentang & cabang maju mengikuti job yang sudah ada.
Transaksi yang sama tidak disimpan dobel (dedup per akun).

- **Hanya saat aplikasi terbuka.** Aplikasi tertutup = jadwal tidak berjalan; tidak ada backfill
  hari-hari terlewat. Bila interval sudah lewat saat aplikasi dibuka, job dijalankan sekali (catch-up).
- Akun yang sedang punya job aktif dilewati sampai antrian kosong (tidak menumpuk).
- Bila job terakhir gagal `[auth]` (password salah), jadwal ditahan sampai Anda retry/manual —
  mencegah akun terkunci. Perbarui password lalu retry.
- Job muncul di Dashboard seperti job manual (retry/export sama). Tombol **Job Baru** tetap untuk
  rentang lain (override manual).

## 6. Keamanan komputer Anda

- **Aktifkan disk encryption OS**: BitLocker (Windows), FileVault (Mac), LUKS (Linux).
  Data transaksi berisi data pelanggan; enkripsi disk melindungi bila laptop hilang/dicuri.
  (Enkripsi database tingkat aplikasi menyusul di update M2.)
- Aplikasi hanya berjalan di localhost (127.0.0.1) — tidak terjangkau komputer lain di jaringan.
- Jangan membagikan folder data aplikasi (`%APPDATA%\scraping-local-auto-desktop`) ke siapa pun.
- Banner kuning "OS keychain tidak tersedia": aplikasi tetap jalan dengan keyfile lokal;
  pastikan disk encryption aktif dan lapor developer bila memungkinkan.

## 7. Bila ada masalah

| Gejala | Yang dilakukan |
|---|---|
| Job `failed [interrupted]` setelah restart/tutup aplikasi | Normal by design — klik **Retry** cabang; data yang sudah selesai tetap utuh. |
| Cabang `[auth]` | Password merchant berubah/salah — edit akun (password baru), lalu retry. Tidak ada retry otomatis agar akun tidak terkunci. |
| Cabang `[ui_changed]` | Website POS berubah — klik **Salin detail error** (teks aman/redacted) dan kirim ke developer pusat. |
| Cabang `[user_timeout]` | Captcha tidak lolos ≤10 menit — klik **Retry** cabang; bila berulang, hubungi developer pusat. |
| Dialog "Sidecar berhenti" | Tutup aplikasi, buka ulang. Bila berulang, kirim pesan dialog ke developer. (Browser scraper memang tidak tampil — itu normal.) |
| Job lama `antri` | Akun yang sama sedang dipakai job lain, atau sudah 2 job berjalan — tunggu selesai. |
| Aplikasi tidak mau buka, dialog "Port dipakai" | Tutup aplikasi lain pemakai port 8765, atau hubungi developer. |

Butuh bantuan? Kirim **Salin detail error** dari Detail Job (sudah disanitasi, aman dibagikan)
ke developer pusat beserta nama POS dan label merchant.
