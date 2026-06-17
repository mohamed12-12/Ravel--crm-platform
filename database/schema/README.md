# Prisma Schema

Prisma schema for the future PostgreSQL-oriented middleware data model.

The current MVP still uses the Flask/SQLAlchemy SQLite database for active CRM behavior.

Run Prisma commands from `apps/middleware` through its package scripts so the schema path stays centralized:

```bash
npm run prisma:generate
npm run prisma:migrate
```
