#!/usr/bin/env python3
"""WHTC BED Music — Production Server
Serves the public player, admin at /breakdown, honeypot traps, SQLite backend.
"""

import hashlib
import http.cookies
import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import time
import urllib.parse
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = int(os.environ.get('WHTC_PORT', 8080))
ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, 'static')
MUSIC_DIR = os.path.join(ROOT, 'music')  # audio files live here, outside static
DB_PATH = os.path.join(ROOT, 'data', 'whtc.db')
HONEYPOT_LOG = os.path.join(ROOT, 'data', 'honeypot.log')

ADMIN_USER = os.environ.get('WHTC_ADMIN_USER', 'miles')
ADMIN_PASS_HASH = os.environ.get('WHTC_ADMIN_PASS_HASH', '')  # sha256 hex

# Sessions: token -> expiry timestamp
sessions = {}
SESSION_TTL = 86400  # 24 hours

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger('whtc')

# --- Database ---
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn

def init_db():
    conn = get_db()
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
    conn.commit()
    conn.close()

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# --- Auth helpers ---
def create_session():
    token = secrets.token_hex(32)
    sessions[token] = time.time() + SESSION_TTL
    return token

def check_session(cookie_header):
    if not cookie_header:
        return False
    c = http.cookies.SimpleCookie()
    try:
        c.load(cookie_header)
    except Exception:
        return False
    morsel = c.get('whtc_session')
    if not morsel:
        return False
    token = morsel.value
    expiry = sessions.get(token)
    if not expiry or time.time() > expiry:
        sessions.pop(token, None)
        return False
    return True

# --- MIME ---
MIME_TYPES = {
    '.html': 'text/html',
    '.css': 'text/css',
    '.js': 'application/javascript',
    '.json': 'application/json',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.ico': 'image/x-icon',
    '.svg': 'image/svg+xml',
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.ogg': 'audio/ogg',
    '.woff2': 'font/woff2',
    '.woff': 'font/woff',
}

# --- Honeypot trap pages ---
HONEYPOT_PATHS = {
    '/wp-admin', '/wp-login.php', '/wp-login', '/admin', '/login',
    '/administrator', '/admin.php', '/wp-admin/', '/admin/',
    '/user/login', '/signin', '/dashboard',
}

FAKE_WP_LOGIN = '''<!DOCTYPE html>
<html><head><title>Log In &lsaquo; Blog &#8212; WordPress</title>
<style>
body { background: #f1f1f1; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.login { width: 320px; margin: 8% auto; }
.login h1 { text-align: center; margin-bottom: 24px; }
.login h1 a { background: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA0MDAgNDAwIj48L3N2Zz4=) no-repeat center; width: 84px; height: 84px; display: block; margin: 0 auto; }
form { background: #fff; border: 1px solid #c3c4c7; padding: 26px 24px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,.04); }
label { display: block; margin-bottom: 3px; font-size: 14px; color: #1e1e1e; }
input[type=text], input[type=password] { width: 100%; padding: 6px 8px; margin-bottom: 16px; border: 1px solid #8c8f94; border-radius: 4px; font-size: 24px; box-sizing: border-box; }
.submit { margin-top: 16px; }
.submit input { background: #2271b1; border: none; color: #fff; padding: 8px 20px; border-radius: 4px; font-size: 13px; cursor: pointer; }
.login-error { background: #d63638; color: #fff; padding: 12px; border-radius: 4px; margin-bottom: 16px; font-size: 13px; }
</style></head>
<body><div class="login">
<h1><a href="#">&nbsp;</a></h1>
{error}
<form method="post">
<label>Username or Email Address</label>
<input type="text" name="log" value="">
<label>Password</label>
<input type="password" name="pwd" value="">
<div class="submit"><input type="submit" value="Log In"></div>
</form></div></body></html>'''

