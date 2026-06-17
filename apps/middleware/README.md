# Middleware API

Express/TypeScript middleware intended to sit between the admin UI, CRM core, and future external integrations.

Current implementation proxies identity resolution to the Flask CRM API. Future work should add durable queues, auth, rate limiting, audit logging, and integration adapters.
