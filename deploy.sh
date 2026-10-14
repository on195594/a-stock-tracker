#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
nginx_dir="${NGINX_DIR:-/home/lin/nginx}"
site="$nginx_dir/sites-enabled/stock.conf"
source_conf=docker/nginx-stock.conf
command -v docker >/dev/null
# Validate both choices before any Docker commands or deployment changes.
if (( $# )); then
    echo 'deploy.sh accepts no arguments; use RESEARCH_WEB_ENABLED and PUBLICATION_WEB_ENABLED.' >&2
    exit 1
fi
for flag in RESEARCH_WEB_ENABLED PUBLICATION_WEB_ENABLED; do
    if [[ -v "$flag" && "${!flag}" != 0 && "${!flag}" != 1 ]]; then
        echo "$flag must be exactly 0 (disable) or 1 (enable)." >&2
        exit 1
    fi
done
research_enabled="${RESEARCH_WEB_ENABLED-}"
publication_enabled="${PUBLICATION_WEB_ENABLED-}"
# Explicit opt-in only; config-file presence alone never enables either feature.
compose=(docker compose)
# Keep the ordinary interpolation environment when adding the bridge env file.
# Compose loads these in order; exported environment variables still take precedence.
if [[ -f .env ]]; then
    compose+=(--env-file .env)
fi
if [[ "$research_enabled" == 1 ]]; then
    if [[ ! -f .research-web.env || ! -f docker-compose.research.yml ]]; then
        echo 'Missing .research-web.env or docker-compose.research.yml bridge configuration.' >&2
        exit 1
    fi
    compose+=(--env-file .research-web.env)
fi
if [[ "$publication_enabled" == 1 && ! -f docker-compose.opportunities.yml ]]; then
    echo 'Missing docker-compose.opportunities.yml publication configuration.' >&2
    exit 1
fi
# Omission may never silently remove either feature from an existing (even stopped) web.
# Inspect only presence markers: never print the container environment or its token.
if [[ ! -v RESEARCH_WEB_ENABLED || ! -v PUBLICATION_WEB_ENABLED ]]; then
    existing=$(docker ps -a --filter name=^/a-stock-tracker-web$ --format '{{.ID}}')
    if [[ -n "$existing" ]]; then
        if [[ ! -v RESEARCH_WEB_ENABLED ]]; then
            bridge=$(docker inspect --format '{{range .Config.Env}}{{if or (eq (index (split . "=") 0) "RESEARCH_SERVICE_URL") (eq (index (split . "=") 0) "RESEARCH_SERVICE_SOCKET") (eq (index (split . "=") 0) "RESEARCH_SERVICE_TOKEN")}}bridge{{end}}{{end}}{{range .Mounts}}{{if eq .Destination "/research-bridge"}}bridge{{end}}{{end}}' "$existing")
            if [[ -n "$bridge" ]]; then
                echo 'Existing web has a research bridge. No changes made. Use RESEARCH_WEB_ENABLED=1 ./deploy.sh to keep it, or RESEARCH_WEB_ENABLED=0 ./deploy.sh to explicitly disable it.' >&2
                exit 1
            fi
        fi
        if [[ ! -v PUBLICATION_WEB_ENABLED ]]; then
            publication=$(docker inspect --format '{{range .Config.Env}}{{if eq (index (split . "=") 0) "OPPORTUNITY_ROOT"}}publication{{end}}{{end}}{{range .Mounts}}{{if eq .Destination "/app/opportunities"}}publication{{end}}{{end}}' "$existing")
            if [[ -n "$publication" ]]; then
                echo 'Existing web has published price references. No changes made. Use PUBLICATION_WEB_ENABLED=1 ./deploy.sh to keep them, or PUBLICATION_WEB_ENABLED=0 ./deploy.sh to explicitly disable them.' >&2
                exit 1
            fi
        fi
    fi
fi
research_enabled="${research_enabled:-0}"
publication_enabled="${publication_enabled:-0}"
compose+=(-f docker-compose.yml)
if [[ "$research_enabled" == 1 ]]; then
    compose+=(-f docker-compose.research.yml)
fi
if [[ "$publication_enabled" == 1 ]]; then
    compose+=(-f docker-compose.opportunities.yml)
fi

for file in .web.env docker-compose.yml "$source_conf" "$site"; do
    if [[ ! -f "$file" ]]; then
        echo "Missing required file: $file" >&2
        exit 1
    fi
done
command -v curl >/dev/null
command -v make >/dev/null
"${compose[@]}" config --quiet
# Validate Nginx after the new upstream exists. During cutover the stopped
# predecessor no longer resolves in Docker DNS, so its old config cannot pass -t.
# Retiring the old runtime is a separate, explicitly authorized cutover.
if [ -n "$(docker ps --filter name=^/a-stock-screen-web$ --format '{{.Names}}')" ]; then
    echo 'Legacy screen is still running; complete the approved backup/cutover before deploying.' >&2
    exit 1
fi
make check

# Build before changing the running site or its proxy configuration.
"${compose[@]}" build web

# Remove the retired worker container, never its host data or volumes.
"${compose[@]}" up -d --no-deps --force-recreate --remove-orphans web
ready=0
for _ in {1..10}; do
    if curl --fail --silent --max-time 3 -o /dev/null \
        -H 'Host: stock.keyi.win' http://127.0.0.1:8550/; then
        ready=1
        break
    fi
    sleep 2
done
if [[ "$ready" -ne 1 ]]; then
    echo 'Web health check failed; Nginx was not reloaded. Inspect the web container.' >&2
    exit 1
fi

if [[ "$research_enabled" == 1 ]]; then
    # Authenticate over the actual mounted socket, using in-container credentials.
    # GET /info only: no run submission, market request, model call or token output.
    if ! docker exec a-stock-tracker-web python -m a_stock_tracker.research_client; then
        echo 'Research bridge health check failed; Nginx was not reloaded. Check the host receiver and private bridge configuration. Web was already recreated; fix and redeploy with RESEARCH_WEB_ENABLED=1.' >&2
        exit 1
    fi
fi

if ! cmp -s "$source_conf" "$site"; then
    backup="$site.bak.$(date +%Y%m%d_%H%M%S).$$"
    cp -p "$site" "$backup"
    staging=$(mktemp "$site.tmp.XXXXXX")
    cp -p "$source_conf" "$staging"
    mv "$staging" "$site"
    if ! docker exec nginx nginx -t; then
        cp -p "$backup" "$site"
        docker exec nginx nginx -t
        echo "Nginx config rejected; restored $backup" >&2
        exit 1
    fi
    echo "Nginx config backup: $backup"
fi


# Reload resolves the recreated container's new Docker IP without restarting other sites.
docker exec nginx nginx -t
docker exec nginx nginx -s reload
# Direct origin uses a Cloudflare Origin CA certificate, hence -k for this localhost probe only.
ready=0
for _ in {1..5}; do
    if curl --fail --silent --max-time 5 -k -o /dev/null \
        --resolve stock.keyi.win:443:127.0.0.1 https://stock.keyi.win/; then
        ready=1
        break
    fi
    sleep 2
done
if [[ "$ready" -ne 1 ]]; then
    echo 'HTTPS origin health check failed after reload; inspect Nginx and web logs.' >&2
    exit 1
fi
if [[ "$(docker inspect --format '{{.State.Running}} {{.RestartCount}}' a-stock-tracker-web)" != 'true 0' ]]; then
    echo 'Web failed to remain running after proxy reload.' >&2
    exit 1
fi
echo 'Deployed: read-only web running, retired worker removed, Nginx reloaded, HTTPS origin healthy.'
