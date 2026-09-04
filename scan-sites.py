#!/usr/bin/env python3
"""
Scan semua website di server (format panel Hestia/Vesta: /home/*/web/*/public_html)
dan petakan ukuran + database untuk perencanaan backup.

Pemakaian:
    sudo python3 scan-sites.py                 # tampilkan peta
    sudo python3 scan-sites.py --json out.json # simpan hasil ke JSON
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOME_DIRS = [Path('/home')]


def find_sites() -> list[dict]:
    """Temukan semua public_html di server.

    Mendukung dua struktur:
    - Hestia/Vesta : /home/<user>/web/<domain>/public_html
    - CyberPanel   : /home/<hash>/<Domain>/public_html
    """
    sites: list[dict] = []
    seen = set()
    for home in HOME_DIRS:
        if not home.is_dir():
            continue
        for public in sorted(home.glob('*/*/public_html')):   # Hestia & CyberPanel
            if not public.is_dir() or str(public) in seen:
                continue
            seen.add(str(public))
            domain = public.parent.name
            if domain in ('web', 'html', 'public', 'public_html',
                          'Akses-PHPMYADMIN', 'php-myadmin'):
                continue
            low = domain.lower()
            if any(x in low for x in ('staging', '-dev', 'test.', 'demo')):
                continue  # staging/dev/test/demo tidak dipetakan
            sites.append({
                'user': public.parent.parent.name,
                'domain': domain,
                'public_html': str(public),
                'wp_content': str(public / 'wp-content') if (public / 'wp-content').is_dir() else None,
                'wp_config': str(public / 'wp-config.php') if (public / 'wp-config.php').is_file() else None,
            })
    return sites


def dir_size_bytes(path: str) -> int:
    try:
        out = subprocess.run(
            ['du', '-sb', path],
            capture_output=True, text=True, check=False,
        ).stdout
        return int(out.split()[0]) if out.strip() else 0
    except Exception:
        return 0


def extract_db_creds(wp_config: str | None) -> tuple[str | None, str, str]:
    """Ambil DB_NAME, DB_USER, DB_PASSWORD dari wp-config.php."""
    if not wp_config:
        return None, '', ''
    try:
        t = Path(wp_config).read_text(errors='replace')

        def g(key):
            m = re.search(r"define\(\s*['\"]" + key + r"['\"]\s*,\s*['\"](.*?)['\"]\s*\)", t)
            return m.group(1) if m else ''
        return g('DB_NAME') or None, g('DB_USER'), g('DB_PASSWORD')
    except Exception:
        return None, '', ''


def installed_sites() -> set[str]:
    """Kunci env yang sudah dipasang installer (di /etc/awan-backup/)."""
    env_dir = Path('/etc/awan-backup')
    if not env_dir.is_dir():
        return set()
    return {f.stem for f in env_dir.glob('*.env')}


def human(nbytes: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if nbytes < 1024:
            return f'{nbytes:.0f} {unit}' if unit in ('B', 'KB') else f'{nbytes:.1f} {unit}'
        nbytes /= 1024
    return f'{nbytes:.1f} TB'


def main() -> int:
    ap = argparse.ArgumentParser(description='Scan semua website di server')
    ap.add_argument('--json', dest='json_out', help='simpan hasil ke file JSON')
    args = ap.parse_args()

    if os.geteuid() != 0:
        print('Jalankan sebagai root: sudo python3 scan-sites.py', file=sys.stderr)
        return 1

    print('Memindai website di server...', flush=True)
    sites = find_sites()
    done_keys = installed_sites()

    rows = []
    for s in sites:
        # ukur wp-content kalau ada (itu yang dibackup), kalau tidak ukur public_html
        target = s['wp_content'] or s['public_html']
        size = dir_size_bytes(target)
        db_name, db_user, db_pass = extract_db_creds(s['wp_config'])
        key = s['domain']
        rows.append({
            **s,
            'size_bytes': size,
            'db_name': db_name,
            'db_user': db_user,
            'db_password': db_pass,
            'installed': key in done_keys,
        })

    rows.sort(key=lambda r: r['size_bytes'])

    print()
    print(f"{'#':>3}  {'Site':38}  {'Ukuran':>10}  {'DB':3}  {'Status'}")
    print('-' * 80)
    total = 0
    for i, r in enumerate(rows, 1):
        total += r['size_bytes']
        status = '✓ terpasang' if r['installed'] else 'belum'
        db = 'ya' if r['db_name'] else '-'
        size_str = human(r['size_bytes'])
        print(f"{i:>3}  {r['domain']:38}  {size_str:>10}  {db:3}  {status}")
    print('-' * 80)
    print(f"    Total: {len(rows)} site, {human(total)}")
    n_installed = sum(1 for r in rows if r['installed'])
    print(f"    Terpasang backup: {n_installed}/{len(rows)}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=2))
        print(f"\nHasil disimpan: {args.json_out}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
