#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  sudo ./scripts/ssl_issue_and_install.sh -d panel.example.com [-e admin@example.com]
  sudo ./scripts/ssl_issue_and_install.sh -d "panel.example.com,api.example.com" [-e admin@example.com]

What it does:
  - Installs required packages (Debian/Ubuntu: certbot, dos2unix, iproute2)
  - (Optionally) stops docker compose nginx to free port 80
  - Issues/renews Let's Encrypt cert via certbot standalone (HTTP-01 on :80)
  - Copies cert to ./nginx/ssl/fullchain.pem and ./nginx/ssl/privkey.pem
  - Creates a renewal deploy-hook to keep ./nginx/ssl in sync after renewals

Args:
  -d, --domains   Comma-separated domains (first one becomes cert-name)
  -e, --email     Email for Let's Encrypt account (recommended). If omitted, uses unsafe registration without email.
  --no-stop-nginx Do not stop docker compose nginx automatically
EOF
}

DOMAINS_CSV=""
EMAIL=""
STOP_NGINX=1

while [ "${1:-}" != "" ]; do
  case "$1" in
    -d|--domains) DOMAINS_CSV="${2:-}"; shift 2 ;;
    -e|--email) EMAIL="${2:-}"; shift 2 ;;
    --no-stop-nginx) STOP_NGINX=0; shift 1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$DOMAINS_CSV" ]; then
  echo "Missing required -d/--domains" >&2
  usage
  exit 2
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$PROJECT_DIR/nginx/ssl"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo -E bash "$0" "$@"
  fi
  echo "This script must run as root (or via sudo)." >&2
  exit 1
fi

install_deps() {
  if command -v certbot >/dev/null 2>&1 && command -v dos2unix >/dev/null 2>&1 && command -v ss >/dev/null 2>&1; then
    return 0
  fi

  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y certbot dos2unix iproute2
    return 0
  fi

  if command -v dnf >/dev/null 2>&1; then
    dnf install -y certbot dos2unix iproute
    return 0
  fi

  if command -v yum >/dev/null 2>&1; then
    yum install -y certbot dos2unix iproute
    return 0
  fi

  echo "Unsupported OS/package manager: please install certbot + dos2unix + iproute2 (ss) manually." >&2
  exit 1
}

docker_nginx_stop_started=0
maybe_stop_docker_nginx() {
  [ "$STOP_NGINX" -eq 1 ] || return 0
  [ -f "$COMPOSE_FILE" ] || return 0
  command -v docker >/dev/null 2>&1 || return 0

  # If nginx is running, stop it to free :80
  set +e
  if docker compose -f "$COMPOSE_FILE" ps nginx 2>/dev/null | grep -qi 'running'; then
    docker compose -f "$COMPOSE_FILE" stop nginx >/dev/null 2>&1 || true
    docker_nginx_stop_started=1
  fi
  set -e
}

maybe_start_docker_nginx() {
  [ "$docker_nginx_stop_started" -eq 1 ] || return 0
  set +e
  docker compose -f "$COMPOSE_FILE" up -d nginx >/dev/null 2>&1 || true
  docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
  set -e
}

ensure_port_80_free() {
  if ss -ltnp 2>/dev/null | grep -qE ':[[:space:]]*80[[:space:]]'; then
    echo "Port 80 is busy. Stop the service using :80 and retry." >&2
    ss -ltnp 2>/dev/null | grep -E ':[[:space:]]*80[[:space:]]' || true
    exit 1
  fi
}

parse_domains() {
  # Trim spaces, split by comma, keep order
  echo "$DOMAINS_CSV" | tr -d ' ' | tr ',' '\n' | awk 'NF'
}

issue_cert() {
  local cert_name="$1"
  shift
  local domains=("$@")

  local certbot_args=(
    certbot
    certonly
    --standalone
    --preferred-challenges http
    --http-01-port 80
    --agree-tos
    --non-interactive
    --cert-name "$cert_name"
  )

  if [ -n "$EMAIL" ]; then
    certbot_args+=( -m "$EMAIL" )
  else
    certbot_args+=( --register-unsafely-without-email )
  fi

  for d in "${domains[@]}"; do
    certbot_args+=( -d "$d" )
  done

  "${certbot_args[@]}"
}

copy_to_project_ssl() {
  local cert_name="$1"

  install -d "$TARGET_DIR"

  # Remove placeholder file if present
  rm -f "$TARGET_DIR/в эту папку ssl.txt" 2>/dev/null || true

  install -m 0644 "/etc/letsencrypt/live/$cert_name/fullchain.pem" "$TARGET_DIR/fullchain.pem"
  install -m 0600 "/etc/letsencrypt/live/$cert_name/privkey.pem" "$TARGET_DIR/privkey.pem"
}

write_deploy_hook() {
  local cert_name="$1"
  local safe_name
  safe_name="$(printf '%s' "$cert_name" | tr -c 'A-Za-z0-9._-' '_' )"
  local hook="/etc/letsencrypt/renewal-hooks/deploy/remnawave-copy-${safe_name}.sh"

  cat > "$hook" <<EOF
#!/bin/sh
set -eu

CERT_NAME="$cert_name"
PROJECT_DIR="$PROJECT_DIR"
TARGET_DIR="\$PROJECT_DIR/nginx/ssl"

install -d "\$TARGET_DIR"
install -m 0644 "/etc/letsencrypt/live/\$CERT_NAME/fullchain.pem" "\$TARGET_DIR/fullchain.pem"
install -m 0600 "/etc/letsencrypt/live/\$CERT_NAME/privkey.pem" "\$TARGET_DIR/privkey.pem"

if command -v docker >/dev/null 2>&1 && [ -f "\$PROJECT_DIR/docker-compose.yml" ]; then
  docker compose -f "\$PROJECT_DIR/docker-compose.yml" exec -T nginx nginx -s reload 2>/dev/null || true
  docker compose -f "\$PROJECT_DIR/docker-compose.yml" restart nginx 2>/dev/null || true
fi
EOF

  # Protect from CRLF (common when edited on Windows)
  dos2unix "$hook" >/dev/null 2>&1 || true
  chmod +x "$hook"
}

main() {
  install_deps
  maybe_stop_docker_nginx
  ensure_port_80_free

  mapfile -t domains < <(parse_domains)
  if [ "${#domains[@]}" -lt 1 ]; then
    echo "No domains parsed from -d/--domains" >&2
    exit 2
  fi
  local cert_name="${domains[0]}"

  issue_cert "$cert_name" "${domains[@]}"
  copy_to_project_ssl "$cert_name"
  write_deploy_hook "$cert_name"

  maybe_start_docker_nginx

  echo "OK: cert issued for ${domains[*]}"
  echo "Copied to: $TARGET_DIR/fullchain.pem and $TARGET_DIR/privkey.pem"
}

main

