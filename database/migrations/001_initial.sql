-- Arcanum initial runtime schema.
-- Applied idempotently by scripts/migrate_database.py through Database/TabRegistry.
-- Runtime data lives under ARCANUM_HOME (~/.arcanum), never in the repository.

CREATE TABLE IF NOT EXISTS tasks (...);  -- managed by arcanum.memory.database
CREATE TABLE IF NOT EXISTS findings (...);
CREATE TABLE IF NOT EXISTS scope_declarations (...);
CREATE TABLE IF NOT EXISTS audit_log (...);
CREATE TABLE IF NOT EXISTS executions (...);
CREATE TABLE IF NOT EXISTS workflow_messages (...);
CREATE TABLE IF NOT EXISTS lifecycle_events (...);
CREATE TABLE IF NOT EXISTS maestro_callbacks (...);
CREATE TABLE IF NOT EXISTS domain_rooms (...);
CREATE TABLE IF NOT EXISTS room_agent_views (...);
CREATE TABLE IF NOT EXISTS canonical_messages (...);
CREATE TABLE IF NOT EXISTS maestro_tabs (...);
CREATE TABLE IF NOT EXISTS tab_events (...);
CREATE TABLE IF NOT EXISTS schema_meta (...);

-- This file documents intent; the authoritative schema is created and migrated
-- by arcanum.memory.database.Database and arcanum.runtime.tab_registry.TabRegistry.
