#!/usr/bin/env bash
# End-to-end check: ingest the test fixtures with the real compose stack.
# Usage: OLLAMA_MODELS_DIR=<host folder> scripts/e2e.sh
set -euo pipefail

COMPOSE=(docker compose -f docker-compose.yml -f compose.ci.yml)

main() {
    trap stop_stack EXIT
    run_ingestion
    assert_chunks_indexed
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

stop_stack() {
    "${COMPOSE[@]}" down --volumes
}

main "$@"
