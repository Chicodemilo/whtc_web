# WHTC Web — Droplet Deployment

## Server
- **Droplet:** 134.199.212.172 (DigitalOcean)
- **Domain:** thewitchinghourtone.club
- **SSH:** `ssh root@134.199.212.172`
- **Shared with:** Clubbie (clubbie.club) — same droplet, same nginx

## Architecture
```
Internet
  │
  ├── thewitchinghourtone.club ──► nginx (Clubbie's container, port 443)
  │                                  └──► whtc_web-whtc-1:8090
  │
  └── clubbie.club ──────────────► nginx (same container)
                                     └──► clubbie api/frontend
```

- WHTC runs as its own Docker container (`whtc_web-whtc-1`) on port 8090
- It joins Clubbie's Docker network (`clubbie_2026_app-network`) automatically via docker-compose
- Clubbie's nginx routes by domain: `clubbie.club` → Clubbie, `thewitchinghourtone.club` → WHTC
- Combined nginx config lives at `~/Clubbie_2026/nginx/nginx.prod.conf` on the droplet AND in the local Clubbie repo at `~/Dev/Clubbie_2026/nginx/nginx.prod.conf`

## Files on droplet
```
~/whtc_web/
  server.py          — Python server (stdlib only, no pip deps)
  docker-compose.yml — container config, joins Clubbie's network
  Dockerfile
  .env               — WHTC_ADMIN_USER, WHTC_ADMIN_PASS_HASH
  data/whtc.db       — SQLite database (tracks + honeypot logs)
  music/             — audio files (Docker volume: whtc_music)
  static/            — HTML/CSS/JS (baked into image)
    player.html      — public player at /
    about.html       — about page at /about
    login.html       — login at /breakdown
    admin.html       — admin at /breakdown (after login)
    common.css       — shared styles
    favicon.ico
    whtc_rect.jpeg   — logo
```

## SSL
- Certs from Let's Encrypt via certbot (standalone mode)
- Stored at `/etc/letsencrypt/live/thewitchinghourtone.club/`
- Copied to `~/Clubbie_2026/nginx/ssl/whtc/` so nginx container can read them
- Auto-renew is set up by certbot, BUT you need to re-copy certs after renewal:
  ```
  cp /etc/letsencrypt/live/thewitchinghourtone.club/fullchain.pem ~/Clubbie_2026/nginx/ssl/whtc/
  cp /etc/letsencrypt/live/thewitchinghourtone.club/privkey.pem ~/Clubbie_2026/nginx/ssl/whtc/
  docker exec clubbie_2026-nginx-1 nginx -s reload
  ```

## DNS
- A records at Namecheap: `@` and `www` → 134.199.212.172

## Auth
- Admin URL: /breakdown
- Username: chico
- Password hash in .env (sha256)
- To change password:
  ```
  python3 -c "import hashlib; print(hashlib.sha256(b'NEW_PASSWORD').hexdigest())"
  ```
  Update WHTC_ADMIN_PASS_HASH in ~/whtc_web/.env, then restart container

## Honeypot
- Fake WordPress login at /wp-admin, /admin, /login, etc.
- Hidden "name" field on real login form — bots fill it, humans don't
- All hits logged to `honeypot_hits` table in whtc.db
- View hits: `docker exec whtc_web-whtc-1 python3 -c "import sqlite3; conn=sqlite3.connect('/app/data/whtc.db'); [print(dict(r)) for r in conn.execute('SELECT * FROM honeypot_hits ORDER BY id DESC LIMIT 10').fetchall()]"`

## Deploy updates
1. Edit files locally in `~/Documents/The_WHTC/whtc_web/`
2. Copy to droplet:
   ```
   scp server.py root@134.199.212.172:~/whtc_web/
   scp static/* root@134.199.212.172:~/whtc_web/static/
   ```
3. Rebuild and restart:
   ```
   ssh root@134.199.212.172 "cd ~/whtc_web && docker compose up -d --build"
   ```
4. Restore DB into container (volumes reset on rebuild):
   ```
   ssh root@134.199.212.172 "docker cp ~/whtc_web/data/whtc.db whtc_web-whtc-1:/app/data/whtc.db"
   ```

## When Clubbie redeploys
- The combined nginx config is in Clubbie's repo (`nginx/nginx.prod.conf`) — it includes both domains
- WHTC auto-joins Clubbie's network via `external: true` in docker-compose.yml
- No manual steps needed — both sites survive a Clubbie deploy

## Music files
- Not in the container image — stored in Docker volume `whtc_music`
- To upload music, copy into the running container:
  ```
  docker cp /path/to/files/. whtc_web-whtc-1:/app/music/
  ```
- Or upload via admin page at /breakdown
