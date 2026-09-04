#!/usr/bin/env python3
"""
Install backup untuk SEMUA website yang belum terpasang, urut dari yang TERKECIL.
Membaca hasil scan-sites.py (--json), lalu untuk tiap site membuat:
  - /etc/awan-backup/<domain>.env
  - cron /etc/cron.d/wp-backup-<domain>   (jadwal di-stagger otomatis)
  - runner dengan lock (dipakai wp-backup.py langsung, pola sama dengan installer)

Pemakaian:
    sudo python3 scan-sites.py --json /tmp/sites.json
    sudo python3 install-batch.py /tmp/sites.json \
        --remote gdrive --prefix backup \
        --start-hour 2 --interval-min 30 \
        --fonnte-token XXX --fonnte-target 08xx
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DEST = '/usr/local/bin/wp-backup.py'


def sh(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def human(nbytes: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if nbytes < 1024:
            return f'{nbytes:.0f} {unit}' if unit in ('B', 'KB') else f'{nbytes:.1f} {unit}'
        nbytes /= 1024
    return f'{nbytes:.1f} TB'


def write_runner(domain: str, env_file: str, log_file: str) -> str:
    runner = f'/usr/local/bin/run-wp-backup-{domain}.sh'
    content = f"""#!/bin/sh
set -e
exec 9>/var/lock/wp-backup-{domain}.lock
flock -n 9 || {{ echo "[$(date '+%F %T')] backup {domain} masih jalan, skip" >> {log_file}; exit 0; }}
exec /usr/bin/python3 {SCRIPT_DEST} {env_file} >> {log_file} 2>&1
"""
    Path(runner).write_text(content)
    Path(runner).chmod(0o700)
    return runner


def write_cron(domain: str, cron_expr: str, runner: str) -> None:
    cron_file = f'/etc/cron.d/wp-backup-{domain}'
    Path(cron_file).write_text(f'{cron_expr} root {runner}\n')
    Path(cron_file).chmod(0o644)


def main() -> int:
    ap = argparse.ArgumentParser(description='Batch install backup untuk semua site belum terpasang')
    ap.add_argument('scan_json', help='hasil scan-sites.py --json')
    ap.add_argument('--remote', required=True, help='nama remote rclone (tanpa titik dua)')
    ap.add_argument('--prefix', default='backup', help='prefix path di remote (default: backup)')
    ap.add_argument('--start-hour', type=int, default=2, help='jam mulai cron site pertama (default 2)')
    ap.add_argument('--interval-min', type=int, default=30, help='jarak antar jadwal site (default 30 menit)')
    ap.add_argument('--keep-db-days', type=int, default=30)
    ap.add_argument('--fonnte-token', default='')
    ap.add_argument('--fonnte-target', default='')
    ap.add_argument('--dry-run', action='store_true', help='tampilkan rencana saja, tidak menulis')
    args = ap.parse_args()

    if os.geteuid() != 0:
        print('Jalankan sebagai root.', file=sys.stderr)
        return 1

    sites = json.loads(Path(args.scan_json).read_text())
    todo = [s for s in sites if not s['installed'] and s['wp_content']]
    todo.sort(key=lambda s: s['size_bytes'])  # terkecil dulu

    if not todo:
        print('Semua site sudah terpasang backup. Tidak ada yang perlu dilakukan.')
        return 0

    print(f'Rencana install {len(todo)} site (terkecil dulu):\n')
    slot = 0
    plans = []
    for s in todo:
        hour = args.start_hour + (slot * args.interval_min) // 60
        minute = (slot * args.interval_min) % 60
        cron_expr = f'{minute} {hour} * * *'
        plans.append((s, cron_expr))
        print(f"  {s['domain']:38} {human(s['size_bytes']):>10}  cron {cron_expr}")
        slot += 1

    if args.dry_run:
        print('\n(dry-run: tidak ada perubahan)')
        return 0

    # pastikan script utama terpasang
    if not Path(SCRIPT_DEST).exists():
        src = Path(__file__).parent / 'wp-backup.py'
        if not src.exists():
            print(f'Script {src} tidak ditemukan. Jalankan dari repo backupyuk.', file=sys.stderr)
            return 1
        sh(['install', '-m', '700', str(src), SCRIPT_DEST])

    Path('/etc/awan-backup').mkdir(mode=0o700, exist_ok=True)

    print()
    ok = fail = 0
    for s, cron_expr in plans:
        domain = s['domain']
        env_file = f'/etc/awan-backup/{domain}.env'
        log_file = f'/var/log/wp-backup-{domain}.log'
        try:
            Path(env_file).write_text(
                f"# dibuat otomatis oleh install-batch.py\n"
                f"SITE_NAME={domain}\n"
                f"DEST_KEY={domain}\n"
                f"SRC_DIR={s['wp_content']}\n"
                f"DB_NAME={s.get('db_name', '')}\n"
                f"DB_USER={s.get('db_user', '')}\n"
                f"DB_PASSWORD={s.get('db_password', '')}\n"
                f"RCLONE_REMOTE={args.remote}:\n"
                f"DEST_PREFIX={args.prefix}\n"
                f"KEEP_DB_DAYS={args.keep_db_days}\n"
                f"FONNTE_TOKEN={args.fonnte_token}\n"
                f"FONNTE_TARGET={args.fonnte_target}\n"
            )
            Path(env_file).chmod(0o600)
            runner = write_runner(domain, env_file, log_file)
            write_cron(domain, cron_expr, runner)
            Path(log_file).touch()
            print(f'  ✓ {domain} -> cron {cron_expr}')
            ok += 1
        except Exception as exc:
            print(f'  ✗ {domain}: {exc}')
            fail += 1

    print(f'\nSelesai: {ok} terpasang, {fail} gagal.')
    print('Backup pertama tiap site akan jalan sesuai cron. Pantau log di /var/log/wp-backup-<site>.log')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
