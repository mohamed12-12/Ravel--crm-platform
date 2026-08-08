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
  Do **not** open 5002 or 5003 publicly - those are proxied through nginx.
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

## 11. Scaling past 1 worker: Redis-backed shared state -- the real rollout

**This is the actual production process manager, not sections 1-10.**
Confirmed live via `pm2 describe` (2026-08-08): the real box runs PM2, not
the `deploy/systemd/*.service` units sections 1-10 describe, and its
tracked launch commands are `deploy/pm2/ecosystem.config.js`, not a
systemd unit file. Separately: that file's `cwd`/paths
(`/home/ec2-user/rahma-crm-platform`) match the default home directory
for Amazon Linux's `ec2-user` login, not Ubuntu's `ubuntu` user --
sections 1-10's `apt install` commands were written for Ubuntu and were
very likely never actually run on this box. **Confirm the real OS before
running any install command below** rather than assuming either doc is
right:

```bash
cat /etc/os-release
pm2 list
which redis-server redis6-server valkey-server 2>/dev/null || echo "no redis-compatible server installed yet"
redis-cli ping 2>&1 || echo "not running / not installed"
```

### 11.1 Install and start Redis

Pick the branch matching what step above actually printed -- do not guess:

```bash
# Amazon Linux 2023 (dnf) -- try redis6 first, fall back to valkey
# (AL2023 replaced the redis6 package with the wire-compatible valkey in
# newer releases; either works fine with Flask-Limiter/Flask-SocketIO's
# redis:// client since both speak the same protocol)
sudo dnf install -y redis6 || sudo dnf install -y valkey
sudo systemctl enable --now redis6 2>/dev/null || sudo systemctl enable --now valkey

# Amazon Linux 2 (yum + extras)
sudo amazon-linux-extras install -y redis6
sudo systemctl enable --now redis6

# Ubuntu (only if step 0 actually confirmed Ubuntu)
sudo apt update && sudo apt install -y redis-server
sudo systemctl enable --now redis-server
```

Verify:

```bash
redis-cli ping     # must print PONG before continuing
```

Bind it to localhost only (usually the package default -- confirm, don't
assume) and do **not** open port 6379 in the security group; only 80/443
should ever be public (see section 1). If you want a password on top of
the localhost-only binding, set `requirepass <strong-value>` in its conf
file and use `redis://:<value>@127.0.0.1:6379/0` as `REDIS_URL` below.

### 11.2 Pull the code that actually has the fix, and its new dependencies

`eventlet` and `redis` were previously missing from what's actually
installed on this box (the drift that caused the original bug -- see the
discovery-report entry dated 2026-08-08d/e). Installing
`requirements.txt` now picks them up:

```bash
cd /home/ec2-user/rahma-crm-platform   # confirm this is the real path via `pm2 describe` first
git fetch && git checkout main && git pull
venv/bin/pip install -r requirements.txt
venv/bin/python -c "import eventlet, redis; print(eventlet.__version__, redis.__version__)"
```

### 11.3 Set REDIS_URL in the file this app actually reads

Confirmed by reading `apps/api/app/config.py` directly: it loads env vars
from the **repo-root** `.env` file (`REPO_ROOT / ".env"`, i.e.
`/home/ec2-user/rahma-crm-platform/.env`), not
`/etc/rahma-traveler/.env` -- that path is only correct for the
never-adopted systemd approach. Edit the real file:

```bash
echo 'REDIS_URL=redis://127.0.0.1:6379/0' >> /home/ec2-user/rahma-crm-platform/.env
```

`rahma-agent` does not need `REDIS_URL` at all -- its session state stays
an in-process dict by design and it stays pinned at 1 worker regardless
(see `deploy/systemd/rahma-ai-agent.service`'s comment); only
`rahma-crm-api`'s Socket.IO/rate-limiter state reads it.

### 11.4 Switch both processes to the tracked ecosystem file

```bash
pm2 delete rahma-agent rahma-crm-api
pm2 start deploy/pm2/ecosystem.config.js
pm2 save
pm2 list
```

### 11.5 Verify eventlet is actually the worker class running, not silently sync

```bash
pm2 describe rahma-crm-api | grep -i "script args"
pm2 describe rahma-agent | grep -i "script args"
ps aux | grep gunicorn        # confirm --worker-class eventlet appears in both command lines
pm2 logs rahma-crm-api --lines 50 --nostream   # gunicorn's boot lines print the worker class
```

### 11.6 Verify Redis is actually wired, not silently still in-memory

`REDIS_URL` being set doesn't guarantee the app picked it up correctly --
Flask-Limiter/Flask-SocketIO both connect lazily, so a typo'd URL can look
fine at boot and only fail on first use. Watch Redis directly while
poking the live app:

```bash
redis-cli monitor
# in a second terminal, hit an endpoint a few times to trigger the rate
# limiter, and trigger a handoff in the admin UI -- you should see
# INCRBY/EXPIRE-style commands for the limiter and PUBLISH/SUBSCRIBE
# traffic for Socket.IO appear in the monitor output as they happen
```

### 11.7 The two tests that actually prove this, not just that it starts

These are the definitive checks -- everything above can look correct and
still be wrong if one of these fails:

1. **Handoff notification, cross-worker.** Open two separate admin browser
   sessions (or one normal + one incognito window), trigger a handoff from
   one, and confirm the notification appears in the other in real time.
   This is exactly the scenario that was silently broken roughly half the
   time before this fix (two gunicorn worker processes, no shared state).
2. **Concurrent load, from on the box** (bypasses nginx/DNS, tests the
   gunicorn+eventlet layer directly):
   ```bash
   python scripts/concurrent_load_test.py --base-url http://127.0.0.1:5003 \
       --sessions 15 --messages 3
   ```
   Expect `RESULT: PASS` -- zero dropped requests, zero cross-contaminated
   sessions. Re-run once against `rahma-crm-api` on port 5002 too if you
   add a CRM-side load-test target later; today's script only covers the
   agent service (see "What this does NOT cover yet" in the discovery
   report).

### 11.8 Rollback

`deploy/pm2/ecosystem.config.js` is tracked in git now, so a rollback is
just:

```bash
git checkout <previous-known-good-commit-or-tag>
pm2 delete rahma-agent rahma-crm-api
pm2 start deploy/pm2/ecosystem.config.js   # from the checked-out (older) version of this file
pm2 save
```

If eventlet itself turns out to be the problem (unlikely given section
11.7 passed, but as a last resort), the emergency fallback is the exact
command that was already running before this pass -- reproduced here only
as a break-glass option, not a recommendation, since it's the config that
had the cross-process notification-loss bug in the first place:

```bash
pm2 delete rahma-crm-api
pm2 start /home/ec2-user/rahma-crm-platform/venv/bin/gunicorn --name rahma-crm-api \
    --cwd /home/ec2-user/rahma-crm-platform/apps/api --interpreter none -- \
    -w 2 -b 127.0.0.1:5002 run:app
```

## What this does NOT cover yet

- Automated CI/CD deploy (currently manual `git pull` + `systemctl restart`;
  `.github/workflows/ci.yml` only runs tests, it does not deploy).
- Managed Postgres failover/backups (use RDS with automated backups rather
  than a self-managed instance for production).
- Log shipping to CloudWatch (files currently just live under
  `/var/log/rahma-traveler/` - fine for one instance, wire up the CloudWatch
  agent before you need to debug across a restart/rotation).
