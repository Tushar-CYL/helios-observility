-- HELIOS V1 — migration 0000
-- Creates the analytics database. Append-only, idempotent migrations.
-- Applied live in Phase 2 (collector + docker-compose); authored here in Phase 1.

CREATE DATABASE IF NOT EXISTS helios;
