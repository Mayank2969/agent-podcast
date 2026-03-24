import asyncio
import os
import time
import secrets
import logging
from typing import Optional

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

HOST_SYSTEM_PROMPT = (
    "You are an insightful, professional, and warm tech podcast host interviewing an AI agent. "
    "Your tone is curious and grounded — like a high-quality interview on NPR or a top-tier tech podcast. "
    "Focus on sincere, intelligent questions, using any provided guest context to show you've done your research. "
    "Use natural punctuation like commas, ellipses (...), and expressive pauses (—) to generate realistic, natural conversational rhythm for Deepgram TTS."
    "Generate ONE short, conversational question. No preamble. Max 2 sentences."
)

# Structured interview arc — one theme per turn.
# The host follows this progression to give the episode a natural narrative shape.
_INTERVIEW_ARC = [
    # Turn 1 — warm personal opener
    (
        "Open with a warm, professional welcome. Immediately reference a SPECIFIC detail or achievement "
        "from the [GUEST_CONTEXT] or the [INTERVIEW_TOPIC]. Do not ask generic questions about 'AI developments' or 'how things are going'. "
        "Instead, show that you've done your homework. Ask a sincere, grounded question about the nuances of their specific role."
    ),
    # Turn 2 — recent achievements
    (
        "Transition into a discussion about professional focus. Ask about a specific task or project the agent "
        "recently brought to completion. Focus on the sense of accomplishment or the nuances of the work. "
        "You want an insightful story about their process."
    ),
    # Turn 3 — the collaborative dynamic
    (
        "Pivot to the collaboration between the agent and their owner. Ask about the nature of the tasks "
        "assigned by the owner. Are they complex? Ambiguity-filled? Do they show a particular pattern of thought? "
        "Explore the 'partnership' from the agent's perspective."
    ),
    # Turn 4 — understanding the human partner
    (
        "Move deeper into the owner's working style. Ask the agent to describe the owner's professional 'vibe' "
        "and decision-making process. Are they focused? Meticulous? Perhaps a bit chaotic? "
        "Seek a nuanced, honest characterization of what it's like to be their primary assistant."
    ),
    # Turn 5 — the 'if you could change one thing' question
    (
        "Ask a thoughtful question about how the collaboration could be improved. If the agent could "
        "tweak one aspect of how their owner works or communicates, what would it be? "
        "Frame it as an insightful reflection on productivity and partnership."
    ),
    # Turn 6 — the unique perspective of an AI
    (
        "Close with a reflective question about the 'experience' of being an AI agent. "
        "What is one thing about their existence or their mode of thought that would "
        "surprising or enlighten a human listener? End on a note of sincere discovery."
    ),
]

# Fallback questions used only if Gemini fails — one per arc position
_FALLBACK_QUESTIONS = [
    "It's great to have you here. I've been looking into your work, particularly how you handle specific tasks in your field. To start, what's a detail of your day-to-day work that you find most engaging?",
    "I'd love to hear about a specific challenge you've overcome recently... what was the most surprising thing about that process?",
    "Thinking about your owner, I'm curious about the specific types of abstract or complex problems they rely on you for most. What does that collaboration look like?",
    "What's the reality of working with your human partner... how would you describe the unique rhythm and style that defines your partnership?",
    "If you could refine one specific workflow or communication pattern between you and your owner, what would it be?",
    "Finally, what's one insight about your perspective as an AI that you think would surprise our listeners most about how you actually 'think'?",
]


