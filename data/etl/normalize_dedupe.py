#!/usr/bin/env python3
"""
Normalize and dedupe airports_staging -> airports_canonical
- Normalizes text (NFKC, lowercased name_official stored)
- Filters types (removes heliport, seaplane, closed/military-only)
- Deduplicates by priority: icao -> iata -> exact lat/lon -> nearest (<=0.5km + name similarity)
- Emits airports_canonical table with airport_id (rowid-based UUID-like)

Run: python3 data/etl/normalize_dedupe.py
"""
import sqlite3
import unicodedata
from pathlib import Path
import math
from difflib import SequenceMatcher
import fcntl
import sys
import time
import os

DB_PATH = Path(__file__).resolve().parents[1] / 'database' / 'airports.db'
THRESHOLD_KM = 0.5

KEEP_TYPES = set(['large_airport', 'medium_airport', 'small_airport'])


def normalize_text(s: str) -> str:
    if s is None:
        return ''
    return unicodedata.normalize('NFKC', s).strip()


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


if __name__ == '__main__':
    # Keep a single open connection for the whole run, apply PRAGMAs early,
    # and use a filesystem lock to avoid concurrent DB access.
    import csv
    db_exists = DB_PATH.exists()

    lock_path = DB_PATH.parent / '.normalize_dedupe.lock'
    lock_fh = open(str(lock_path), 'w')
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('normalize_dedupe: another instance is running; exiting.', flush=True)
        lock_fh.close()
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        # Tune pragmas for concurrent-friendly writes and longer waits
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA temp_store=MEMORY;')
        conn.execute('PRAGMA busy_timeout = 60000;')

        cur = conn.cursor()

        if not db_exists:
            print(f"Staging DB {DB_PATH} not found. Creating new DB from CSV.", flush=True)
            cur.execute('''
                CREATE TABLE airports_staging (
                    id INTEGER,
                    ident TEXT,
                    type TEXT,
                    name TEXT,
                    latitude_deg REAL,
                    longitude_deg REAL,
                    elevation_ft REAL,
                    continent TEXT,
                    iso_country TEXT,
                    iso_region TEXT,
                    municipality TEXT,
                    scheduled_service TEXT,
                    gps_code TEXT,
                    iata_code TEXT,
                    local_code TEXT
                )
            ''')
            conn.commit()

            csv_path = DB_PATH.parent / 'airports.csv'
            insert_sql = ('INSERT INTO airports_staging (id,ident,type,name,latitude_deg,longitude_deg,'
                          'elevation_ft,continent,iso_country,iso_region,municipality,scheduled_service,'
                          'gps_code,iata_code,local_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')

            batch = []
            batch_size = 5000
            with open(csv_path, newline='', encoding='utf-8') as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    lat = row.get('latitude_deg') or None
                    lon = row.get('longitude_deg') or None
                    elev = row.get('elevation_ft') or None
                    batch.append((row.get('id'), row.get('ident'), row.get('type'), row.get('name'),
                                  lat, lon, elev,
                                  row.get('continent'), row.get('iso_country'), row.get('iso_region'), row.get('municipality'),
                                  row.get('scheduled_service'), row.get('gps_code'), row.get('iata_code'), row.get('local_code')))
                    if len(batch) >= batch_size:
                        cur.executemany(insert_sql, batch)
                        conn.commit()
                        batch = []
                if batch:
                    cur.executemany(insert_sql, batch)
                    conn.commit()

        # Create canonical table (fresh)
        print('Creating canonical table...', flush=True)
        cur.execute('DROP TABLE IF EXISTS airports_canonical')
        cur.execute('''
            CREATE TABLE airports_canonical (
                airport_id INTEGER PRIMARY KEY AUTOINCREMENT,
                ident TEXT,
                iata_code TEXT,
                icao_code TEXT,
                type TEXT,
                name_official TEXT,
                name_norm TEXT,
                latitude REAL,
                longitude REAL,
                elevation_ft REAL,
                iso_country TEXT,
                iso_region TEXT,
                municipality TEXT,
                has_scheduled_service TEXT
            )
        ''')
        conn.commit()
        print('Starting canonical extraction...', flush=True)

        # Prepare read and write cursors (same connection)
        read_cur = conn.cursor()
        write_cur = conn.cursor()

        read_cur.execute('SELECT rowid, ident, type, name, latitude_deg, longitude_deg, elevation_ft, iso_country, iso_region, municipality, scheduled_service, iata_code, gps_code FROM airports_staging')

        # Dedup index structures
        by_icao = {}
        by_iata = {}
        coords_index = []  # list of (rowid, lat, lon, name)

        inserted = 0
        batch_commit_interval = 1000

        while True:
            rows = read_cur.fetchmany(1000)
            if not rows:
                break
            for r in rows:
                rowid, ident, type_, name, lat, lon, elev, country, region, muni, sched, iata, gps = r
                if type_ not in KEEP_TYPES:
                    continue
                name_norm = normalize_text(name)
                if name_norm == '':
                    continue
                # Dedupe by ICAO (robust check)
                if ident and (ident.startswith('K') or len(ident) == 4):
                    icao = ident
                else:
                    icao = None
                if icao and icao in by_icao:
                    continue
                if iata and iata in by_iata:
                    continue

                # Exact lat/lon match
                duplicate_found = False
                if lat is not None and lon is not None:
                    for existing in coords_index:
                        _, ex_lat, ex_lon, ex_name = existing
                        if ex_lat == lat and ex_lon == lon:
                            duplicate_found = True
                            break
                if duplicate_found:
                    continue

                # Nearest neighbor check
                if lat is not None and lon is not None:
                    for existing in coords_index:
                        ex_rowid, ex_lat, ex_lon, ex_name = existing
                        if ex_lat is None or ex_lon is None:
                            continue
                        dist_km = haversine_km(lat, lon, ex_lat, ex_lon)
                        if dist_km <= THRESHOLD_KM:
                            sim = name_similarity(name_norm, ex_name)
                            if sim > 0.8:
                                duplicate_found = True
                                break
                if duplicate_found:
                    continue

                # Insert canonical row
                write_cur.execute('INSERT INTO airports_canonical (ident, iata_code, icao_code, type, name_official, name_norm, latitude, longitude, elevation_ft, iso_country, iso_region, municipality, has_scheduled_service) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    ident, iata, icao, type_, name, name_norm, lat, lon, elev, country, region, muni, sched
                ))
                new_id = write_cur.lastrowid
                inserted += 1
                # update indexes
                if icao:
                    by_icao[icao] = new_id
                if iata:
                    by_iata[iata] = new_id
                coords_index.append((new_id, lat, lon, name_norm))

                if inserted % 5000 == 0:
                    print(f'Inserted {inserted} canonical rows so far...', flush=True)
                if inserted % batch_commit_interval == 0:
                    conn.commit()
                    time.sleep(0.01)

        conn.commit()
        total = write_cur.execute('SELECT COUNT(1) FROM airports_canonical').fetchone()[0]
        print(f'Inserted canonical rows: {inserted}; total canonical: {total}', flush=True)

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
