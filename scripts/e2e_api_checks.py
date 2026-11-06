"""API checks for scripts/e2e.sh. Runs inside the api container, standard library only."""

import json
import sys
import urllib.request
from collections.abc import Sequence

API_URL = "http://localhost:8000/ask"
# CPU-only generation with the local model can take a minute or more.
REQUEST_TIMEOUT_SECONDS = 300
ANSWERABLE_QUESTION = "How do I mitigate a payments worker queue backlog?"
UNRELATED_QUESTION = "What is the capital of France?"
FIRST_QUESTION = "Should I run FLUSHALL to fix Redis memory pressure?"
FOLLOW_UP_QUESTION = "Why not?"
FOLLOW_UP_TOPICS = ("redis", "flushall")


class CheckFailedError(Exception):
    pass


def main() -> None:
    check_answerable_question()
    check_unrelated_question()
    check_follow_up_question()


def check_answerable_question() -> None:
    body = ask(ANSWERABLE_QUESTION)
    if body["status"] != "answered" or not body["sources"]:
        raise CheckFailedError(f"expected an answer with sources, got {body}")
    print(f"OK: answered with {len(body['sources'])} sources")


def check_unrelated_question() -> None:
    body = ask(UNRELATED_QUESTION)
    if body["status"] != "no_relevant_context":
        raise CheckFailedError(f"expected no_relevant_context, got {body}")
    print("OK: unrelated question has no relevant context")


def check_follow_up_question() -> None:
    first = ask(FIRST_QUESTION)
    history = [
        {"role": "user", "content": FIRST_QUESTION},
        {"role": "assistant", "content": first["answer"]},
    ]
    body = ask(FOLLOW_UP_QUESTION, history)
    if body["status"] != "answered" or not body["sources"]:
        raise CheckFailedError(f"expected an answer with sources, got {body}")
    search_query = str(body["search_query"])
    if not mentions_a_topic(search_query):
        raise CheckFailedError(f"expected the search query to name Redis, got {search_query!r}")
    print(f"OK: follow-up searched for {search_query!r}")


def mentions_a_topic(text: str) -> bool:
    lowered = text.lower()
    return any(topic in lowered for topic in FOLLOW_UP_TOPICS)


def ask(question: str, history: Sequence[dict[str, object]] = ()) -> dict[str, object]:
    payload = json.dumps({"question": question, "history": list(history)}).encode()
    request = urllib.request.Request(
        API_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        body: dict[str, object] = json.load(response)
    return body


if __name__ == "__main__":
    try:
        main()
    except CheckFailedError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
