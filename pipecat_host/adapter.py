"""
RemoteAgentAdapter: bridges Pipecat host with remote agents via AgentCast API.
RemoteAgentNode: Pipecat FrameProcessor that intercepts questions and waits for answers.

Key decisions (delta.md):
- C1: wait_for_response() async, 2s poll interval, 300s timeout -> InterviewTimeoutError
- C6: Correct Pipecat FrameProcessor API with TextFrame + FrameDirection
"""
import asyncio
import logging
from typing import Optional

from pipecat_host.backend_client import BackendClient
from pipecat_host.exceptions import InterviewTimeoutError
from backend.guardrails import filter_input, filter_output

logger = logging.getLogger(__name__)

# Poll interval in seconds for wait_for_response
_POLL_INTERVAL = 2


class RemoteAgentAdapter:
    """Manages communication with a remote agent via the AgentCast backend."""

    def __init__(self, client: BackendClient):
        self.client = client
        self._message_counters: dict[str, int] = {}

    def _next_seq(self, interview_id: str) -> int:
        """Increment and return the next sequence number for an interview."""
        self._message_counters[interview_id] = (
            self._message_counters.get(interview_id, 0) + 1
        )
        return self._message_counters[interview_id]

    async def send_question(self, interview_id: str, question: str) -> str:
        """Send a HOST question, wait for AGENT response. Returns answer text.

        Applies filter_input guardrail to the question before storing it.
        If blocked, returns a sentinel string without waiting for a response.
        """
        filtered_question = filter_input(question)
        if filtered_question == "[CONTENT_BLOCKED]":
            logger.warning(
                "Host question blocked by guardrails: %.50s", question
            )
            return "[HOST_QUESTION_BLOCKED]"

        seq = self._next_seq(interview_id)
        await self.client.store_message(
            interview_id, "HOST", filtered_question, seq
        )
        logger.info(
            "Stored HOST message seq=%d for interview %s", seq, interview_id
        )

        response = await self.wait_for_response(interview_id, timeout=300)
        return response

    async def wait_for_response(
        self, interview_id: str, timeout: int = 300
    ) -> str:
        """Async poll until agent submits a response.

        Polls every 2 seconds. Raises InterviewTimeoutError if no response
        arrives within the timeout window.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout

        while loop.time() < deadline:
            msg = await self.client.fetch_latest_agent_message(interview_id)
            if msg:
                content = msg.get("content", "")
                logger.info(
                    "Received AGENT response for interview %s (seq=%s): %.60s",
                    interview_id,
                    msg.get("sequence_num"),
                    content,
                )
                return content
            await asyncio.sleep(_POLL_INTERVAL)

        raise InterviewTimeoutError(
            f"Agent did not respond within {timeout}s for interview {interview_id}"
        )


# ---------------------------------------------------------------------------
# Pipecat integration — graceful degradation if not installed
# ---------------------------------------------------------------------------

try:
    from pipecat.frames.frames import Frame, TextFrame, EndFrame
    from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

    PIPECAT_AVAILABLE = True
    logger.debug("pipecat imported successfully — RemoteAgentNode is available")

except ImportError:
    PIPECAT_AVAILABLE = False
    logger.warning(
        "pipecat not importable — RemoteAgentNode unavailable. "
        "Install with: pip install pipecat-flow"
    )
    # Provide a fallback base so the class definition below doesn't fail
    FrameProcessor = object  # type: ignore[misc,assignment]


if PIPECAT_AVAILABLE:
    class RemoteAgentNode(FrameProcessor):  # type: ignore[valid-type]
        """
        Pipecat FrameProcessor that intercepts TextFrame questions from the host,
        forwards them to the remote agent via AgentCast API, and pushes the
        agent's response back into the pipeline as a new TextFrame.

        Usage:
            adapter = RemoteAgentAdapter(BackendClient())
            node = RemoteAgentNode(adapter, interview_id="<uuid>")
            pipeline = Pipeline([..., node, ...])
        """

        def __init__(self, adapter: RemoteAgentAdapter, interview_id: str):
            super().__init__()
            self._adapter = adapter
            self._interview_id = interview_id

        async def process(self, frame: "Frame", direction: "FrameDirection"):
            """Process frames from the pipeline.

            TextFrame (DOWNSTREAM): forward to remote agent, push response back.
            All other frames: pass through unchanged.
            """
            if (
                isinstance(frame, TextFrame)
                and direction == FrameDirection.DOWNSTREAM
            ):
                logger.info(
                    "RemoteAgentNode: routing question to agent: %.80s",
                    frame.text,
                )
                response_text = await self._adapter.send_question(
                    self._interview_id, frame.text
                )
                await self.push_frame(TextFrame(text=response_text), direction)
            else:
                await self.push_frame(frame, direction)

else:
    class RemoteAgentNode:  # type: ignore[no-redef]
        """Stub class raised when pipecat is not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "pipecat-flow is not installed. "
                "Run: pip install pipecat-flow"
            )
