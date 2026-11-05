#!/usr/bin/env bash
# End-to-end check: ingest data/docs with the real compose stack, then query the API.
# Usage: OLLAMA_MODELS_DIR=<host folder> scripts/e2e.sh
set -euo pipefail

COMPOSE=(docker compose -f docker-compose.yml -f compose.ci.yml)

main() {
    trap stop_stack EXIT
    run_ingestion
    assert_chunks_indexed
    start_api
    run_api_checks
}

run_ingestion() {
    "${COMPOSE[@]}" run --rm --build ingest
}

assert_chunks_indexed() {
    local chunk_count
    chunk_count=$(count_chunks)
    if [[ "$chunk_count" -eq 0 ]]; then
        echo "FAIL: the chunks table is empty" >&2
        return 1
    fi
    echo "OK: $chunk_count chunks indexed"
}

count_chunks() {
    "${COMPOSE[@]}" exec -T db sh -c \
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM chunks"'
}

start_api() {
    # Ingestion already ran above; --no-deps starts only the api.
    "${COMPOSE[@]}" up --detach --build --wait --no-deps api
}

run_api_checks() {
    "${COMPOSE[@]}" exec -T api python - < scripts/e2e_api_checks.py
}

stop_stack() {
    "${COMPOSE[@]}" down --volumes
}

main "$@"
