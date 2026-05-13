"""
Gemini summarisation client.

Used when free-text fields (currently patient symptoms) exceed the corresponding
Salesforce field's length limit. Raises SummarizationError on any failure so
callers can decide whether to reject the write or fall back.
"""
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "gemini-2.5-flash"

_configured = False


class SummarizationError(Exception):
    """Raised when Gemini cannot produce a usable summary."""


def _ensure_configured() -> None:
    global _configured
    if _configured:
        return
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SummarizationError(
            "GEMINI_API_KEY is not set. Add it to backend/.env to enable "
            "symptom summarisation for Salesforce sync."
        )
    genai.configure(api_key=api_key)
    _configured = True


def summarize_to_length(text: str, max_length: int, *, context: str = "patient symptoms") -> str:
    """
    Summarise `text` so that the result is <= max_length characters.

    Clinically important detail (severity, location, duration, red-flag terms)
    is preserved by the prompt; the result is hard-capped to max_length as a
    safety net in case the model overshoots.
    """
    if max_length <= 0:
        raise SummarizationError(f"Invalid max_length: {max_length}")
    if not text:
        return text
    if len(text) <= max_length:
        return text

    _ensure_configured()

    model_name = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    prompt = (
        f"You are summarising {context} for a clinical record. "
        f"Rewrite the text below so it fits in {max_length} characters or fewer. "
        "Preserve severity, location, duration, and any red-flag terms "
        "(chest pain, bleeding, shortness of breath, etc.). "
        "Output only the summary text. No quotes, no prefixes like 'Summary:'.\n\n"
        f"Text:\n{text}"
    )

    try:
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
    except Exception as e:
        raise SummarizationError(f"Gemini API call failed: {e}") from e

    summary = (getattr(response, "text", None) or "").strip()
    if not summary:
        raise SummarizationError("Gemini returned an empty response.")

    if len(summary) > max_length:
        summary = summary[:max_length].rstrip()

    return summary
