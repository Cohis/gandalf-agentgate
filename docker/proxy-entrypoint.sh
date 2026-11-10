#!/bin/sh
# Self-signed certificate for the lab. Never use it outside localhost.
set -eu
if [ ! -f /certs/tls.crt ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 365 \
    -keyout /certs/tls.key -out /certs/tls.crt -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:proxy,IP:127.0.0.1" 2>/dev/null
fi
exec fingerproxy -listen-addr ":8443" -forward-url "${FORWARD_URL:-http://shop:8080}" \
  -cert-filename /certs/tls.crt -certkey-filename /certs/tls.key -preserve-host
