# TLS-terminating reverse proxy that computes JA4 / HTTP2 fingerprints.
FROM debian:bookworm-slim
ARG FINGERPROXY_VERSION=v1.2.3
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl openssl \
 && rm -rf /var/lib/apt/lists/*
RUN set -eu; base="https://github.com/wi1dcard/fingerproxy/releases/download/${FINGERPROXY_VERSION}"; \
    arch="$(dpkg --print-architecture)"; \
    curl -fsSL -o "/tmp/fingerproxy_linux_${arch}" "${base}/fingerproxy_linux_${arch}"; \
    curl -fsSL -o /tmp/sums "${base}/fingerproxy.sha256sum"; \
    cd /tmp && grep "fingerproxy_linux_${arch}\$" sums | sha256sum -c -; \
    install -m 0755 "/tmp/fingerproxy_linux_${arch}" /usr/local/bin/fingerproxy; rm -f /tmp/fingerproxy_* /tmp/sums
COPY docker/proxy-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir /certs && chown 65534 /certs
USER 65534
EXPOSE 8443
ENTRYPOINT ["/entrypoint.sh"]
