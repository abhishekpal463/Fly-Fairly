#!/usr/bin/env python3
"""
Detect MACs (multi-airport cities) from `airports_canonical` and emit
`airports_cities` into the staging DB. Also adds `city_id` column to
`airports_canonical` and updates rows to reference their city.

Run: python3 data/etl/detect_macs_emit_cities.py
"""
import sqlite3
import math
import json
from pathlib import Path
from collections import defaultdict, Counter
from difflib import SequenceMatcher
import unicodedata
import fcntl
import sys
import time
import os

DB_PATH = Path(__file__).resolve().parents[1] / 'database' / 'airports.db'
MAC_THRESHOLD_KM = 50.0


def normalize_text(s: str) -> str:
    if s is None:
        return ''
    return unicodedata.normalize('NFKC', s).strip()


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or '').lower(), (b or '').lower()).ratio()


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        p = self.parent
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def main():
    if not DB_PATH.exists():
        print('staging DB not found:', DB_PATH)
        sys.exit(1)

    lock_path = DB_PATH.parent / '.detect_macs.lock'
    lock_fh = open(str(lock_path), 'w')
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('detect_macs: another instance is running; exiting.', flush=True)
        lock_fh.close()
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA temp_store=MEMORY;')
        conn.execute('PRAGMA busy_timeout = 60000;')
        cur = conn.cursor()

        # Ensure city_id column on canonical
        cur.execute("PRAGMA table_info('airports_canonical')")
        cols = [r[1] for r in cur.fetchall()]
        if 'city_id' not in cols:
            print('Adding city_id column to airports_canonical', flush=True)
            cur.execute('ALTER TABLE airports_canonical ADD COLUMN city_id INTEGER')
            conn.commit()

        # Load canonical airports
        cur.execute('''
            SELECT airport_id, name_official, name_norm, latitude, longitude, municipality, iso_country, type, has_scheduled_service
            FROM airports_canonical
        ''')
        rows = cur.fetchall()

        entries = []
        for r in rows:
            airport_id, name_official, name_norm, lat, lon, muni, iso_country, type_, sched = r
            entries.append({
                'airport_id': airport_id,
                'name_official': name_official,
                'name_norm': name_norm,
                'lat': float(lat) if lat is not None else None,
                'lon': float(lon) if lon is not None else None,
                'municipality': normalize_text(muni) if muni else '',
                'iso_country': iso_country or '',
                'type': type_ or '',
                'scheduled': (sched or '').lower()
            })

        n = len(entries)
        print(f'Loaded {n} canonical airports', flush=True)

        # Grid bucketing to reduce comparisons
        delta_deg = MAC_THRESHOLD_KM / 111.0
        grid = defaultdict(list)
        muni_map = defaultdict(list)
        for i, e in enumerate(entries):
            muni_map[(e['iso_country'], e['municipality'])].append(i)
            if e['lat'] is not None and e['lon'] is not None:
                gx = int(math.floor(e['lat'] / delta_deg))
                gy = int(math.floor(e['lon'] / delta_deg))
                grid[(gx, gy)].append(i)

        uf = UnionFind(n)

        # Spatial + name-based unions
        for i, e in enumerate(entries):
            lat = e['lat']
            lon = e['lon']
            if lat is not None and lon is not None:
                gx = int(math.floor(lat / delta_deg))
                gy = int(math.floor(lon / delta_deg))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for j in grid.get((gx + dx, gy + dy), []):
                            if j <= i:
                                continue
                            ej = entries[j]
                            if ej['lat'] is None or ej['lon'] is None:
                                continue
                            d = haversine_km(lat, lon, ej['lat'], ej['lon'])
                            if d <= MAC_THRESHOLD_KM:
                                uf.union(i, j)
                                continue
                            # municipality similarity in same country
                            if e['municipality'] and ej['municipality'] and e['iso_country'] == ej['iso_country']:
                                if name_similarity(e['municipality'], ej['municipality']) > 0.8:
                                    uf.union(i, j)
            else:
                # no coords: try to attach to same-muni groups
                if e['municipality']:
                    for j in muni_map.get((e['iso_country'], e['municipality']), []):
                        if j == i:
                            continue
                        uf.union(i, j)

        # Build groups
        groups = defaultdict(list)
        for i in range(n):
            groups[uf.find(i)].append(i)

        print(f'Found {len(groups)} raw groups', flush=True)

        # Create cities table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS airports_cities (
                city_id INTEGER PRIMARY KEY AUTOINCREMENT,
                mac_code TEXT,
                name TEXT,
                iso_country TEXT,
                latitude REAL,
                longitude REAL,
                airport_ids TEXT,
                primary_airport_id INTEGER,
                airport_count INTEGER
            )
        ''')
        conn.commit()

        # Insert groups as cities and update canonical rows
        mac_counter = 1
        updates = []
        inserted = 0
        for root, idxs in groups.items():
            airport_ids = [entries[i]['airport_id'] for i in idxs]
            # centroid
            lats = [entries[i]['lat'] for i in idxs if entries[i]['lat'] is not None]
            lons = [entries[i]['lon'] for i in idxs if entries[i]['lon'] is not None]
            lat_cent = sum(lats) / len(lats) if lats else None
            lon_cent = sum(lons) / len(lons) if lons else None

            # choose name and country
            muni_counter = Counter([entries[i]['municipality'] for i in idxs if entries[i]['municipality']])
            name = muni_counter.most_common(1)[0][0] if muni_counter else (entries[idxs[0]]['name_official'] or entries[idxs[0]]['name_norm'] or 'unknown')
            iso_country = Counter([entries[i]['iso_country'] for i in idxs]).most_common(1)[0][0]

            # choose primary airport: prefer large>medium>small, scheduled service, then lowest airport_id
            def score(i):
                t = entries[i]['type']
                rank = 0
                if t == 'large_airport':
                    rank = 3
                elif t == 'medium_airport':
                    rank = 2
                elif t == 'small_airport':
                    rank = 1
                sched = 1 if (entries[i]['scheduled'] and entries[i]['scheduled'].startswith('y')) else 0
                return (rank, sched, -entries[i]['airport_id'])

            primary_idx = max(idxs, key=score)
            primary_airport_id = entries[primary_idx]['airport_id']

            mac_code = f'MAC{mac_counter:05d}'
            mac_counter += 1

            cur.execute('INSERT INTO airports_cities (mac_code, name, iso_country, latitude, longitude, airport_ids, primary_airport_id, airport_count) VALUES (?,?,?,?,?,?,?,?)', (
                mac_code, name, iso_country, lat_cent, lon_cent, json.dumps(airport_ids), primary_airport_id, len(airport_ids)
            ))
            city_id = cur.lastrowid

            for aid in airport_ids:
                updates.append((city_id, aid))

            inserted += 1
            if inserted % 500 == 0:
                conn.commit()
                print(f'Inserted {inserted} cities so far...', flush=True)

        # Apply updates to canonical table
        for i in range(0, len(updates), 1000):
            batch = updates[i:i+1000]
            cur.executemany('UPDATE airports_canonical SET city_id = ? WHERE airport_id = ?', batch)
            conn.commit()

        total_cities = cur.execute('SELECT COUNT(1) FROM airports_cities').fetchone()[0]
        print(f'Inserted total cities: {total_cities}', flush=True)

    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_UN)
            except Exception:
                pass
            lock_fh.close()
            try:
                os.unlink(str(lock_path))
            except Exception:
                pass
        except Exception:
            pass


if __name__ == '__main__':
    main()
