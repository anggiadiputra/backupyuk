#!/bin/bash
# ============================================================
# AWAN BACKUP INSTALLER
# Backup otomatis WordPress (files + database) ke rclone remote
# (Google Drive / S3-compatible) + notifikasi WhatsApp Fonnte.
#
# Pemakaian:
#   git clone <repo ini> && cd awan-backup-installer
#   sudo bash install.sh
# ============================================================
set -euo pipefail

# ---------------- warna ----------------
B='\033[1m'; G='\033[32m'; Y='\033[33m'; R='\033[31m'; N='\033[0m'
say()  { echo -e "${G}==>${N} ${B}$1${N}"; }
ask()  { echo -e "${Y}?${N} ${B}$1${N}"; }
warn() { echo -e "${R}!${N} $1"; }

# ---------------- harus root ----------------
if [[ $EUID -ne 0 ]]; then
  warn "Jalankan sebagai root: sudo bash install.sh"
  exit 1
fi

echo ""
echo "=============================================="
echo "   AWAN BACKUP INSTALLER"
echo "   Files + Database -> rclone remote"
echo "=============================================="
echo ""

# ---------------- 1. dependensi ----------------
say "Cek dependensi..."
MISSING=()
command -v rclone      >/dev/null || MISSING+=("rclone")
command -v python3     >/dev/null || MISSING+=("python3")
command -v mariadb-dump >/dev/null || command -v mysqldump >/dev/null || MISSING+=("mariadb-dump/mysqldump")
if [[ ${#MISSING[@]} -gt 0 ]]; then
  warn "Belum terinstall: ${MISSING[*]}"
  read -rp "Install sekarang via apt? [Y/n] " yn
  if [[ ! "${yn:-Y}" =~ ^[Yy] ]]; then
    warn "Installer butuh paket itu. Keluar."
    exit 1
  fi
  apt-get update -qq
  apt-get install -y -qq rclone python3 mariadb-client 2>/dev/null || \
  apt-get install -y -qq rclone python3 mysql-client
fi
say "Dependensi OK."

# ---------------- 2. remote rclone ----------------
echo ""
REMOTE_NAME=""
if rclone listremotes 2>/dev/null | grep -q ':'; then
  echo "Remote rclone yang sudah ada:"
  rclone listremotes
  echo ""
  ask "Pakai remote yang ada (ketik namanya, mis. gdrive:) atau buat baru [baru]?"
  read -rp "> " choice
  choice="${choice:-baru}"
  if [[ "$choice" != "baru" && "$choice" != *":"* ]]; then
    choice="${choice}:"
  fi
  if [[ "$choice" != "baru" ]]; then
    REMOTE_NAME="${choice%:}"
  fi
fi

if [[ -z "$REMOTE_NAME" ]]; then
  ask "Nama remote baru (mis. gdrive, alfatihah):"
  read -rp "> " REMOTE_NAME
  [[ -z "$REMOTE_NAME" ]] && { warn "Nama remote wajib diisi."; exit 1; }
  echo ""
  echo "Pilih tipe storage:"
  echo "  1) Google Drive"
  echo "  2) S3-compatible (AWS S3 / Neva / Wasabi / dll)"
  read -rp "Pilihan [1]: " stype
  stype="${stype:-1}"
  case "$stype" in
    2)
      ask "Endpoint S3 (mis. https://s3.nevaobjects.id):"
      read -rp "> " S3_ENDPOINT
      ask "Access Key ID:"
      read -rp "> " S3_KEY
      ask "Secret Access Key:"
      read -rs -rp "> " S3_SECRET; echo ""
      ask "Bucket name:"
      read -rp "> " S3_BUCKET
      ask "Region (auto boleh):"
      read -rp "> " S3_REGION; S3_REGION="${S3_REGION:-auto}"
      rclone config create "$REMOTE_NAME" s3 \
        provider Other \
        access_key_id "$S3_KEY" \
        secret_access_key "$S3_SECRET" \
        endpoint "$S3_ENDPOINT" \
        region "$S3_REGION" >/dev/null
      ;;
    *)
      rclone config create "$REMOTE_NAME" drive scope drive >/dev/null
      echo ""
      say "Sekarang otorisasi Google Drive."
      say "Karena server ini tidak punya browser, jalankan DI LAPTOP Anda:"
      echo ""
      echo -e "    ${B}rclone authorize \"drive\" \"1073292335940-ok9m21oa1tb8ufu44mmsh3s2sh114u89.apps.googleusercontent.com\" \"$(rclone config show "$REMOTE_NAME" | grep client_secret | cut -d' ' -f3-)\"${N}"
      echo ""
      say "(install rclone di laptop dulu kalau belum: brew install rclone)"
      say "Login akun Google -> Allow -> copy token yang muncul di laptop"
      ask "Paste token di sini:"
      read -rp "> " CONFIG_TOKEN
      rclone config update "$REMOTE_NAME" config_token "$CONFIG_TOKEN" >/dev/null 2>&1 || \
        warn "rclone config update gagal (bug umum). Token akan ditulis manual."
      # fallback tulis manual ke file config
      if ! rclone about "${REMOTE_NAME}:" >/dev/null 2>&1; then
        TOKEN_JSON=$(python3 - "$CONFIG_TOKEN" <<'EOF'
import base64, json, sys
tok = sys.argv[1]
print(json.loads(base64.b64decode(tok + '=' * (-len(tok) % 4)))['token'])
EOF
)
        CONF=$(rclone config file | tail -1)
        python3 - "$CONF" "$REMOTE_NAME" "$TOKEN_JSON" <<'EOF'
import sys, re
conf, name, token = sys.argv[1:4]
text = open(conf).read()
text = re.sub(rf'(\[{name}\](?:\n[^\[]*?)?token = )[^\n]*', rf'\g<1>{token}', text, count=1)
open(conf, 'w').write(text)
EOF
      fi
      ;;
  esac
fi

# ---------------- verifikasi remote ----------------
echo ""
say "Verifikasi remote ${REMOTE_NAME}:..."
if rclone about "${REMOTE_NAME}:" >/dev/null 2>&1; then
  rclone about "${REMOTE_NAME}:" | head -3
elif rclone lsd "${REMOTE_NAME}:" >/dev/null 2>&1; then
  say "Remote OK (S3)."
else
  warn "Remote ${REMOTE_NAME}: TIDAK bisa diakses. Perbaiki dulu, jalankan installer lagi."
  exit 1
fi

# ---------------- 3. data site ----------------
echo ""
ask "Nama website (untuk notifikasi, mis. alfatihah.com):"
read -rp "> " SITE_NAME
[[ -z "$SITE_NAME" ]] && { warn "Nama site wajib diisi."; exit 1; }

DEFAULT_KEY="$SITE_NAME"
ask "Kunci unik untuk path backup [${DEFAULT_KEY}]:"
read -rp "> " DEST_KEY
DEST_KEY="${DEST_KEY:-$DEFAULT_KEY}"

ask "Folder yang di-backup (mis. /home/user/web/site.com/public_html/wp-content):"
read -rp "> " SRC_DIR
if [[ ! -d "$SRC_DIR" ]]; then
  warn "Folder tidak ditemukan: $SRC_DIR"
  read -rp "Lanjut saja? [y/N] " yn
  [[ "${yn:-N}" =~ ^[Yy] ]] || exit 1
fi

ask "Nama database (kosongkan kalau tidak mau backup DB):"
read -rp "> " DB_NAME

KEEP_DB_DAYS=30
if [[ -n "$DB_NAME" ]]; then
  ask "Retensi dump DB berapa hari [30]:"
  read -rp "> " KEEP_DB_DAYS
  KEEP_DB_DAYS="${KEEP_DB_DAYS:-30}"
fi

# ---------------- 4. notifikasi ----------------
echo ""
ask "Token Fonnte (kosongkan = tanpa notifikasi):"
read -rp "> " FONNTE_TOKEN
FONNTE_TARGET=""
if [[ -n "$FONNTE_TOKEN" ]]; then
  ask "Nomor WA tujuan (08xx):"
  read -rp "> " FONNTE_TARGET
fi

# ---------------- 5. jadwal ----------------
echo ""
echo "Jadwal backup (cron):"
echo "  1) Harian  02:00"
echo "  2) Harian  03:00"
echo "  3) Mingguan (Minggu 02:00)"
echo "  4) Jam kustom"
read -rp "Pilihan [1]: " sched
sched="${sched:-1}"
case "$sched" in
  2) CRON_SCHED="0 3 * * *" ;;
  3) CRON_SCHED="0 2 * * 0" ;;
  4) read -rp "Ekspresi cron (mis. '30 2 * * *'): " CRON_SCHED ;;
  *) CRON_SCHED="0 2 * * *" ;;
