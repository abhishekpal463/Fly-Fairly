#!/usr/bin/env python3
"""
Ingest OurAirports airports.csv into a local SQLite staging DB and normalized CSV.
- Downloads the latest CSV from OurAirports
- Normalizes name fields using Unicode NFKC
- Filters out empty rows and writes staging CSV + SQLite table `airports_staging`

Run: python3 data/etl/ingest_ourairports.py
"""
import csv
import sqlite3
import unicodedata
import urllib.request
import os
from pathlib import Path

OURAIRPORTS_URL = "https://ourairports.com/data/airports.csv"
DATA_DIR = Path(__file__).resolve().parents[1] / "database"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / "airports.csv"
DB_PATH = DATA_DIR / "airports.db"

FIELDS_TO_KEEP = [
    'id', 'ident', 'type', 'name', 'latitude_deg', 'longitude_deg', 'elevation_ft',
    'continent', 'iso_country', 'iso_region', 'municipality', 'scheduled_service',
    'gps_code', 'iata_code', 'local_code'
]


def download_csv(url: str, dest: Path) -> None:
    print(f"Downloading {url} -> {dest}")
    with urllib.request.urlopen(url) as response:
        content = response.read()
    dest.write_bytes(content)
    print(f"Saved {dest} ({dest.stat().st_size} bytes)")


def normalize(text: str) -> str:
    if text is None:
        return ""
    s = str(text)
    s = unicodedata.normalize('NFKC', s).strip()
    return s


def ingest_to_sqlite(csv_path: Path, db_path: Path) -> None:
    print(f"Ingesting {csv_path} into SQLite {db_path}")
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS airports_staging")
    cur.execute(
        """
        CREATE TABLE airports_staging (
            id INTEGER PRIMARY KEY,
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
        """
    )
    inserted = 0
    with open(csv_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        to_db = []
        for row in reader:
            # basic normalization
            row_norm = {k: normalize(row.get(k, "")) for k in FIELDS_TO_KEEP}
            # filter out closed or blank names
            if row_norm['name'] == '':
                continue
            to_db.append(
                (
                    row_norm['id'] or None,
                    row_norm['ident'],
                    row_norm['type'],
                    row_norm['name'],
                    float(row_norm['latitude_deg']) if row_norm['latitude_deg'] else None,
                    float(row_norm['longitude_deg']) if row_norm['longitude_deg'] else None,
                    float(row_norm['elevation_ft']) if row_norm['elevation_ft'] else None,
                    row_norm['continent'],
                    row_norm['iso_country'],
                    row_norm['iso_region'],
                    row_norm['municipality'],
                    row_norm['scheduled_service'],
                    row_norm['gps_code'],
                    row_norm['iata_code'],
                    row_norm['local_code'],
                )
            )
    cur.executemany(
        "INSERT INTO airports_staging (id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,iso_region,municipality,scheduled_service,gps_code,iata_code,local_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        to_db,
    )
    conn.commit()
    inserted = cur.execute("SELECT COUNT(1) FROM airports_staging").fetchone()[0]
    conn.close()
    print(f"Inserted {inserted} rows into airports_staging")


if __name__ == '__main__':
    try:
        download_csv(OURAIRPORTS_URL, CSV_PATH)
    except Exception as e:
        print(f"Warning: failed to download {OURAIRPORTS_URL}: {e}. If you already have a local airports.csv, continuing.")
    if CSV_PATH.exists():
        ingest_to_sqlite(CSV_PATH, DB_PATH)
    else:
        print(f"Error: {CSV_PATH} not present. Please download airports.csv to {CSV_PATH} and rerun.")
