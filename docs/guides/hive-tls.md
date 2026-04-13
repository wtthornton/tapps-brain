# TLS for the Hive Stack (EPIC-067 STORY-067.4)

This guide covers adding HTTPS to the `tapps-visual` dashboard endpoint.
By default the hive stack only listens on port 80 — TLS is an operator-managed opt-in.

Two paths are documented:
1. **nginx SSL** — use the included `nginx-visual-tls.conf` with a volume-mounted certificate.
2. **Caddy reverse proxy** — zero-config HTTPS with automatic certificate management.

---

## Option 1: nginx SSL (recommended for most deployments)

### Dev/local: self-signed certificate

Generate a self-signed certificate (valid for 365 days):

```bash
mkdir -p docker/certs
openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
  -keyout docker/certs/key.pem \
  -out docker/certs/cert.pem \
  -subj "/CN=localhost"
```

Add a volume override to your `docker-compose.hive.yaml` (or a `docker-compose.override.yaml`):

```yaml
services:
  tapps-visual:
    volumes:
      - ./nginx-visual-tls.conf:/etc/nginx/conf.d/default.conf:ro
      - ./certs/cert.pem:/etc/nginx/certs/cert.pem:ro
      - ./certs/key.pem:/etc/nginx/certs/key.pem:ro
    ports:
      - "${TAPPS_VISUAL_PORT:-8088}:443"
```

Restart tapps-visual and visit `https://localhost:8088` (accept the browser warning for self-signed certs).

### Production: Let's Encrypt / Certbot

1. Install [Certbot](https://certbot.eff.org/) on the host.

2. Obtain a certificate (standalone mode — stop nginx first):
   ```bash
   certbot certonly --standalone -d your.domain.example
   ```
   Certificates are written to `/etc/letsencrypt/live/your.domain.example/`.

3. Mount the certificates as read-only volumes:
   ```yaml
   services:
     tapps-visual:
       volumes:
         - ./nginx-visual-tls.conf:/etc/nginx/conf.d/default.conf:ro
         - /etc/letsencrypt/live/your.domain.example/fullchain.pem:/etc/nginx/certs/cert.pem:ro
         - /etc/letsencrypt/live/your.domain.example/privkey.pem:/etc/nginx/certs/key.pem:ro
       ports:
         - "443:443"
         - "80:80"
   ```

4. Set up automatic renewal:
   ```bash
   # Certbot installs a systemd timer or cron job automatically.
   # After renewal, reload nginx:
   certbot renew --deploy-hook "docker compose -f /path/to/docker/docker-compose.hive.yaml exec tapps-visual nginx -s reload"
   ```

---

## Option 2: Caddy reverse proxy (zero-config alternative)

[Caddy](https://caddyserver.com/) handles certificate issuance and renewal automatically.
It proxies HTTPS traffic to the `tapps-visual` container running on port 80.

1. Keep `tapps-visual` on its default port (no TLS config needed in the container itself):
   ```yaml
   services:
     tapps-visual:
       ports:
         - "8088:80"   # or remove ports to expose internally only
   ```

2. Run Caddy alongside the stack. `docker-compose.override.yaml` example:
   ```yaml
   services:
     caddy:
       image: caddy:2-alpine
       ports:
         - "80:80"
         - "443:443"
       volumes:
         - ./Caddyfile:/etc/caddy/Caddyfile:ro
         - caddy_data:/data
         - caddy_config:/config
       networks:
         - docker_default   # same network as tapps-visual

   volumes:
     caddy_data:
     caddy_config:
   ```

3. Create a `Caddyfile` in the `docker/` directory:
   ```
   your.domain.example {
       reverse_proxy tapps-visual:80
   }
   ```

   Caddy automatically obtains and renews a Let's Encrypt certificate for `your.domain.example`.
   For local/self-signed use, replace the domain with `localhost` — Caddy issues a locally-trusted cert via its internal CA.

---

## Verifying TLS

After configuration, verify the certificate and connection:

```bash
# Check certificate details
curl -v https://your.domain.example/health 2>&1 | grep -E "SSL|subject|issuer|expire"

# Quick health check
curl https://your.domain.example/snapshot | python3 -m json.tool | head -20
```

---

## References

- [docker/nginx-visual-tls.conf](../../docker/nginx-visual-tls.conf) — nginx TLS config drop-in
- [docker/README.md](../../docker/README.md) — stack overview and make targets
- [docs/guides/hive-deployment.md](hive-deployment.md) — full deployment guide
- [Certbot documentation](https://certbot.eff.org/instructions)
- [Caddy documentation](https://caddyserver.com/docs/)