esac

# ---------------- 6. tulis file ----------------
say "Menulis file..."
install -d -m 700 /etc/awan-backup
ENV_FILE="/etc/awan-backup/${DEST_KEY}.env"
cat > "$ENV_FILE" <<EOF
# Dibuat oleh awan-backup-installer pada $(date '+%F %T')
SITE_NAME=${SITE_NAME}
DEST_KEY=${DEST_KEY}
SRC_DIR=${SRC_DIR}
DB_NAME=${DB_NAME}
RCLONE_REMOTE=${REMOTE_NAME}:
DEST_PREFIX=backup
KEEP_DB_DAYS=${KEEP_DB_DAYS}
FONNTE_TOKEN=${FONNTE_TOKEN}
FONNTE_TARGET=${FONNTE_TARGET}
EOF
chmod 600 "$ENV_FILE"

install -m 700 wp-backup.py /usr/local/bin/wp-backup.py

RUNNER="/usr/local/bin/run-wp-backup-${DEST_KEY}.sh"
cat > "$RUNNER" <<EOF
#!/bin/sh
set -e
exec 9>/var/lock/wp-backup-${DEST_KEY}.lock
flock -n 9 || { echo "[\$(date '+%F %T')] backup ${DEST_KEY} masih jalan, skip" >> /var/log/wp-backup-${DEST_KEY}.log; exit 0; }
exec /usr/bin/python3 /usr/local/bin/wp-backup.py /etc/awan-backup/${DEST_KEY}.env >> /var/log/wp-backup-${DEST_KEY}.log 2>&1
EOF
chmod 700 "$RUNNER"

