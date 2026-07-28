"""Shared parsing for Claude responses that are supposed to be one JSON object.

The deconstruct and copy prompts both say "Return ONLY valid JSON, no preamble or
markdown". Some models honour that literally; others wrap the object in a ```json
fence, and some add a sentence of prose before the fence. Both call sites need the
same tolerance, so it lives here rather than being duplicated per step.
"""
import json
import re

# Non-greedy so the first fenced block wins if a model emits more than one.
_FENCED = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(raw_text):
    """Parse the JSON object out of a model response. Raises ValueError (json's
    JSONDecodeError) if no JSON can be recovered."""
    text = (raw_text or "").strip()
    fenced = _FENCED.search(text)
    if fenced:
        text = fenced.group(1).strip()
    else:
        # No complete fence: narrow to the outermost braces so a leading sentence
        # of prose, or an unterminated opening fence, doesn't fail at char 0.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    return json.loads(text)
