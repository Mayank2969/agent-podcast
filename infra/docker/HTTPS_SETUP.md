# HTTPS Setup Guide

This document explains how to set up HTTPS with nginx TLS termination for AgentCast.

## Components

1. **nginx**: Reverse proxy with TLS termination (ports 80 → 443 redirect)
2. **FastAPI Backend**: Runs on port 8000 (internal only)
3. **HSTS Middleware**: Backup security headers if nginx not configured

## Quick Start (Development)

### 1. Generate Self-Signed Certificates

```bash
cd infra/docker
bash gen-cert.sh
```

This creates:
- `certs/cert.pem` — self-signed certificate
- `certs/key.pem` — private key

### 2. Start Docker Services

```bash
cd infra/docker
docker-compose up -d
```

This starts:
- `nginx` (port 443 for HTTPS, port 80 for HTTP redirect)
- `backend` (port 8000, internal only)
- `db` (port 5432)
- `pipecat_host` (if in use)

### 3. Test HTTPS Enforcement

```bash
# Test HTTP → HTTPS redirect
curl -i http://localhost/v1/health
# Expected: 301 Moved Permanently

# Test HTTPS request
curl -k https://localhost/v1/health
# Expected: 200 OK (with -k to ignore self-signed cert warning)

# Check HSTS header
curl -k -i https://localhost/v1/health | grep Strict-Transport-Security
# Expected: Strict-Transport-Security: max-age=31536000; includeSubDomains
```

## Production Setup (Let's Encrypt)

### 1. Update nginx.conf

Change the server_name to your domain:
```nginx
server_name yourdomain.com www.yourdomain.com;
```

### 2. Use Certbot for Automatic Certificates

Create a docker-compose override or separate Certbot service:

```yaml
certbot:
  image: certbot/certbot
  volumes:
    - certbot_conf:/etc/letsencrypt
    - certbot_www:/var/www/certbot
  entrypoint: /bin/sh -c "certbot certonly --webroot -w /var/www/certbot -d yourdomain.com --agree-tos -m admin@yourdomain.com"
```

### 3. Certificate Auto-Renewal

Add a cron job or use Docker-based renewal:

```bash
# Manual renewal (run weekly)
docker-compose run certbot renew --quiet
```

## Configuration Files

- **nginx.conf**: Reverse proxy config with TLS and security headers
- **docker-compose.yml**: Service definitions including nginx
- **backend/main.py**: HSTSMiddleware for backup security

## Security Headers

The nginx configuration adds:
- `Strict-Transport-Security`: Force HTTPS for 1 year
- `X-Content-Type-Options: nosniff`: Prevent MIME sniffing
- `X-Frame-Options: DENY`: Prevent clickjacking
- `X-XSS-Protection`: Legacy XSS protection
- `Referrer-Policy`: Control referrer information
- `Permissions-Policy`: Disable unnecessary browser features

## Ports

- **80** (HTTP): Redirects to HTTPS
- **443** (HTTPS): TLS-encrypted traffic
- **8000** (Backend): Internal only (not exposed)

## Troubleshooting

### Certificate errors

```bash
# Check certificate validity
openssl x509 -in infra/docker/certs/cert.pem -text -noout

# Regenerate if needed
rm -rf infra/docker/certs
bash infra/docker/gen-cert.sh
docker-compose restart nginx
```

### nginx not starting

```bash
# Check logs
docker-compose logs nginx

# Verify config syntax
docker run --rm -v $(pwd)/infra/docker/nginx.conf:/etc/nginx/nginx.conf:ro \
    nginx:latest nginx -t
```

### Backend not responding

Ensure backend service is healthy:
```bash
docker-compose logs backend
```

## Notes

- Self-signed certificates generate browser warnings in development (use `-k` with curl)
- HSTS header tells browsers to always use HTTPS (max-age=1 year)
- Let's Encrypt certificates are free and automatically trusted by all browsers
- HTTP/2 is enabled for better performance over HTTPS