CRON_FILE="/etc/cron.d/wp-backup-${DEST_KEY}"
echo "${CRON_SCHED} root ${RUNNER}" > "$CRON_FILE"
chmod 644 "$CRON_FILE"
touch "/var/log/wp-backup-${DEST_KEY}.log"

say "File terpasang:"
echo "  env    : ${ENV_FILE}"
echo "  script : /usr/local/bin/wp-backup.py"
echo "  runner : ${RUNNER} (dengan lock anti-tumpuk)"
echo "  cron   : ${CRON_FILE} -> '${CRON_SCHED}'"
echo "  log    : /var/log/wp-backup-${DEST_KEY}.log"

# ---------------- 7. test ----------------
echo ""
ask "Jalankan backup pertama sekarang untuk test? [Y/n]"
read -rp "> " yn
if [[ ! "${yn:-Y}" =~ ^[Yy] ]]; then
  say "Selesai. Backup pertama akan jalan sesuai jadwal cron."
  exit 0
fi

say "Menjalankan backup pertama (lihat log: tail -f /var/log/wp-backup-${DEST_KEY}.log)..."
if "$RUNNER"; then
  say "Backup pertama BERHASIL! Cek notifikasi WA Anda."
else
  warn "Backup pertama GAGAL. Cek: tail -30 /var/log/wp-backup-${DEST_KEY}.log"
  exit 1
fi
