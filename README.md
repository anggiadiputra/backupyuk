# AWAN BACKUP INSTALLER

Backup otomatis WordPress (wp-content + database) ke **Google Drive / S3-compatible** dengan notifikasi **WhatsApp (Fonnte)**.

Satu installer bisa dipakai untuk **banyak website**, di **banyak server** — tiap site punya env, cron, log, dan lock sendiri.

## Cara Pakai

```bash
# 1. Clone di VPS (sebagai root)
git clone <url-repo-ini> && cd awan-backup-installer

# 2. Jalankan installer
sudo bash install.sh
```

Installer akan bertanya:

1. **Remote rclone** — pakai yang sudah ada, atau buat baru (Google Drive / S3-compatible)
2. **Nama website** — untuk notifikasi, mis. `alfatihah.com`
3. **Kunci path backup** — default sama dengan nama site
4. **Folder yang di-backup** — biasanya `.../public_html/wp-content`
5. **Nama database** — kosongkan kalau tidak mau backup DB
6. **Retensi dump DB** — default 30 hari (dump lebih tua dihapus otomatis)
7. **Token & nomor Fonnte** — kosongkan kalau tidak mau notifikasi WA
8. **Jadwal cron** — harian 02:00 / 03:00 / mingguan / kustom
9. **Test backup pertama** langsung — ya/tidak

## Yang Dipasang

| File | Fungsi |
|---|---|
| `/etc/awan-backup/<site>.env` | konfigurasi site (chmod 600) |
| `/usr/local/bin/wp-backup.py` | script backup generik |
| `/usr/local/bin/run-wp-backup-<site>.sh` | runner cron **dengan lock** (anti proses tumpuk) |
| `/etc/cron.d/wp-backup-<site>` | penjadwal |
| `/var/log/wp-backup-<site>.log` | log |

## Struktur di Storage Tujuan

```
<remote>:backup/<site>/files/    ← mirror wp-content (inkremental)
<remote>:backup/<site>/db/       ← dump .sql.gz harian
```

## Menambah Site Kedua, Ketiga, dst.

Jalankan lagi `sudo bash install.sh` di server yang sama — jawab pertanyaan dengan data site baru. Semua site hidup berdampingan (env & cron terpisah per site).

## Perintah Berguna

```bash
# lihat log
tail -20 /var/log/wp-backup-<site>.log

# backup manual
/usr/local/bin/run-wp-backup-<site>.sh

# cek progres/ukuran backup di storage
rclone size "<remote>:backup/<site>/"
```

## Catatan Google Drive

- Untuk performa maksimal, pakai **Client ID sendiri** (bukan shared rclone) — panduan lengkap ada di `PANDUAN-BACKUP.txt`.
- Aplikasi OAuth Google mode **Testing** hanya menerima email yang terdaftar di **Test users**; kalau authorize kena `Error 403: access_denied`, tambahkan email di *OAuth consent screen → Audience → Test users*.
