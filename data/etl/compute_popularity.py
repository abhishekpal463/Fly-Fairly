#!/usr/bin/env python3
"""
Compute `popularity_score` for `airports_canonical`.

Algorithm:
- If `airports_staging` contains a passenger-like column (annual_passengers, passengers, etc.), use `log1p(passengers)`.
- Otherwise use a heuristic combining airport `type`, `has_scheduled_service`, presence of `iata_code`, and city airport count as fallback.

Writes two columns on `airports_canonical`:
- `popularity_score` (REAL)
- `popularity_source` (TEXT)

Run: python3 data/etl/compute_popularity.py
"""
from pathlib import Path
import sqlite3
import math
import fcntl
import sys
import os
import time

DB_PATH = Path(__file__).resolve().parents[1] / 'staging' / 'airports.db'

CANDIDATE_PASSENGER_COLS = [
    'annual_passengers', 'passengers', 'passenger_count', 'passengers_per_year',
    'enplanements', 'annual_enplanements', 'passengers_annual', 'passengers_total'
]


def safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        try:
            return float(str(v).replace(',', ''))
        except Exception:
            return None


def main():
    if not DB_PATH.exists():
        print('staging DB not found:', DB_PATH)
        sys.exit(1)

    lock_path = DB_PATH.parent / '.compute_popularity.lock'
    lock_fh = open(str(lock_path), 'w')
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('compute_popularity: another instance is running; exiting.', flush=True)
        lock_fh.close()
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    try:
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA temp_store=MEMORY;')
        conn.execute('PRAGMA busy_timeout = 60000;')
        cur = conn.cursor()

        # Ensure columns exist
        cur.execute("PRAGMA table_info('airports_canonical')")
        cols = [r[1] for r in cur.fetchall()]
        if 'popularity_score' not in cols:
            cur.execute('ALTER TABLE airports_canonical ADD COLUMN popularity_score REAL')
        if 'popularity_source' not in cols:
            cur.execute('ALTER TABLE airports_canonical ADD COLUMN popularity_source TEXT')
        conn.commit()

        # Detect passenger-like column in staging
        cur.execute("PRAGMA table_info('airports_staging')")
        staging_cols = [r[1] for r in cur.fetchall()]
        passenger_col = None
        for c in CANDIDATE_PASSENGER_COLS:
            if c in staging_cols:
                passenger_col = c
                break
        if passenger_col:
            print('Using passenger column from staging:', passenger_col, flush=True)
        else:
            print('No passenger column found in staging; using heuristics', flush=True)

        # Build staging passenger lookup if column available
        staging_by_ident = {}
        staging_by_iata = {}
        staging_by_gps = {}
        if passenger_col:
            sel = f"SELECT ident, iata_code, gps_code, {passenger_col} FROM airports_staging"
            for ident, iata, gps, p in conn.execute(sel):
                v = safe_float(p)
                if v is None:
                    continue
                if ident:
                    staging_by_ident[ident] = v
                if iata:
                    staging_by_iata[iata] = v
                if gps:
                    staging_by_gps[gps] = v

        # Load city counts if available
        city_counts = {}
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='airports_cities'")
        if cur.fetchone():
            for cid, count in conn.execute('SELECT city_id, airport_count FROM airports_cities'):
                try:
                    city_counts[int(cid)] = int(count)
                except Exception:
                    city_counts[int(cid)] = 0

        # Iterate canonical rows and compute score
        rows = conn.execute('SELECT airport_id, ident, iata_code, icao_code, type, has_scheduled_service, city_id FROM airports_canonical').fetchall()
        print(f'Computing popularity for {len(rows)} canonical airports...', flush=True)

        updates = []
        used_passenger = 0
        used_heuristic = 0
        batch = []
        for r in rows:
            airport_id, ident, iata, icao, type_, sched, city_id = r

            passengers = None
            if passenger_col:
                if ident and ident in staging_by_ident:
                    passengers = staging_by_ident.get(ident)
                elif iata and iata in staging_by_iata:
                    passengers = staging_by_iata.get(iata)
                elif icao and icao in staging_by_gps:
                    passengers = staging_by_gps.get(icao)

            score = None
            source = None
            if passengers is not None:
                score = math.log1p(passengers)
                source = f'passengers:{passenger_col}'
                used_passenger += 1
            else:
                # heuristic
                base_by_type = {'large_airport': 10.0, 'medium_airport': 5.0, 'small_airport': 1.0}
                base = base_by_type.get((type_ or '').lower(), 1.0)
                sched_flag = 1 if (sched and str(sched).lower().startswith('y')) else 0
                has_iata = 1 if (iata and str(iata).strip()) else 0
                city_mult = 1.0
                if city_id and city_id in city_counts:
                    city_mult += math.log1p(city_counts[city_id]) / 10.0
                score = base * (1.0 + 0.5 * sched_flag + 0.3 * has_iata) * city_mult
                source = 'heuristic'
                used_heuristic += 1

            updates.append((float(score), source, airport_id))
            if len(updates) >= 1000:
                conn.executemany('UPDATE airports_canonical SET popularity_score = ?, popularity_source = ? WHERE airport_id = ?', updates)
                conn.commit()
                updates = []

        if updates:
            conn.executemany('UPDATE airports_canonical SET popularity_score = ?, popularity_source = ? WHERE airport_id = ?', updates)
            conn.commit()

        print(f'Popularity computation done. used_passenger={used_passenger}, used_heuristic={used_heuristic}', flush=True)

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
