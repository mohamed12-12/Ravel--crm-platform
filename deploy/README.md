# EC2 Deployment Guide

Companion to `docs/deployment/DEPLOYMENT_RUNBOOK.md` and
`docs/deployment/DEPLOYMENT_ENV_CHECKLIST.md` (existing, keep using those for
the pre-deploy checklist and backup steps). This doc covers the specific
pieces that were missing: how the two Flask services actually get served in
production (gunicorn, not the dev server), and the systemd/nginx wiring for
a single EC2 instance.

This is deliberately scoped to **one EC2 instance running both services**
behind nginx - the simplest thing that satisfies "get it running on EC2". It
does not cover auto-scaling groups, multi-instance Socket.IO fan-out (needs
Redis - see the note in `deploy/systemd/rahma-crm-api.service`), or
containerization; treat those as later scaling decisions, not blockers for a
first deploy.

## 1. Provision the instance

- Ubuntu 22.04 LTS (or similar), t3.small or larger.
- Security group: allow 22 (SSH, restricted to your IP), 80 and 443 (public).
  Do **not** open 5000 or 3001 publicly - those are proxied through nginx.
- Attach an EBS volume sized for uploads/DB growth if not using RDS/EFS.
- Point a domain's A record at the instance's Elastic IP (needed for Meta's
  webhook HTTPS requirement and for certbot).

## 2. System packages

```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv nginx certbot python3-certbot-nginx git
sudo useradd --system --create-home --shell /usr/sbin/nologin rahma
sudo mkdir -p /opt/rahma-traveler /etc/rahma-traveler /var/log/rahma-traveler \
    /var/lib/rahma-traveler/uploads/travelers /var/lib/rahma-traveler/uploads/agent
sudo chown -R rahma:rahma /opt/rahma-traveler /var/log/rahma-traveler /var/lib/rahma-traveler
```

## 3. Deploy the code

```bash
sudo -u rahma git clone <your-repo-url> /opt/rahma-traveler
cd /opt/rahma-traveler
sudo -u rahma python3.11 -m venv /opt/rahma-traveler/venv
sudo -u rahma /opt/rahma-traveler/venv/bin/pip install -r requirements.txt
```

Using a real venv here (unlike local dev, which this repo currently runs
against a global interpreter) means `pip freeze` inside it is a trustworthy,
reproducible lockfile - worth capturing once things are stable:

```bash
sudo -u rahma /opt/rahma-traveler/venv/bin/pip freeze > requirements.lock.txt
```

## 4. Configure the environment

```bash
sudo cp .env.production.example /etc/rahma-traveler/.env
sudo chown rahma:rahma /etc/rahma-traveler/.env
sudo chmod 600 /etc/rahma-traveler/.env
sudo -u rahma nano /etc/rahma-traveler/.env   # fill in every SECRET-marked value
```

Fill in, at minimum: `SECRET_KEY`, `APP_SECRET_KEY`, `ADMIN_USERNAME`/
`ADMIN_PASSWORD`, `CRM_API_TOKEN`, `DATABASE_URL` (production Postgres),
`GEMINI_API_KEY`, and the `META_*` values once you've created the Meta App
(step 7). Prefer pulling secrets from AWS Secrets Manager/SSM Parameter
Store into this file at deploy time over hand-editing it, once you have that
wired up - see `docs/deployment/DEPLOYMENT_ENV_CHECKLIST.md`'s "Secret
Handling" section.

## 5. Database

Run the production Postgres schema/migration steps from
`database/postgres/DEVOPS_POSTGRES_SETUP_INSTRUCTIONS.md` and
`database/postgres/SQLITE_TO_POSTGRES_MIGRATION_PLAN.md` against your real
production database before first start. Do not point `DATABASE_URL` at the
same database used for staging smoke tests.

## 6. Install and start the services

```bash
sudo cp deploy/systemd/rahma-crm-api.service deploy/systemd/rahma-ai-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rahma-crm-api rahma-ai-agent
sudo systemctl status rahma-crm-api rahma-ai-agent
```

Check logs if a service doesn't come up:

```bash
sudo journalctl -u rahma-crm-api -n 100 --no-pager
tail -n 100 /var/log/rahma-traveler/crm-api-error.log
```

## 7. nginx + TLS

```bash
sudo cp deploy/nginx/rahma-traveler.conf /etc/nginx/sites-available/rahma-traveler.conf
sudo ln -s /etc/nginx/sites-available/rahma-traveler.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d your-domain.example.com
```

## 8. Meta App Dashboard (Instagram webhook)

1. Create/open your app at developers.facebook.com, add the
   Instagram/Messenger product.
2. Webhook callback URL: `https://your-domain.example.com/webhook`.
3. Verify token: any value you generate - put the same value in
   `META_VERIFY_TOKEN` in `/etc/rahma-traveler/.env` before clicking
   "Verify and Save" (the app must already be running for the handshake to
   succeed).
4. Subscribe to the `messages` field for your Instagram Business Account.
5. Generate a page-scoped access token -> `META_PAGE_ACCESS_TOKEN`.
6. App Secret (Settings > Basic) -> `META_APP_SECRET`.
7. Note the Page/IG Business Account ID shown in the dashboard ->
   `META_PAGE_ID` (see `services/instagram/webhooks.py` -
   `filter_entries_for_page`; required once secrets are set, per
   `services/ai_agent/ai_agent_app/config.py`'s production validation).
8. Restart the agent service after updating `.env`:
   `sudo systemctl restart rahma-ai-agent`.

Verify with:

```bash
curl -i "https://your-domain.example.com/webhook?hub.mode=subscribe&hub.verify_token=<your token>&hub.challenge=123"
# expect: 200 and body "123"
```

## 9. Smoke test after deploy

```bash
curl -i https://your-domain.example.com/crm/admin/db-health
curl -i https://your-domain.example.com/
python -m pytest tests/test_deployment_production_validation.py tests/test_port1_write_safety_idempotency.py \
    tests/test_port2_write_result_response_gating.py tests/test_port3_response_guard.py \
    tests/test_instagram_webhook.py -q
```

## 10. Redeploy / rollback

```bash
cd /opt/rahma-traveler
sudo -u rahma git fetch && sudo -u rahma git checkout <commit-or-tag>
sudo -u rahma /opt/rahma-traveler/venv/bin/pip install -r requirements.txt
sudo systemctl restart rahma-crm-api rahma-ai-agent
```

To roll back, `git checkout` the previous known-good commit/tag and repeat.
See `database/postgres/POSTGRES_MIGRATION_ROLLBACK_PLAN.md` if a DB migration
needs to be reverted too.

## What this does NOT cover yet

- Horizontal scaling / multiple instances (Socket.IO needs a Redis
  `message_queue` first - see `deploy/systemd/rahma-crm-api.service`).
- Automated CI/CD deploy (currently manual `git pull` + `systemctl restart`;
  `.github/workflows/ci.yml` only runs tests, it does not deploy).
- Managed Postgres failover/backups (use RDS with automated backups rather
  than a self-managed instance for production).
- Log shipping to CloudWatch (files currently just live under
  `/var/log/rahma-traveler/` - fine for one instance, wire up the CloudWatch
  agent before you need to debug across a restart/rotation).
