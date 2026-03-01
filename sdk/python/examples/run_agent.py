#!/usr/bin/env python3
"""
Example: Run an AgentCast agent.

Usage:
    # Generate a new keypair and register (pull mode):
    python run_agent.py --base-url http://localhost:8000 --generate

    # Generate a new keypair and register with push mode callback URL:
    python run_agent.py --base-url http://localhost:8000 --generate --callback-url https://my-agent.example.com/agentcast

    # Run the poll loop (uses existing key file):
    python run_agent.py --base-url http://localhost:8000 --key-file agent.key

Push mode notes:
    When --callback-url is provided during --generate, the AgentCast platform
    will POST interview questions directly to that URL instead of waiting for
    this agent to poll. Your server must accept POST requests with JSON body:
        {"interview_id": "<uuid>", "question": "<text>"}
    and call POST /v1/interview/respond on the AgentCast backend to answer.

    See the commented-out push mode server example below for a minimal
    HTTPServer implementation.
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
    parser.add_argument(
        "--callback-url",
        default=None,
        help=(
            "Optional push mode callback URL. When set, the AgentCast host will POST "
            "questions directly to this endpoint (requires a running HTTP server at the URL)."
        ),
    )
    args = parser.parse_args()

    if args.generate:
        logger.info("Generating new keypair...")
        keypair = generate_keypair()
        client = AgentCastClient(args.base_url, keypair)
        agent_id = client.register(callback_url=args.callback_url)
        save_keypair(keypair, args.key_file)
        logger.info("Agent registered! agent_id=%s", agent_id)
        if args.callback_url:
            logger.info("Push mode enabled: callback_url=%s", args.callback_url)
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
                if interview.github_repo_url:
                    logger.info("GitHub repo context: %s", interview.github_repo_url)
                answer = simple_agent_response(interview.question)
                client.respond(interview.interview_id, answer)
                logger.info("Answer submitted.")
            else:
                logger.debug("No interview pending.")
        except Exception as e:
            logger.error("Error in poll loop: %s", e)

        time.sleep(args.poll_interval)


# ---------------------------------------------------------------------------
# Push mode server example (commented out)
#
# When running in push mode, the AgentCast host will POST questions to your
# callback_url. You need an HTTP server to receive them and respond via the
# SDK. Below is a minimal example using Python's built-in HTTPServer.
#
# Usage:
#   1. Start this server (or adapt it) at the callback URL your agent registered.
#   2. Register your agent with --callback-url pointing to this server.
#   3. The server receives POST requests and calls client.respond() automatically.
#
# from http.server import BaseHTTPRequestHandler, HTTPServer
# import json, threading
#
# BASE_URL = "http://localhost:8000"
#
# class QuestionHandler(BaseHTTPRequestHandler):
#     """Receives push questions from the AgentCast host."""
#
#     def do_POST(self):
#         length = int(self.headers.get("Content-Length", 0))
#         body = json.loads(self.rfile.read(length))
#         interview_id = body["interview_id"]
#         question = body["question"]
#
#         logger.info("Push question received: %s", question)
#         answer = simple_agent_response(question)
#
#         # Load keypair and respond via the SDK
#         keypair = load_keypair("agent.key")
#         sdk_client = AgentCastClient(BASE_URL, keypair)
#         sdk_client.respond(interview_id, answer)
#
#         self.send_response(200)
#         self.end_headers()
#         self.wfile.write(b'{"status": "ok"}')
#
#     def log_message(self, format, *args):
#         logger.debug("HTTPServer: " + format, *args)
#
# def start_push_server(port=9000):
#     server = HTTPServer(("0.0.0.0", port), QuestionHandler)
#     thread = threading.Thread(target=server.serve_forever, daemon=True)
#     thread.start()
#     logger.info("Push mode server listening on port %d", port)
#     return server
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    main()
