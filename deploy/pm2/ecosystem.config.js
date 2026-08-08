// PM2 process definitions for the two Flask services, matching what's
// actually run in production today -- PM2, not the systemd units under
// deploy/systemd/ (those document an alternative deployment approach that
// was apparently never actually adopted). This file exists because there
// was previously no tracked PM2 config anywhere in the repo at all: the
// live launch commands (confirmed 2026-08-08 via `pm2 describe`) had
// drifted from every piece of documentation checked -- rahma-agent was
// running the raw Flask dev server (`python demo_web/app.py`) instead of
// gunicorn, and rahma-crm-api was running 2 plain sync workers with no
// eventlet and no REDIS_URL, silently breaking Socket.IO handoff
// notifications about half the time. Deploy from this file from now on
// so the real config stays in git instead of living only in whatever a
// past `pm2 start` command happened to be.
//
// Usage:
//   pm2 delete rahma-agent rahma-crm-api   (stop the old, untracked processes)
//   pm2 start deploy/pm2/ecosystem.config.js
//   pm2 save
//
// REDIS_URL must be set in the real environment (not here) before
// rahma-crm-api runs with more than 1 worker -- see deploy/README.md
// section 11. rahma-agent stays at 1 worker regardless: its session state
// (services/ai_agent/ai_agent_app/agent/tool_calling_runtime.py) is an
// in-process dict with no shared store, so more worker processes would
// silently fragment customer sessions across them.

module.exports = {
  apps: [
    {
      name: "rahma-agent",
      cwd: "/home/ec2-user/rahma-crm-platform",
      script: "venv/bin/gunicorn",
      // eventlet, not sync workers: lets this single worker serve many
      // concurrent customers' requests during I/O waits (including the
      // 1-6s Gemini API call) without blocking them behind each other,
      // while keeping the in-memory session dict single-process-consistent.
      // Bind port is 5003 to match nginx's real /rahma-agent/ upstream
      // (confirmed live 2026-08-08 -- this file previously said 3001,
      // which nginx was never actually proxying to).
      args: "--worker-class eventlet --workers 1 --bind 127.0.0.1:5003 --timeout 60 services.ai_agent.wsgi:app",
      interpreter: "none",
      exec_mode: "fork",
    },
    {
      name: "rahma-crm-api",
      cwd: "/home/ec2-user/rahma-crm-platform/apps/api",
      script: "/home/ec2-user/rahma-crm-platform/venv/bin/gunicorn",
      // eventlet is required for Flask-SocketIO. Workers > 1 requires
      // REDIS_URL to be set in the real environment first (see above).
      args: "--worker-class eventlet --workers 2 --bind 127.0.0.1:5002 --timeout 60 run:app",
      interpreter: "none",
      exec_mode: "fork",
    },
  ],
};
