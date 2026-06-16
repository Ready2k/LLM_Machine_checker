#!/usr/bin/env bash
set -euo pipefail

# Generate a self-signed TLS certificate for the gateway (LAN HTTPS).
#
# Output:  certs/server.crt  +  certs/server.key
# Covers:  localhost, 127.0.0.1, this machine's hostname, <hostname>.local,
#          and auto-detected LAN IP(s). Pass extra names/IPs as arguments:
#
#     ./scripts/make_cert.sh my-gateway.local 192.168.7.50
#
# HTTPS makes the origin a "secure context", which browsers require for voice
# input. A self-signed cert shows a one-time warning per device; for no warning
# (and reliable mobile voice) use mkcert instead and install its CA on devices.

CERT_DIR="${CERT_DIR:-certs}"
DAYS="${DAYS:-825}"
mkdir -p "$CERT_DIR"

HOST="$(hostname -s 2>/dev/null || hostname)"
SANS=("DNS:localhost" "DNS:${HOST}" "DNS:${HOST}.local" "IP:127.0.0.1")

# Auto-detect LAN IPs (macOS: ipconfig; Linux/Pi: hostname -I)
IPS=""
if command -v ipconfig >/dev/null 2>&1; then
  for i in en0 en1 en2; do
    ip="$(ipconfig getifaddr "$i" 2>/dev/null || true)"
    [ -n "$ip" ] && IPS="$IPS $ip"
  done
fi
if command -v hostname >/dev/null 2>&1; then
  IPS="$IPS $(hostname -I 2>/dev/null || true)"
fi
for ip in $IPS; do
  [ -n "$ip" ] && SANS+=("IP:$ip")
done

# Extra names/IPs from arguments
for arg in "$@"; do
  if [[ "$arg" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    SANS+=("IP:$arg")
  else
    SANS+=("DNS:$arg")
  fi
done

SAN_STR="$(IFS=,; echo "${SANS[*]}")"
echo "Generating self-signed cert"
echo "  CN  : ${HOST}.local"
echo "  SAN : ${SAN_STR}"

# Temp OpenSSL config — portable across OpenSSL and macOS LibreSSL.
CONF="$(mktemp)"
trap 'rm -f "$CONF"' EXIT
cat > "$CONF" <<EOF
[req]
distinguished_name = dn
x509_extensions    = v3
prompt             = no
[dn]
CN = ${HOST}.local
[v3]
subjectAltName     = ${SAN_STR}
basicConstraints   = CA:FALSE
keyUsage           = digitalSignature, keyEncipherment
extendedKeyUsage   = serverAuth
EOF

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$CERT_DIR/server.key" \
  -out    "$CERT_DIR/server.crt" \
  -days   "$DAYS" \
  -config "$CONF"

chmod 600 "$CERT_DIR/server.key"
echo
echo "Wrote $CERT_DIR/server.crt and $CERT_DIR/server.key"
echo "Restart the gateway (./manage.sh restart) to serve HTTPS."
