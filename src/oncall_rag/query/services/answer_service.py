import asyncio
from collections.abc import Sequence

from oncall_rag.query.rag.graph import RagGraph
from oncall_rag.query.rag.state import initial_state
from oncall_rag.query.schemas.ask import AskResponse, HistoryMessage, Source
from oncall_rag.shared.models import ChatMessage


class AnswerService:
    def __init__(self, graph: RagGraph, *, deadline_seconds: float) -> None:
        self._graph = graph
        self._deadline_seconds = deadline_seconds

    async def answer(self, question: str, history: Sequence[HistoryMessage]) -> AskResponse:
        """Raises TimeoutError when the whole run takes longer than the deadline. Cancelling
        the run releases the language model slot and the pooled connection it holds."""
        messages = [ChatMessage(role=message.role, content=message.content) for message in history]
        try:
            async with asyncio.timeout(self._deadline_seconds):
                # version="v2" returns the final state typed as RagState, not dict[str, Any].
                output = await self._graph.ainvoke(initial_state(question, messages), version="v2")
        except TimeoutError as error:
            deadline = f"{self._deadline_seconds:g}"
            raise TimeoutError(f"the request deadline of {deadline} s expired") from error
        state = output.value
        sources = [Source.from_passage(passage) for passage in state["passages"]]
        return AskResponse(
            answer=state["answer"],
            status=state["status"],
            search_query=state["search_query"],
            sources=sources,
        )
