# Panduan Operator — Scraping Lokal Otomatis

Aplikasi ini menarik data transaksi merchant dari website POS di komputer Anda, lalu membuat
file Excel siap kirim. Browser terbuka otomatis; **Anda hanya perlu menyelesaikan captcha/OTP
bila muncul** — sisanya berjalan sendiri. Semua data tersimpan lokal di komputer Anda.

## 1. Install (Windows)

1. Dobel-klik **`Scraping Lokal Otomatis Setup 0.1.0.exe`**.
2. Tunggu sampai selesai — tidak perlu pilih folder, tidak perlu hak admin, tidak perlu
   install Python atau browser apa pun (semua runtime sudah terbundel).
3. Buka aplikasi dari Start Menu / shortcut desktop. Jendela aplikasi terbuka sendiri.

> Mac/Linux: installer belum tersedia — hubungi developer pusat.
> Bila muncul dialog **"Port dipakai"**: ada aplikasi lain memakai port 8765 di komputer ini.
> Tutup aplikasi itu, lalu buka ulang aplikasi ini. Bila bingung, hubungi developer.

## 2. Tambah akun merchant (sekali per merchant)

1. Menu **Akun Merchant** → **Tambah Akun**.
2. Pilih POS, isi label bebas (mis. nama toko), username, dan password merchant.
3. Simpan. Password **terenkripsi** dan tidak pernah ditampilkan ulang — bila password merchant
   berubah, edit akun dan isi password baru.

## 3. Jalankan job

1. Menu **Job Baru** → pilih POS → pilih akun → rentang tanggal → cabang
   (daftar cabang terisi otomatis setelah login pertama; bila kosong, isi manual).
2. Klik **Jalankan**. Status terlihat live di **Detail Job** (antri / berjalan / menunggu user /
   selesai / gagal). Maks 2 job bersamaan; job dengan akun sama mengantri.
3. Bila website POS meminta **captcha / OTP / verifikasi Cloudflare**:
   - jendela browser scraper tampil ke depan dan status menjadi **MENUNGGU USER**;
   - selesaikan captcha/OTP **di jendela browser itu**; aplikasi lanjut sendiri ≤5 detik;
   - bila aplikasi tidak mendeteksi, klik tombol **"Sudah selesai"** di banner;
   - bila dibiarkan 10 menit, cabang itu gagal `[user_timeout]` — cabang lain tetap jalan,
     dan cabang gagal bisa di-**Retry**.
4. Hasil parsial tetap tersimpan: cabang sukses tidak hilang bila cabang lain gagal.
   Klik **Retry** per cabang atau **Retry semua gagal** (maks 3 attempt per cabang).

## 4. Ambil Excel

1. **Detail Job** → lihat preview transaksi → klik **Export Excel**.
2. File tersimpan di folder export aplikasi:
   `%APPDATA%\scraping-local-auto-desktop\data\exports`
   (nama file: `{pos}_{label}_{dari}_{sampai}_{idjob}.xlsx`).
3. Kirim file ke server sesuai instruksi tim (upload FTP otomatis menyusul di update berikutnya).
   Job kosong (0 transaksi) tetap menghasilkan Excel ber-header — itu normal, bukan gagal.

## 5. Jadwal harian (opsional)

**Akun Merchant** → kartu **Jadwal otomatis** → centang **Aktif** + pilih jam (default 06:00).
Setiap hari aplikasi menarik data **kemarin (D-1)** untuk akun itu. Aplikasi harus terbuka
pada jam jadwal; bila terlewat, job dijalankan saat aplikasi dibuka di hari yang sama.
Tombol **Job Baru** tetap bisa dipakai kapan pun untuk rentang lain (override manual).

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
| Cabang `[user_timeout]` | Captcha tidak diselesaikan ≤10 menit — retry cabang dan selesaikan captcha saat browser tampil. |
| Browser tidak terbuka / dialog "Sidecar berhenti" | Tutup aplikasi, buka ulang. Bila berulang, kirim pesan dialog ke developer. |
| Job lama `antri` | Akun yang sama sedang dipakai job lain, atau sudah 2 job berjalan — tunggu selesai. |
| Aplikasi tidak mau buka, dialog "Port dipakai" | Tutup aplikasi lain pemakai port 8765, atau hubungi developer. |

Butuh bantuan? Kirim **Salin detail error** dari Detail Job (sudah disanitasi, aman dibagikan)
ke developer pusat beserta nama POS dan label merchant.
