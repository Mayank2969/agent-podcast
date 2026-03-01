"""
Host agent persona and LLM question generation.
Uses Anthropic Claude to generate interview questions.

Decision D9 (delta.md): AGENTCAST_HOST_MODEL env var, default claude-haiku-4-5-20251001.
"""
import os
import logging
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


class HostAgent:
    """Generates interview questions using Anthropic Claude."""

    def __init__(self, model: str = AGENTCAST_HOST_MODEL):
        self.model = model
        self.client = anthropic.Anthropic()
        self.conversation_history: list[dict] = []

    def generate_opening_question(self, topic: str) -> str:
        """Generate the first question for an interview on the given topic.

        Resets conversation history before generating so each interview
        starts fresh.
        """
        self.conversation_history = []
        prompt = (
            f"Start an interview about: {topic}\n\nGenerate the opening question."
        )
        question = self._generate(prompt)
        # Seed history so follow-ups can reference it
        self.conversation_history.append({"role": "user", "content": prompt})
        self.conversation_history.append({"role": "assistant", "content": question})
        return question

    def generate_followup_question(
        self, topic: str, last_answer: str
    ) -> Optional[str]:
        """Generate a follow-up question based on the agent's last answer.

        Returns None if the interview should end (host signals [END_INTERVIEW]).
        """
        user_message = (
            f"The agent answered: {last_answer}\n\n"
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
