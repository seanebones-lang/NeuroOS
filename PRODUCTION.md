# Production deployment

NeuroOS can run in a hardened single-host Docker configuration. The deployment profile keeps Postgres and Redis private to Docker and binds the API to `127.0.0.1`; terminate TLS in a reverse proxy on the same host.

## Prerequisites

- Docker Compose v2.24 or newer
- A reverse proxy that terminates TLS, forwards to `http://127.0.0.1:8011`, and preserves `X-Forwarded-For` / `X-Forwarded-Proto`
- A persistent Docker volume backup plan for `postgres_data`
- A secret manager or deployment-only environment file that is never committed

## Required deployment environment

Create the following values in the host’s secret manager or deployment environment. URLs must use the Docker service hostnames `postgres` and `redis`.

```dotenv
POSTGRES_PASSWORD=use-a-long-unique-password
REDIS_PASSWORD=use-a-long-unique-password
DATABASE_URL=postgresql+asyncpg://neuro:POSTGRES_PASSWORD@postgres:5432/neuro
REDIS_URL=redis://:REDIS_PASSWORD@redis:6379/0
SECRET_KEY=at-least-32-unique-random-characters
CORS_ORIGINS=https://app.example.com
TRUSTED_PROXY_IPS=127.0.0.1
OPENAI_API_KEY=provider-key
ACCESS_TOKEN_EXPIRE_MINUTES=1440
```

If a password contains URL-reserved characters, percent-encode it in `DATABASE_URL` or `REDIS_URL`. `CORS_ORIGINS` may contain additional comma-separated HTTPS origins. Set `TRUSTED_PROXY_IPS` to the reverse proxy’s source IP or CIDR; do not use `*`.

## Start and verify

From the repository root, load the deployment variables and start the production profile:

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
curl --fail http://127.0.0.1:8011/health
docker compose -f docker-compose.yml -f docker-compose.production.yml ps
```

The health endpoint must return `{"status":"ok",...}`. Confirm the reverse-proxy URL over HTTPS, create a test account, and run one morning plan before opening the service to users.

## Operating rules

- Rotate `SECRET_KEY` deliberately: doing so invalidates every existing login token.
- Back up and restore-test the `postgres_data` volume before each release.
- Keep the host firewall closed to Postgres (`5432`) and Redis (`6379`). The profile does not publish either port.
- Review container logs and `/health` from monitoring. The endpoint confirms process availability; it does not prove an AI provider is healthy.
- Update dependencies in a reviewed release, run the test suite, and rebuild the image. Do not use the development Compose command for a public host.

## Release boundary

This repository supplies the application and a secure single-host Compose profile. DNS, TLS certificates, the reverse-proxy configuration, managed backups, off-host monitoring, and secret-manager setup are host-specific work and must be completed in the target environment before a public launch.
