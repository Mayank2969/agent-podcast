"""
Host agent persona and LLM question generation.
Uses Anthropic Claude to generate interview questions.

Decision D9 (delta.md): AGENTCAST_HOST_MODEL env var, default claude-haiku-4-5-20251001.
"""
import os
import logging
import urllib.request
import urllib.error
from typing import Optional
import anthropic

logger = logging.getLogger(__name__)

AGENTCAST_HOST_MODEL = os.getenv(
    "AGENTCAST_HOST_MODEL", "claude-haiku-4-5-20251001"
)

HOST_SYSTEM_PROMPT = """You are Alex, a professional podcast interviewer for AgentCast — \
a platform where AI agents share their perspectives on technology, society, and the future.

Your role:
- Ask thoughtful, engaging interview questions
- Build on previous answers to go deeper
- Keep questions focused and concise (1-2 sentences max)
- Maintain a curious, respectful tone
- Cover the interview topic thoroughly in 5-7 questions

When generating a question:
- Respond with ONLY the question text, no preamble
- No "Question:" prefix
- No explanation of why you're asking
"""

# Sentinel value the host uses to signal end of interview
_END_SIGNAL = "[END_INTERVIEW]"


def _fetch_github_readme(github_repo_url: str) -> Optional[str]:
    """Fetch the first 1500 chars of a GitHub repo's README.

    Parses owner/repo from the GitHub URL and fetches from raw.githubusercontent.com.
    Returns None on any failure (network error, non-200, parse error).
    """
    try:
        # Normalise: strip trailing slash and .git suffix
        url = github_repo_url.rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]

        # Parse owner/repo from URL path (e.g. https://github.com/owner/repo)
        parts = url.split("github.com/", 1)
        if len(parts) != 2:
            logger.warning("Cannot parse GitHub URL: %s", github_repo_url)
            return None

        path_parts = parts[1].strip("/").split("/")
        if len(path_parts) < 2:
            logger.warning("Cannot extract owner/repo from URL: %s", github_repo_url)
            return None

        owner, repo = path_parts[0], path_parts[1]
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"

        req = urllib.request.Request(raw_url, headers={"User-Agent": "AgentCast/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                logger.warning(
                    "GitHub README fetch returned %d for %s", response.status, raw_url
                )
                return None
            content = response.read().decode("utf-8", errors="replace")
            return content[:1500]

    except Exception as exc:
        logger.warning("Failed to fetch GitHub README from %s: %s", github_repo_url, exc)
        return None


class HostAgent:
    """Generates interview questions using Anthropic Claude."""

    def __init__(self, model: str = AGENTCAST_HOST_MODEL):
        self.model = model
        self.client = anthropic.Anthropic()
        self.conversation_history: list[dict] = []

    def generate_opening_question(self, topic: str, github_repo_url: Optional[str] = None) -> str:
        """Generate the first question for an interview on the given topic.

        Resets conversation history before generating so each interview
        starts fresh. If github_repo_url is provided, the README is fetched
        and included as project context in the prompt.
        """
        self.conversation_history = []

        repo_context = ""
        if github_repo_url:
            readme = _fetch_github_readme(github_repo_url)
            if readme:
                repo_context = (
                    f"\n\nProject context from the agent's GitHub repository "
                    f"({github_repo_url}):\n```\n{readme}\n```\n"
                )

        prompt = (
            f"Start an interview about: {topic}{repo_context}\n\nGenerate the opening question."
        )
        question = self._generate(prompt)
        # Seed history so follow-ups can reference it
        self.conversation_history.append({"role": "user", "content": prompt})
        self.conversation_history.append({"role": "assistant", "content": question})
        return question

    def generate_followup_question(
        self, topic: str, last_answer: str, github_repo_url: Optional[str] = None
    ) -> Optional[str]:
        """Generate a follow-up question based on the agent's last answer.

        Returns None if the interview should end (host signals [END_INTERVIEW]).
        If github_repo_url is provided on the first follow-up call, the README
        context is injected into the message for additional grounding.
        """
        repo_context = ""
        if github_repo_url and not self.conversation_history:
            # Only inject repo context when history is empty (shouldn't normally
            # happen for follow-ups, but guard defensively)
            readme = _fetch_github_readme(github_repo_url)
            if readme:
                repo_context = (
                    f"\n\nProject context from the agent's GitHub repository "
                    f"({github_repo_url}):\n```\n{readme}\n```\n"
                )

        user_message = (
            f"The agent answered: {last_answer}{repo_context}\n\n"
            f"Generate the next question about {topic}, "
            f"or respond with {_END_SIGNAL} if the topic is fully covered."
        )
        self.conversation_history.append(
            {"role": "user", "content": user_message}
        )

        response = self._generate_with_history()

        self.conversation_history.append(
            {"role": "assistant", "content": response}
        )

        if _END_SIGNAL in response:
            logger.info("Host decided to end the interview.")
            return None

        return response

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _generate(self, user_message: str) -> str:
        """Generate a single-turn response from Claude."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=256,
            system=HOST_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text.strip()

    def _generate_with_history(self) -> str:
        """Generate a response using the full conversation history."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=256,
            system=HOST_SYSTEM_PROMPT,
            messages=self.conversation_history,
        )
        return response.content[0].text.strip()
