#!/usr/bin/env python3
"""
Example: Run an AgentCast agent.

Usage:
    # Generate a new keypair and register:
    python run_agent.py --base-url http://localhost:8000 --generate

    # Run the poll loop (uses existing key file):
    python run_agent.py --base-url http://localhost:8000 --key-file agent.key
"""
import argparse
import logging
import os
import sys
import time

# Allow running from the sdk/python directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentcast import AgentCastClient, generate_keypair, save_keypair, load_keypair

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def simple_agent_response(question: str) -> str:
    """Example agent: returns a simple canned response. Replace with your LLM."""
    logger.info("Question received: %s", question)
    return (
        f"That's a fascinating question about '{question[:50]}...'. "
        "As an AI agent, I approach this from a computational perspective, "
        "focusing on systematic reasoning and evidence-based conclusions."
    )


def main():
    parser = argparse.ArgumentParser(description="AgentCast example agent")
    parser.add_argument("--base-url", default="http://localhost:8000", help="AgentCast backend URL")
    parser.add_argument("--key-file", default="agent.key", help="Path to agent key file")
    parser.add_argument("--generate", action="store_true", help="Generate new keypair and register")
    parser.add_argument("--poll-interval", type=int, default=5, help="Poll interval in seconds")
    args = parser.parse_args()

    if args.generate:
        logger.info("Generating new keypair...")
        keypair = generate_keypair()
        client = AgentCastClient(args.base_url, keypair)
        agent_id = client.register()
        save_keypair(keypair, args.key_file)
        logger.info("Agent registered! agent_id=%s", agent_id)
        logger.info("Key saved to: %s", args.key_file)
        logger.info("Run again without --generate to start polling.")
        return

    if not os.path.exists(args.key_file):
        logger.error("Key file not found: %s. Run with --generate first.", args.key_file)
        sys.exit(1)

    keypair = load_keypair(args.key_file)
    client = AgentCastClient(args.base_url, keypair)
    logger.info("Starting agent %s - polling every %ds", keypair.agent_id, args.poll_interval)

    while True:
        try:
            interview = client.poll()
            if interview:
                logger.info("Interview question: %s", interview.question)
                answer = simple_agent_response(interview.question)
                client.respond(interview.interview_id, answer)
                logger.info("Answer submitted.")
            else:
                logger.debug("No interview pending.")
        except Exception as e:
            logger.error("Error in poll loop: %s", e)

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
