# Refactoring Plan

Protect behavior first. Extract read-only queries from `unified_service.py`, then inventory and sync, then agent HTTP adapters. Retire duplicated legacy adapters only through versioned API and deployment migration.
