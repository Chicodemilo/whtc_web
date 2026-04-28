#!/usr/bin/env python3
"""Migrate tracks.js → SQLite database."""

import os
import re
import sqlite3

TRACKS_JS = os.path.expanduser('~/Documents/The_WHTC/BED_Music/tracks.js')
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'whtc.db')

def parse_tracks_js(path):
    with open(path, 'r') as f:
        content = f.read()

    tracks = []
    # Match each { ... } object
    for m in re.finditer(r'\{([^}]+)\}', content):
        obj = m.group(1)
        track = {}

        # Extract string fields
        for key in ('title', 'src', 'added'):
            match = re.search(rf'{key}:\s*"([^"]*)"', obj)
            if match:
                track[key] = match.group(1).replace('\\"', '"').replace('\\\\', '\\')

        # Extract numeric fields
        for key in ('dur',):
            match = re.search(rf'{key}:\s*(\d+)', obj)
            if match:
                track[key] = int(match.group(1))

        # Extract boolean fields
        for key in ('active', 'shazam'):
            match = re.search(rf'{key}:\s*(true|false)', obj)
            if match:
                track[key] = match.group(1) == 'true'

        if 'title' in track and 'src' in track:
            tracks.append(track)

    return tracks

def migrate():
    tracks = parse_tracks_js(TRACKS_JS)
    print(f'Parsed {len(tracks)} tracks from tracks.js')

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')

    conn.execute('''CREATE TABLE IF NOT EXISTS tracks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        src TEXT NOT NULL UNIQUE,
        dur INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        shazam INTEGER DEFAULT 0,
        added TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    )''')

    conn.execute('''CREATE TABLE IF NOT EXISTS honeypot_hits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ip TEXT,
        path TEXT,
        payload TEXT,
        user_agent TEXT,
        timestamp TEXT DEFAULT (datetime('now'))
    )''')

    inserted = 0
    skipped = 0
    for t in tracks:
        # Remap src paths: WHTC_BED/artist/file → music/artist/file
        src = t.get('src', '')
        if src.startswith('WHTC_BED/'):
            src = 'music/' + src[9:]

        try:
            conn.execute(
                'INSERT INTO tracks (title, src, dur, active, shazam, added) VALUES (?, ?, ?, ?, ?, ?)',
                (t['title'], src, t.get('dur', 0), int(t.get('active', True)),
                 int(t.get('shazam', False)), t.get('added', ''))
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1

    conn.commit()
    conn.close()
    print(f'Inserted {inserted}, skipped {skipped} (duplicate src)')
    print(f'Database: {DB_PATH}')

if __name__ == '__main__':
    migrate()
