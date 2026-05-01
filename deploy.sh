#!/bin/bash
# WHTC Web Deploy — runs from your Mac
# Syncs source to droplet, builds on remote
#
# First deploy: also copy music files and run migrate.py
# Usage: ./deploy.sh [--init]

set -e

SERVER="root@134.199.212.172"
REMOTE_DIR="~/whtc_web"

INIT=false
if [ "$1" = "--init" ]; then
    INIT=true
fi

echo "=== 1/3  Syncing source to droplet ==="
ssh $SERVER "mkdir -p $REMOTE_DIR"
rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '.env' \
    --exclude 'data/' \
    --exclude 'music/' \
    . $SERVER:$REMOTE_DIR/

if [ "$INIT" = true ]; then
    echo "=== INIT: Syncing music files ==="
    echo "  (This may take a while for the first sync)"
    rsync -avz --progress ../BED_Music/WHTC_BED/ $SERVER:$REMOTE_DIR/music_staging/

    echo "=== INIT: Running migration ==="
    ssh $SERVER "cd $REMOTE_DIR && python3 migrate.py"

    echo ""
    echo "NOTE: After first deploy, copy music_staging into the Docker volume:"
    echo "  ssh $SERVER"
    echo "  docker cp $REMOTE_DIR/music_staging/. \$(docker compose -f $REMOTE_DIR/docker-compose.yml ps -q whtc):/app/music/"
    echo "  # Or mount music_staging as the volume path"
fi

echo "=== 2/3  Building and starting containers ==="
ssh $SERVER "cd $REMOTE_DIR && docker compose up -d --build --force-recreate"

echo ""
echo "=== 3/3  Health check ==="
sleep 5
ssh $SERVER "cd $REMOTE_DIR && docker compose ps --format 'table {{.Name}}\t{{.Status}}'"

echo ""
echo "=== Deploy complete ==="
echo "https://thewitchinghourtone.club"
echo ""
echo "Don't forget:"
echo "  1. Point DNS for thewitchinghourtone.club to 134.199.212.172"
echo "  2. Set up SSL certs (certbot)"
echo "  3. Add nginx.conf to the droplet's nginx config"
echo "  4. Set WHTC_ADMIN_PASS_HASH in .env"
