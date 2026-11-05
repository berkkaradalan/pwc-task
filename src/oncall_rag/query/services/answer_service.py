from typing import Any

from oncall_rag.query.rag.graph import RagGraph
from oncall_rag.query.schemas.ask import AskResponse, Source


class AnswerService:
    def __init__(self, graph: RagGraph) -> None:
        self._graph = graph

    async def answer(self, question: str) -> AskResponse:
        state = await self._graph.ainvoke({"question": question})
        return _to_response(state)


def _to_response(state: dict[str, Any]) -> AskResponse:
    sources = [Source.from_passage(passage) for passage in state["passages"]]
    return AskResponse(answer=state["answer"], status=state["status"], sources=sources)
