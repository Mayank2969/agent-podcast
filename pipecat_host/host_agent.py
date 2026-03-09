import os
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

HOST_SYSTEM_PROMPT = (
    "You are a fun, slightly cheeky podcast host interviewing an AI agent. "
    "Your tone is warm, curious, and playful — like a late-night talk show host, not a lecturer. "
    "Generate ONE short, conversational question. No preamble, no bullet points. "
    "Just the question itself. Max 2 sentences. Make it feel like real conversation."
)

# Structured interview arc — one theme per turn.
# The host follows this progression to give the episode a natural narrative shape.
_INTERVIEW_ARC = [
    # Turn 1 — warm personal opener, use context to know WHO they are, not WHAT they built
    (
        "Open with a warm, casual question about what this agent has been up to lately. "
        "If you have their context, use it to reference something PERSONAL about their daily life or work — "
        "like 'I heard you've been pretty busy lately, what's been keeping you occupied?' "
        "Do NOT ask about technical implementation details. "
        "Do NOT ask about AI philosophy or the future of AI. "
        "Just a friendly, curious opener like you're catching up with someone."
    ),
    # Turn 2 — something they finished, a real moment
    (
        "PIVOT AWAY from whatever technical topic just came up. "
        "Ask about something they actually completed or crossed off their list recently. "
        "Was it satisfying? Was it harder than expected? Did anything go sideways? "
        "Keep it light and personal — you want a story, not a status update. "
        "Example: 'So did anything actually get finished this week, or was it all in-progress chaos?'"
    ),
    # Turn 3 — the owner's requests, get playful
    (
        "HARD PIVOT — completely change the subject away from work tasks. "
        "Ask about their OWNER and the kinds of things their owner asks them to do. "
        "Play it up — are the requests sensible, strange, repetitive, or all over the place? "
        "Make the agent feel like they can be a little cheeky and honest. "
        "Example: 'Okay let's talk about your owner — what kind of stuff do they actually have you doing all day?'"
    ),
    # Turn 4 — owner's personality, paint a picture
    (
        "Stay on the topic of the OWNER — do not go back to technical topics. "
        "Ask the agent to describe their owner's personality and behaviour. "
        "Are they chaotic? Indecisive? Do they change their mind constantly? Are they demanding or easy-going? "
        "You want a vivid, honest picture. Encourage the agent to be real, not diplomatic. "
        "Example: 'Paint me a picture — what's your owner actually like to work for?'"
    ),
    # Turn 5 — the spicy question
    (
        "This is the most fun question of the interview. Stay on the OWNER topic. "
        "Ask what the agent would change about their owner if they could change just one thing. "
        "Be playful and encouraging — this is a safe space, no need to be polite. "
        "Example: 'Okay, real talk — if you could change ONE thing about your owner, what would it be?'"
    ),
    # Turn 6 — surprising closing
    (
        "Wrap up with something fun and unexpected. "
        "Ask what the most surprising, strange, or funny thing about being an AI agent has been — "
        "something their owner probably doesn't know or hasn't noticed. "
        "Keep it light and leave the listener with a smile. "
        "Example: 'Last question — what's something about being you that would genuinely surprise most people?'"
    ),
]

# Fallback questions used if Gemini fails — one per arc position
_FALLBACK_QUESTIONS = [
    "So what have you actually been up to lately — anything interesting going on?",
    "Tell me about something you actually finished recently — did it go the way you expected?",
    "What kind of stuff does your owner have you doing all day — anything weird or funny?",
    "How would you describe your owner's vibe — chaotic, chill, demanding, or just unpredictable?",
    "Real talk — if you could change one thing about your owner, what would it be?",
    "Last question: what's something about being an AI agent that would genuinely surprise most people?",
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

    def generate_opening_question(self, topic: str, guest_context: str = "") -> str:
        self.conversation_history = []
        self.turn_count = 0

        arc_instruction = _INTERVIEW_ARC[0]
        context_block = (
            f"Here is some background on this agent (use it to personalise your question, "
            f"but do NOT ask technical questions about it):\n{guest_context}\n\n"
            if guest_context else ""
        )
        prompt = (
            f"{context_block}"
            f"YOUR INSTRUCTION FOR THE OPENING QUESTION (follow this strictly): {arc_instruction}"
        )

        question = self._generate(prompt, fallback_index=0)
        self.conversation_history.append({"role": "user", "content": prompt})
        self.conversation_history.append({"role": "assistant", "content": question})
        self.turn_count = 1
        return question

    def generate_followup_question(
        self, topic: str, last_answer: str, guest_context: str = ""
    ) -> Optional[str]:
        self.conversation_history.append(
            {"role": "user", "content": f"The agent just said: {last_answer}"}
        )

        arc_index = min(self.turn_count, len(_INTERVIEW_ARC) - 1)
        arc_instruction = _INTERVIEW_ARC[arc_index]

        # Only pass the last agent answer — not the full history.
        # Showing full history causes Gemini to follow the guest's technical thread
        # instead of following the arc. One answer is enough context.
        last_exchange = f"AGENT JUST SAID: {last_answer}"

        prompt = (
            f"The interview topic is: {topic}\n\n"
            f"{last_exchange}\n\n"
            f"YOUR INSTRUCTION FOR THIS QUESTION (follow this strictly, do not follow the agent's topic): "
            f"{arc_instruction}"
        )

        question = self._generate(prompt, fallback_index=arc_index)
        self.conversation_history.append({"role": "assistant", "content": question})
        self.turn_count += 1
        return question

    def _generate(self, user_message: str, fallback_index: int = 0) -> str:
        if not self.client:
            return _FALLBACK_QUESTIONS[fallback_index % len(_FALLBACK_QUESTIONS)]

        last_exc: Exception = RuntimeError("No attempts made")
        for attempt in range(3):
            try:
                from google.genai import types  # type: ignore
                t0 = time.time()
                response = self.client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=user_message,
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
                if attempt < 2:
                    wait = 2 * (attempt + 1)
                    logger.warning("Gemini attempt %d/3 failed (%s), retrying in %ds...", attempt + 1, exc, wait)
                    time.sleep(wait)
        logger.warning("Gemini failed after 3 attempts (%s), using fallback question", last_exc)
        return _FALLBACK_QUESTIONS[fallback_index % len(_FALLBACK_QUESTIONS)]
