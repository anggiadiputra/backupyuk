#!/usr/bin/env python3
"""
WP Backup generik -> rclone remote (Google Drive / S3-compatible).

Dipasang oleh install.sh. Semua konfigurasi dibaca dari satu file env:
  /etc/awan-backup/<SITE_KEY>.env

Variabel env yang didukung:
  SITE_NAME       nama site (dipakai di pesan notifikasi)
  DEST_KEY        kunci unik untuk path tujuan & nama file (mis. diurusin.id)
  SRC_DIR         folder yang di-sync (biasanya wp-content)
  DB_NAME         nama database (kosongkan = skip dump DB)
  RCLONE_REMOTE   remote rclone tujuan, mis. gdrive: atau alfatihah:
  DEST_PREFIX     prefix path di remote, mis. backup/  (boleh kosong)
  KEEP_DB_DAYS    retensi dump DB dalam hari (0 = simpan semua)
  FONNTE_TOKEN    token Fonnte (opsional)
  FONNTE_TARGET   nomor WA tujuan (opsional)
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def log(message: str) -> None:
    print(f"{datetime.now().strftime('%F %T')} {message}", flush=True)


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing env file: {path}")
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


def require_env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def send_fonnte(message: str) -> None:
    token = os.environ.get('FONNTE_TOKEN', '').strip()
    target = os.environ.get('FONNTE_TARGET', '').strip()
    if not token or not target:
        return
    cmd = [
        'curl', '-sS', '--max-time', '20', '-X', 'POST', 'https://api.fonnte.com/send',
        '-H', f'Authorization: {token}',
        '-F', f'target={target}',
        '-F', 'countryCode=62',
        '-F', f'message={message}',
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        output = (res.stdout or '').strip()
        if res.returncode != 0:
            log(f'[notify] Fonnte failed: {res.stderr.strip() or output}')
            return
        if output:
            try:
                payload = json.loads(output)
                if payload.get('status') is False:
                    log(f"[notify] Fonnte rejected: {payload.get('reason') or output}")
            except json.JSONDecodeError:
                log(f'[notify] Fonnte response: {output}')
    except Exception as exc:
        log(f'[notify] Fonnte exception: {exc}')


def run_rclone(args: list[str]) -> str:
    cmd = ['rclone'] + args
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(
            f"rclone {' '.join(args[:2])} failed ({res.returncode}): {res.stderr.strip()[:300]}"
        )
    return res.stdout


def sync_files(src: Path, dest: str) -> None:
    log(f'[files] syncing {src} -> {dest}')
    args = [
        'sync', str(src), dest,
        '--transfers', '4',
        '--checkers', '8',
        '--fast-list',
        '--stats', '0',
    ]
    min_age = os.environ.get('MIN_FILE_AGE', '').strip()
    if min_age:
        args += ['--min-age', min_age]
    run_rclone(args)
    log('[files] sync done')


def dump_database(db_name: str, tmpdir: Path) -> Path:
    stamp = datetime.now().strftime('%F_%H%M%S')
    gz_path = tmpdir / f'{db_name}_{stamp}.sql.gz'
    cmd = [
        'mariadb-dump' if shutil.which('mariadb-dump') else 'mysqldump',
        '--single-transaction',
        '--routines',
        '--triggers',
        '--events',
        '--no-tablespaces',
        db_name,
    ]
    log(f'[db] dumping {db_name} -> {gz_path.name}')
    with gzip.open(gz_path, 'wb', compresslevel=9) as gz:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None
        shutil.copyfileobj(proc.stdout, gz)
        stderr = proc.stderr.read() if proc.stderr is not None else b''
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f'db dump failed ({rc}): {stderr.decode(errors="replace").strip()[:300]}')
    return gz_path


def upload_db_dump(dest_dir: str, db_dump: Path) -> None:
    log(f'[db] uploading {db_dump.name}')
    run_rclone(['copyto', str(db_dump), f'{dest_dir}/{db_dump.name}'])
    log('[db] upload done')


def prune_old_db_dumps(dest_dir: str, keep_days: int) -> int:
    if keep_days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    out = run_rclone(['lsjson', dest_dir + '/', '--files-only'])
    items = json.loads(out or '[]')
    to_delete = []
    for item in items:
        if not item['Name'].endswith('.sql.gz'):
            continue
        mod = datetime.fromisoformat(item['ModTime'].replace('Z', '+00:00'))
        if mod < cutoff:
            to_delete.append(f'{dest_dir}/{item["Name"]}')
    if not to_delete:
        log('[db] retention: nothing to delete')
        return 0
    log(f'[db] retention: deleting {len(to_delete)} dump(s) older than {keep_days} days')
    for key in to_delete:
        run_rclone(['deletefile', key])
    return len(to_delete)


def main() -> int:
    start_time = datetime.now()
    if len(sys.argv) > 1:
        load_env_file(Path(sys.argv[1]))
    else:
        # fallback: cari satu-satunya env di /etc/awan-backup/
        envs = sorted(Path('/etc/awan-backup').glob('*.env'))
        if not envs:
            print('Usage: wp-backup.py /etc/awan-backup/<site>.env', file=sys.stderr)
            return 2
        load_env_file(envs[0])

    site_name = require_env('SITE_NAME')
    dest_key = require_env('DEST_KEY')
    src = Path(require_env('SRC_DIR'))
    db_name = os.environ.get('DB_NAME', '').strip()
    remote = require_env('RCLONE_REMOTE').rstrip('/')
    prefix = os.environ.get('DEST_PREFIX', '').strip().strip('/')
    keep_days = int(os.environ.get('KEEP_DB_DAYS', '30') or '0')
    host_name = os.uname().nodename

    files_dest = f'{remote}/{prefix}/{dest_key}/files'
    db_dest = f'{remote}/{prefix}/{dest_key}/db'

    try:
        if not src.is_dir():
            raise RuntimeError(f'SRC_DIR tidak ada: {src}')
        sync_files(src, files_dest)
        dump_path = None
        if db_name:
            with tempfile.TemporaryDirectory(prefix='wpbackup-') as td:
                dump_path = dump_database(db_name, Path(td))
                upload_db_dump(db_dest, dump_path)
            prune_old_db_dumps(db_dest, keep_days)
        duration = (datetime.now() - start_time).total_seconds()
        send_fonnte(
            f"✅ *BACKUP SUKSES*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🌐 Site: {site_name}\n"
            f"🖥 Host: {host_name}\n"
            f"⏱ Durasi: {duration:.0f} detik\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📁 Files: sync OK\n"
            + (f"🗄 DB: {dump_path.name}\n" if dump_path else "")
            + (f"🗑 Retensi DB: {keep_days} hari\n" if db_name else "")
            + f"🕐 {datetime.now().strftime('%d %b %Y %H:%M')}"
        )
        log(f'[{site_name}] backup completed in {duration:.0f}s')
        return 0
    except Exception as exc:
        duration = (datetime.now() - start_time).total_seconds()
        send_fonnte(
            f"❌ *BACKUP GAGAL*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🌐 Site: {site_name}\n"
            f"🖥 Host: {host_name}\n"
            f"⏱ Durasi: {duration:.0f} detik\n"
            f"━━━━━━━━━━━━━━━\n"
            f"⚠️ Error: {type(exc).__name__}\n"
            f"{str(exc)[:300]}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🕐 {datetime.now().strftime('%d %b %Y %H:%M')}"
        )
        raise


if __name__ == '__main__':
    raise SystemExit(main())
