#!/usr/bin/env bash
set -euo pipefail

psql=(psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB")

create_role() {
  local role_name="$1"
  local role_password="$2"

  "${psql[@]}" <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${role_name}') THEN
    CREATE ROLE "${role_name}" LOGIN PASSWORD '${role_password}';
  ELSE
    ALTER ROLE "${role_name}" WITH LOGIN PASSWORD '${role_password}';
  END IF;
END
\$\$;
SQL
}

create_database() {
  local database_name="$1"
  local owner_name="$2"

  "${psql[@]}" <<SQL
SELECT 'CREATE DATABASE "${database_name}" OWNER "${owner_name}"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${database_name}')\gexec
SQL

  "${psql[@]}" <<SQL
ALTER DATABASE "${database_name}" OWNER TO "${owner_name}";
GRANT ALL PRIVILEGES ON DATABASE "${database_name}" TO "${owner_name}";
SQL
}

create_role "$MOODLE_DB_USER" "$MOODLE_DB_PASSWORD"
create_database "$MOODLE_DB_NAME" "$MOODLE_DB_USER"

create_role "$JUPYTERHUB_DB_USER" "$JUPYTERHUB_DB_PASSWORD"
create_database "$JUPYTERHUB_DB_NAME" "$JUPYTERHUB_DB_USER"

create_role "$GITLAB_JUPYTER_DB_USER" "$GITLAB_JUPYTER_DB_PASSWORD"
create_database "$GITLAB_JUPYTER_DB_NAME" "$GITLAB_JUPYTER_DB_USER"

create_role "$QA_DB_USER" "$QA_DB_PASSWORD"
create_database "$QA_DB_NAME" "$QA_DB_USER"

create_database "$MANTIS_DB_NAME" "$QA_DB_USER"
create_database "$TESTLINK_DB_NAME" "$QA_DB_USER"