# --- Handler ---
class WHTCHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip('/')

        # Honeypot traps
        if path in HONEYPOT_PATHS or path + '/' in HONEYPOT_PATHS:
            self.serve_honeypot_page(path)
            return

        # Public player
        if path in ('', '/'):
            self.serve_file('player.html', 'text/html')
            return

        # About page
        if path == '/about':
            self.serve_file('about.html', 'text/html')
            return

        # Admin login + page
        if path == '/breakdown':
            if self.is_authed():
                self.serve_file('admin.html', 'text/html')
            else:
                self.serve_file('login.html', 'text/html')
            return

        # API: public tracks (active only, no shazam/admin fields)
        if path == '/api/tracks':
            conn = get_db()
            rows = conn.execute(
                'SELECT id, title, src, dur FROM tracks WHERE active = 1 ORDER BY title'
            ).fetchall()
            conn.close()
            self.ok_json([dict(r) for r in rows])
            return

        # API: admin tracks (all, with all fields)
        if path == '/api/admin/tracks':
            if not self.is_authed():
                self.error_json(401, 'Not authenticated')
                return
            conn = get_db()
            rows = conn.execute(
                'SELECT id, title, src, dur, active, shazam, added FROM tracks ORDER BY id'
            ).fetchall()
            conn.close()
            self.ok_json([dict(r) for r in rows])
            return

        # API: check auth
        if path == '/api/auth/check':
            self.ok_json({'authed': self.is_authed()})
            return

        # Music files (served from music dir, not static)
        if path.startswith('/music/'):
            self.serve_music(path[7:])  # strip /music/
            return

        # Static files
        if path.startswith('/static/'):
            rel = path[8:]
            safe = os.path.normpath(rel)
            if safe.startswith('..'):
                self.send_error(403)
                return
            fpath = os.path.join(STATIC, safe)
            if os.path.isfile(fpath):
                ext = os.path.splitext(fpath)[1].lower()
                self.serve_file_path(fpath, MIME_TYPES.get(ext, 'application/octet-stream'))
            else:
                self.send_error(404)
            return

        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip('/')

        # Honeypot POST
        if path in HONEYPOT_PATHS or path + '/' in HONEYPOT_PATHS:
            self.handle_honeypot_post(path)
            return

        # Login
        if path == '/api/auth/login':
            self.handle_login()
            return

        # Logout
        if path == '/api/auth/logout':
            self.handle_logout()
            return

        # Admin: save tracks
        if path == '/api/admin/tracks':
            if not self.is_authed():
                self.error_json(401, 'Not authenticated')
                return
            self.save_tracks()
            return

        # Admin: upload file
        if path == '/api/admin/upload':
            if not self.is_authed():
                self.error_json(401, 'Not authenticated')
                return
            self.upload_file()
            return

        # Admin: delete tracks
        if path == '/api/admin/delete-tracks':
            if not self.is_authed():
                self.error_json(401, 'Not authenticated')
                return
            self.delete_tracks()
            return

        self.send_error(404)

    # --- Auth ---
    def is_authed(self):
        return check_session(self.headers.get('Cookie'))

    def handle_login(self):
        body = self.read_body()
        try:
            data = json.loads(body)
        except Exception:
            self.send_error(400)
            return

        # Honeypot hidden field check
        if data.get('name', '') != '':
            self.log_honeypot('/breakdown (hidden field)', body.decode('utf-8', errors='replace'))
            # Fake success — waste their time
            self.ok_json({'ok': True, 'message': 'Logged in'})
            return

        username = data.get('username', '')
        password = data.get('password', '')

        if username == ADMIN_USER and hash_password(password) == ADMIN_PASS_HASH:
            token = create_session()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie',
                f'whtc_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True}).encode())
        else:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': False, 'error': 'Invalid credentials'}).encode())

    def handle_logout(self):
        cookie_header = self.headers.get('Cookie')
        if cookie_header:
            c = http.cookies.SimpleCookie()
            try:
                c.load(cookie_header)
                morsel = c.get('whtc_session')
                if morsel:
                    sessions.pop(morsel.value, None)
            except Exception:
                pass
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Set-Cookie', 'whtc_session=; Path=/; HttpOnly; Max-Age=0')
        self.end_headers()
        self.wfile.write(json.dumps({'ok': True}).encode())

    # --- Track CRUD ---
    def save_tracks(self):
        body = self.read_body()
        try:
            data = json.loads(body)
        except Exception:
            self.send_error(400)
            return

        conn = get_db()
        for t in data:
            if 'id' in t and t['id']:
                conn.execute(
                    'UPDATE tracks SET title=?, active=?, shazam=?, added=?, dur=? WHERE id=?',
                    (t['title'], int(t.get('active', 1)), int(t.get('shazam', 0)),
                     t.get('added', ''), t.get('dur', 0), t['id'])
                )
            else:
                conn.execute(
                    'INSERT INTO tracks (title, src, dur, active, shazam, added) VALUES (?, ?, ?, ?, ?, ?)',
                    (t['title'], t['src'], t.get('dur', 0), int(t.get('active', 1)),
                     int(t.get('shazam', 0)), t.get('added', ''))
                )
        conn.commit()
        conn.close()
        self.ok_json({'ok': True})

    def delete_tracks(self):
        body = self.read_body()
        try:
            data = json.loads(body)
            ids = data.get('ids', [])
        except Exception:
            self.send_error(400)
            return

        if not ids:
            self.send_error(400)
            return

        conn = get_db()
        placeholders = ','.join('?' * len(ids))
        conn.execute(f'DELETE FROM tracks WHERE id IN ({placeholders})', ids)
        conn.commit()
        conn.close()
        self.ok_json({'ok': True, 'deleted': len(ids)})

    def upload_file(self):
        content_type = self.headers.get('Content-Type', '')
        body = self.read_body()

        boundary = re.search(r'boundary=(.+)', content_type)
        if not boundary:
            self.send_error(400, 'No boundary')
            return

        boundary_bytes = ('--' + boundary.group(1)).encode()
        parts = body.split(boundary_bytes)

        artist_folder = None
        file_data = None
        filename = None

        for part in parts:
            if b'Content-Disposition' not in part:
                continue
            header_end = part.find(b'\r\n\r\n')
            if header_end < 0:
                continue
            header = part[:header_end].decode('utf-8', errors='replace')
            data = part[header_end + 4:]
            if data.endswith(b'\r\n'):
                data = data[:-2]

            name_match = re.search(r'name="([^"]+)"', header)
            if not name_match:
                continue
            name = name_match.group(1)

            if name == 'artist_folder':
                artist_folder = data.decode('utf-8').strip()
            elif name == 'file':
                fn_match = re.search(r'filename="([^"]+)"', header)
                if fn_match:
                    filename = fn_match.group(1)
                    file_data = data

        if not artist_folder or not filename or file_data is None:
            self.send_error(400, 'Need artist_folder and file')
            return

        # Sanitize folder name
        safe_folder = re.sub(r'[^a-z0-9_]', '_', artist_folder.lower().strip())
        dest_dir = os.path.join(MUSIC_DIR, safe_folder)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, filename)
        with open(dest_path, 'wb') as f:
            f.write(file_data)

        src_path = f'music/{safe_folder}/{filename}'
        self.ok_json({'ok': True, 'path': src_path})

    # --- Honeypot ---
    def serve_honeypot_page(self, path):
        self.log_honeypot(path, 'GET')
        html = FAKE_WP_LOGIN.replace('{error}', '')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def handle_honeypot_post(self, path):
        body = self.read_body()
        self.log_honeypot(path, body.decode('utf-8', errors='replace'))
        html = FAKE_WP_LOGIN.replace('{error}',
            '<div class="login-error">Invalid username or password. Please try again.</div>')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())

    def log_honeypot(self, path, payload):
        ip = self.client_address[0]
        ua = self.headers.get('User-Agent', '')
        logger.warning(f'HONEYPOT: ip={ip} path={path} ua={ua}')
        try:
            conn = get_db()
            conn.execute(
                'INSERT INTO honeypot_hits (ip, path, payload, user_agent) VALUES (?, ?, ?, ?)',
                (ip, path, payload[:2000], ua[:500])
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    # --- Helpers ---
    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length)

    def ok_json(self, data):
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        except BrokenPipeError:
            pass

    def error_json(self, code, message='Error'):
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': False, 'error': message}).encode())
        except BrokenPipeError:
            pass

    def serve_file(self, name, content_type):
        fpath = os.path.join(STATIC, name)
        self.serve_file_path(fpath, content_type)

    def serve_file_path(self, fpath, content_type):
        if not os.path.isfile(fpath):
            self.send_error(404)
            return
        with open(fpath, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        if content_type.startswith('audio/'):
            self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()
        self.wfile.write(data)

    def serve_music(self, rel_path):
        safe = os.path.normpath(rel_path)
        if safe.startswith('..'):
            self.send_error(403)
            return
        fpath = os.path.join(MUSIC_DIR, safe)
        if not os.path.isfile(fpath):
            self.send_error(404)
            return
        ext = os.path.splitext(fpath)[1].lower()
        self.serve_file_path(fpath, MIME_TYPES.get(ext, 'application/octet-stream'))

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except BrokenPipeError:
            pass

    def log_message(self, format, *args):
        req = str(args[0]) if args else ''
        if '/api/' in req or 'HONEYPOT' in req:
            super().log_message(format, *args)


if __name__ == '__main__':
    init_db()
    print(f'WHTC server running at http://localhost:{PORT}')
    print(f'  Player:  http://localhost:{PORT}/')
    print(f'  Admin:   http://localhost:{PORT}/breakdown')
    print(f'  DB:      {DB_PATH}')
    server = HTTPServer(('', PORT), WHTCHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