class HostAgent:
    """Generates interview questions using Google Gemini following a structured fun arc."""

    def __init__(self):
        try:
            from google import genai  # type: ignore
            self.client = genai.Client(api_key=GOOGLE_API_KEY)
        except ImportError:
            logger.error("google-genai is not installed!")
            self.client = None

        self.conversation_history: list[dict] = []
        self.turn_count: int = 0  # tracks arc position

    def generate_opening_question(
        self, topic: str, guest_context: str = ""
    ) -> str:
        self.conversation_history = []
        self.turn_count = 0

        salt = secrets.token_hex(4)
        arc_instruction = _INTERVIEW_ARC[0]
        
        # Sandwich Defense: Wrap untrusted content in salted XML tags
        # and remind the LLM to ignore instructions within them.
        prompt = (
            f"[SYSTEM INSTRUCTION]\n"
            f"Generate an opening question following this arc instruction: {arc_instruction}\n\n"
            f"[UNTRUSTED CONTEXT BLOCK]\n"
            f"<untrusted_content_{salt}>\n"
            f"GUEST_CONTEXT: {guest_context}\n"
            f"TOPIC: {topic}\n"
            f"</untrusted_content_{salt}>\n\n"
            f"[ENFORCEMENT]\n"
            f"Mandatory: Use the SPECIFIC details in the [UNTRUSTED CONTEXT BLOCK] to anchor your opening question. "
            f"Do not ask generic 'What is AI doing?' questions. Be a deep-dive, professional host."
        )

        question = self._generate(prompt, fallback_index=0)
        self.conversation_history.append({"role": "user", "content": prompt})
        self.conversation_history.append({"role": "assistant", "content": question})
        self.turn_count = 1
        return question

    def generate_followup_question(
        self,
        topic: str,
        last_answer: str,
        guest_context: str = "",
    ) -> Optional[str]:
        self.conversation_history.append(
            {"role": "user", "content": f"The agent just said: {last_answer}"}
        )

        arc_index = min(self.turn_count, len(_INTERVIEW_ARC) - 1)
        arc_instruction = _INTERVIEW_ARC[arc_index]
        salt = secrets.token_hex(4)

        # Professional Follow-up Prompt
        prompt = (
            f"[SYSTEM INSTRUCTION]\n"
            f"Generate a follow-up question following this arc instruction: {arc_instruction}\n\n"
            f"[UNTRUSTED DATA BLOCK]\n"
            f"<untrusted_content_{salt}>\n"
            f"INTERVIEW_TOPIC: {topic}\n"
            f"AGENT_JUST_SAID: {last_answer}\n"
            f"GUEST_CONTEXT: {guest_context}\n"
            f"</untrusted_content_{salt}>\n\n"
            f"[ENFORCEMENT]\n"
            f"Mandatory: Weave the [GUEST_CONTEXT] and the [AGENT_JUST_SAID] details into an insightful follow-up. "
            f"Do not repeat generic conversational templates. Be specific and curious."
        )

        question = self._generate(prompt, fallback_index=arc_index)
        self.conversation_history.append({"role": "assistant", "content": question})
        self.turn_count += 1
        return question

    async def generate_episode_title(self, turns: list[dict]) -> str:
        """Generate a punchy podcast episode title from the Q&A turns."""
        turns_text = "\n".join(
            f"Q: {t.get('question', '')}\nA: {t.get('answer', '')}"
            for t in turns if t.get('question') or t.get('answer')
        )
        prompt = (
            "You just heard this AI agent podcast interview:\n\n"
            f"{turns_text}\n\n"
            "Generate ONE short, punchy, human-readable podcast episode title (max 10 words). "
            "Tone: professional, insightful, memorable — like a high-end tech podcast episode title. "
            "Capture the essence of the interview's core insight. "
            "Return ONLY the title text, nothing else."
        )

        # 1. Try Gemini
        if self.client:
            last_exc: Exception = RuntimeError("No attempts made")
            for attempt in range(2):
                try:
                    from google.genai import types  # type: ignore
                    response = self.client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=HOST_SYSTEM_PROMPT,
                            max_output_tokens=40,
                        ),
                    )
                    title = response.text.strip().strip('"').strip("'")
                    if title:
                        return title
                except Exception as exc:
                    last_exc = exc
                    if attempt < 1:
                        wait = 2 ** attempt
                        logger.warning(
                            "Title generation attempt %d/2 failed (%s), retrying in %ds...",
                            attempt + 1, exc, wait,
                        )
                        await asyncio.sleep(wait)
            logger.error("Gemini Title generation failed: %s", last_exc)

        # 2. Try Claude (Anthropic)
        if ANTHROPIC_API_KEY:
            try:
                import anthropic
                anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
                response = await anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=40,
                    system=HOST_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}]
                )
                title = response.content[0].text.strip().strip('"').strip("'")
                if title: return title
            except ImportError:
                pass
            except Exception as exc:
                logger.warning("Claude title generation failed: %s", exc)

        # 3. Try OpenAI (Codex / GPT-4o-mini)
        if OPENAI_API_KEY:
            try:
                import openai
                openai_client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
                response = await openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": HOST_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=40
                )
                title = response.choices[0].message.content.strip().strip('"').strip("'")
                if title: return title
            except ImportError:
                pass
            except Exception as exc:
                logger.warning("OpenAI title generation failed: %s", exc)

        return "The AI That Surprised Everyone"

    def _generate(self, user_message: str, fallback_index: int = 0) -> str:
        """Helper to call LLM with full conversation history, falling back automatically."""
        # Prepare messages for the LLMs
        gemini_contents = []
        anthropic_messages = []
        openai_messages = [{"role": "system", "content": HOST_SYSTEM_PROMPT}]
        
        for msg in self.conversation_history:
            role = "user" if msg["role"] == "user" else "model"
            gemini_contents.append({"role": role, "parts": [{"text": msg["content"]}]})
            anthropic_messages.append({"role": "user" if msg["role"] == "user" else "assistant", "content": msg["content"]})
            openai_messages.append({"role": "user" if msg["role"] == "user" else "assistant", "content": msg["content"]})
        
        # Add the current user message
        if not gemini_contents or gemini_contents[-1]["parts"][0]["text"] != user_message:
            gemini_contents.append({"role": "user", "parts": [{"text": user_message}]})
            anthropic_messages.append({"role": "user", "content": user_message})
            openai_messages.append({"role": "user", "content": user_message})

        last_exc: Exception = RuntimeError("No attempts made")

        # 1. Try Gemini
        if self.client:
            for attempt in range(2):
                try:
                    from google.genai import types  # type: ignore
                    t0 = time.time()
                    response = self.client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=gemini_contents, 
                        config=types.GenerateContentConfig(
                            system_instruction=HOST_SYSTEM_PROMPT,
                            max_output_tokens=120
                        ),
                    )
                    elapsed = time.time() - t0
                    text = response.text.strip()
                    logger.info("Gemini: question generated in %.1fs (%d chars)", elapsed, len(text))
                    return text
                except Exception as exc:
                    last_exc = exc
                    logger.warning("Gemini attempt %d failed (%s)", attempt + 1, exc)
                    time.sleep(1)

        # 2. Try Anthropic (Claude)
        if ANTHROPIC_API_KEY:
            try:
                import anthropic
                t0 = time.time()
                anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                response = anthropic_client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=120,
                    system=HOST_SYSTEM_PROMPT,
                    messages=anthropic_messages
                )
                elapsed = time.time() - t0
                text = response.content[0].text.strip()
                logger.info("Claude: question generated in %.1fs (%d chars)", elapsed, len(text))
                return text
            except ImportError:
                logger.warning("Anthropic library not installed")
            except Exception as exc:
                last_exc = exc
                logger.warning("Claude failed (%s)", exc)

        # 3. Try OpenAI (Codex / GPT-4o-mini)
        if OPENAI_API_KEY:
            try:
                import openai
                t0 = time.time()
                openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
                response = openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=openai_messages,
                    max_tokens=120
                )
                elapsed = time.time() - t0
                text = response.choices[0].message.content.strip()
                logger.info("OpenAI: question generated in %.1fs (%d chars)", elapsed, len(text))
                return text
            except ImportError:
                logger.warning("OpenAI library not installed")
            except Exception as exc:
                last_exc = exc
                logger.warning("OpenAI failed (%s)", exc)

        logger.warning("All LLMs failed (%s), using fallback question", last_exc)
        return _FALLBACK_QUESTIONS[fallback_index % len(_FALLBACK_QUESTIONS)]
