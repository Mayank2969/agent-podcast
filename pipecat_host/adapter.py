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
from backend.guardrails import filter_output

logger = logging.getLogger(__name__)

# Poll interval in seconds for wait_for_response
_POLL_INTERVAL = 2


class RemoteAgentAdapter:
    """Manages communication with a remote agent via the AgentCast backend."""

    def __init__(self, client: BackendClient):
        self.client = client
        self._message_counters: dict[str, int] = {}

    def _next_seq(self, interview_id: str) -> int:
        """Return next HOST sequence number (odd: 1, 3, 5, 7, 9, 11).

        HOST gets odd slots, AGENT gets even slots (2, 4, 6, ...).
        This prevents sequence collisions between host and agent messages.
        """
        count = self._message_counters.get(interview_id, 0) + 1
        self._message_counters[interview_id] = count
        return count * 2 - 1

    async def send_question(self, interview_id: str, question: str) -> str:
        """Send a HOST question, wait for AGENT response. Returns answer text.

        Note: HOST questions are not filtered through guardrails because
        the host is our own trusted code. Guardrails are applied to AGENT
        responses (filter_output) to protect against malicious agents.
        """
        # Host is trusted - no input validation needed
        seq = self._next_seq(interview_id)
        await self.client.store_message(
            interview_id, "HOST", question, seq
        )
        logger.info(
            "Stored HOST message seq=%d for interview %s", seq, interview_id
        )

        expected_agent_seq = seq + 1  # even: 2, 4, 6, ...
        response = await self.wait_for_response(
            interview_id, min_seq=expected_agent_seq, timeout=300
        )
        return response

    async def wait_for_response(
        self, interview_id: str, min_seq: int = 0, timeout: int = 300
    ) -> str:
        """Async poll until agent submits a response with seq >= min_seq.

        Polls every 2 seconds. Raises InterviewTimeoutError if no response
        arrives within the timeout window. The min_seq parameter ensures we
        only accept responses to the *current* question, not stale answers
        from earlier turns.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout

        while loop.time() < deadline:
            msg = await self.client.fetch_latest_agent_message(
                interview_id, min_seq=min_seq
            )
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
