"""
Pipecat host main polling loop.

Decision C2 (delta.md): polls GET /v1/interview/claim every 5 seconds.
When an interview is claimed (status -> IN_PROGRESS), runs it as a
background asyncio task so the loop can continue picking up more interviews.
"""
import asyncio
import logging
import os

from pipecat_host.backend_client import BackendClient
from pipecat_host.workflow import run_interview_workflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
POLL_INTERVAL = 5  # seconds between claim attempts


async def main() -> None:
    """Main polling loop — claims and runs interviews indefinitely."""
    client = BackendClient(base_url=BACKEND_URL)
    logger.info(
        "Pipecat host started. Polling %s every %ds for queued interviews.",
        BACKEND_URL,
        POLL_INTERVAL,
    )

    # Track active tasks so they are not GC'd before completion
    active_tasks: set[asyncio.Task] = set()

    while True:
        try:
            interview = await client.claim_interview()
            if interview:
                logger.info(
                    "Claimed interview %s (topic: %s)",
                    interview.get("interview_id"),
                    interview.get("topic"),
                )
                task = asyncio.create_task(run_interview_workflow(interview))
                active_tasks.add(task)
                # Automatically remove the task reference when it finishes
                task.add_done_callback(active_tasks.discard)
            else:
                logger.debug(
                    "No queued interviews available. Sleeping %ds.", POLL_INTERVAL
                )
        except Exception as exc:
            logger.error("Error in polling loop: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
