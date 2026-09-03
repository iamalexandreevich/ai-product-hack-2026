-- Runs once, automatically, on first startup of the `db` service (Postgres
-- only executes files under /docker-entrypoint-initdb.d on an empty data
-- directory). Creates the second database used by the store test suite
-- (AGENTGATE_TEST_DB_URL), alongside the primary `agentgate` database that
-- POSTGRES_DB already creates.
CREATE DATABASE agentgate_test;
