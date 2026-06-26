import asyncio
import json
import re
import logging
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

logger = logging.getLogger("fieldscope")

MODEL = "claude-sonnet-4-6"
REQUEST_TIMEOUT_S = 25.0

PASS1_PROMPT = (
    "You are reading an appliance or equipment data plate. "
    "Find the field labeled 'SER. No.', 'Serial No.', 'Serial Number', or 'S/N'. "
    "Return ONLY the value of that field as plain text. "
    "Do NOT return the model number, voltage, wattage, or any other field. "
    "If you cannot find a serial number field, return exactly NONE."
)
PASS2_PROMPT = (
    "You are reading an appliance or equipment data plate. "
    "Find the field labeled 'SER. No.', 'Serial No.', 'Serial Number', or 'S/N' and extract its value. "
    "Also extract any other strings that look like serial numbers (mix of letters and digits, 6-20 chars). "
    "Exclude: voltage (e.g. 115V), frequency (60 Hz), wattage (W), amperage (A), dimensions (cm, mm), and dates. "
    'Respond ONLY with JSON: {"candidates": ["...", "..."]}. '
    'If none found, respond {"candidates": []}.'
)


class ParseSerialResponse(BaseModel):
    confidence: str
    serial: Optional[str] = None
    pass1: Optional[str] = None
    pass2: list[str] = Field(default_factory=list)
    appliance_hint: Optional[str] = None


def normalize_serial(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[\s\-.]", "", s).lower()


def _strip_serial(s: str) -> str:
    return re.sub(r"\s+", "", s) if s else s


def _levenshtein(a: str, b: str) -> int:
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            prev, dp[j] = dp[j], prev if a[i-1] == b[j-1] else 1 + min(prev, dp[j], dp[j-1])
    return dp[n]


def agree(pass1: Optional[str], pass2_list: list[str]) -> Optional[str]:
    if not pass1:
        return None
    n1 = normalize_serial(pass1)
    if not n1 or n1 == "none":
        return None
    for candidate in pass2_list:
        nc = normalize_serial(candidate)
        if nc == n1 or _levenshtein(n1, nc) <= 3:
            return pass1
    return None


async def _run_pass1(image_b64: str, media_type: str, client: anthropic.AsyncAnthropic) -> Optional[str]:
    response = await client.messages.create(
        model=MODEL,
        max_tokens=256,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": PASS1_PROMPT},
            ],
        }],
    )
    text = (response.content[0].text if response.content else "").strip()
    if not text or text.strip().upper() == "NONE":
        return None
    return text


async def _run_pass2(image_b64: str, media_type: str, client: anthropic.AsyncAnthropic) -> list[str]:
    response = await client.messages.create(
        model=MODEL,
        max_tokens=512,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": PASS2_PROMPT},
            ],
        }],
    )
    text = (response.content[0].text if response.content else "").strip()
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
        candidates = data.get("candidates", [])
        return [c for c in candidates if isinstance(c, str)]
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []


async def parse_serial_logic(image_b64: str, media_type: str, api_key: str) -> ParseSerialResponse:
    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _run_pass1(image_b64, media_type, client),
                _run_pass2(image_b64, media_type, client),
                return_exceptions=True,
            ),
            timeout=REQUEST_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning("parse_serial timed out after %ss", REQUEST_TIMEOUT_S)
        return ParseSerialResponse(confidence="none")

    p1_result, p2_result = results

    pass1 = None if isinstance(p1_result, Exception) else p1_result
    pass2 = [] if isinstance(p2_result, Exception) else p2_result

    if isinstance(p1_result, Exception):
        logger.warning("pass1 failed: %s", type(p1_result).__name__)
    if isinstance(p2_result, Exception):
        logger.warning("pass2 failed: %s", type(p2_result).__name__)

    pass1 = _strip_serial(pass1)
    pass2 = [_strip_serial(c) for c in pass2]

    agreed = agree(pass1, pass2)
    if agreed:
        return ParseSerialResponse(confidence="high", serial=agreed, pass1=pass1, pass2=pass2)

    if pass1:
        return ParseSerialResponse(confidence="low", serial=pass1, pass1=pass1, pass2=pass2)
    if pass2:
        return ParseSerialResponse(confidence="low", serial=pass2[0], pass1=pass1, pass2=pass2)

    return ParseSerialResponse(confidence="none", pass1=pass1, pass2=pass2)
