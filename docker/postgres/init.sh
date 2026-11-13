#!/bin/bash
# Изпълнява се еднократно при първо създаване на тома pgdata.
# Създава тестовата база и разширението pg_trgm (търсене в пикерите, docs/data-model.md §5).
# Ако трябва да се изпълни наново: docker compose down -v  (трие данните!)
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    CREATE DATABASE agro_test OWNER ${POSTGRES_USER};
EOSQL

for db in "$POSTGRES_DB" agro_test; do
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<-EOSQL
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
EOSQL
done
