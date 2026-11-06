from collections.abc import Sequence

from oncall_rag.query.rag.graph import RagGraph
from oncall_rag.query.rag.state import initial_state
from oncall_rag.query.schemas.ask import AskResponse, HistoryMessage, Source
from oncall_rag.shared.models import ChatMessage


class AnswerService:
    def __init__(self, graph: RagGraph) -> None:
        self._graph = graph

    async def answer(self, question: str, history: Sequence[HistoryMessage]) -> AskResponse:
        messages = [ChatMessage(role=message.role, content=message.content) for message in history]
        # version="v2" returns the final state typed as RagState instead of dict[str, Any].
        output = await self._graph.ainvoke(initial_state(question, messages), version="v2")
        state = output.value
        sources = [Source.from_passage(passage) for passage in state["passages"]]
        return AskResponse(
            answer=state["answer"],
            status=state["status"],
            search_query=state["search_query"],
            sources=sources,
        )
