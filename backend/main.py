import json
import logging
import os
import re

from dotenv import load_dotenv
load_dotenv()

import anthropic
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from parse_serial import parse_serial_logic, ParseSerialResponse

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "http://localhost:5050")

VALID_GROUP_IDS = {
    "ig:flooring", "ig:paint", "ig:doors", "ig:pest", "ig:misc",
    "kt:cabinets", "kt:counters", "kt:appliances",
    "ba:vanity", "ba:tub", "ba:tile",
    "as:hvac", "as:electrical", "as:structural", "as:insulation",
    "ex:fence", "ex:siding", "ex:windows", "ex:garage", "ex:trees",
    "bd:closet", "lv:lighting",
}

GROUP_SUGGEST_PROMPT = """You are helping a property inspector categorize repair observations.
The available repair groups are:
  ig:flooring — Flooring
  ig:paint — Paint & Wall Repair
  ig:doors — Doors & Trim
  ig:pest — Pest Control
  ig:misc — Misc & Finish
  kt:cabinets — Cabinets
  kt:counters — Countertops & Tile
  kt:appliances — Appliances
  ba:vanity — Vanity & Countertop
  ba:tub — Tub & Shower
  ba:tile — Tile
  as:hvac — HVAC
  as:electrical — Electrical
  as:structural — Structural
  as:insulation — Insulation & Drywall
  ex:fence — Fence
  ex:siding — Siding
  ex:windows — Windows
  ex:garage — Garage
  ex:trees — Trees
  bd:closet — Bedroom Closet
  lv:lighting — Living Area Lighting

The inspector has described: "{description}"

If the description is not about property damage or repair work, return an empty array [].
Return a JSON array of the most relevant group IDs, ordered by confidence. Maximum 3 groups.
Example: ["as:hvac", "as:electrical"]
Return ONLY the JSON array. No explanation."""


class _StripImageFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "image_b64" in msg and len(msg) > 500:
            record.msg = "<request body containing image_b64 redacted>"
            record.args = ()
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("fieldscope")
logger.addFilter(_StripImageFilter())

def client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

limiter = Limiter(key_func=client_ip)
app = FastAPI(title="Field Repair Estimator API")
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "X-Api-Key"],
    allow_credentials=False,
)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})


class ParseSerialRequest(BaseModel):
    image_b64: str = Field(..., max_length=11_000_000)
    media_type: str = Field("image/jpeg", pattern=r"^image/(jpeg|png|webp)$")


class SuggestGroupRequest(BaseModel):
    description: str = Field(..., max_length=2000)


class SuggestGroupResponse(BaseModel):
    suggestions: list[dict]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/parse-serial", response_model=ParseSerialResponse)
@limiter.limit("10/minute")
async def parse_serial(request: Request, body: ParseSerialRequest):
    api_key = request.headers.get("X-Api-Key", "").strip()
    if not api_key:
        return JSONResponse(status_code=401, content={"detail": "Anthropic API key required"})
    logger.info("parse_serial called, media_type=%s", body.media_type)
    return await parse_serial_logic(body.image_b64, body.media_type, api_key)


@app.post("/suggest-group", response_model=SuggestGroupResponse)
@limiter.limit("15/minute")
async def suggest_group(request: Request, body: SuggestGroupRequest):
    api_key = request.headers.get("X-Api-Key", "").strip()
    if not api_key:
        return JSONResponse(status_code=401, content={"detail": "Anthropic API key required"})
    logger.info("suggest_group called")
    raw_suggestions = await _call_group_suggestion(body.description, api_key)
    validated = [g for g in raw_suggestions if g.get("group_id") in VALID_GROUP_IDS]
    return SuggestGroupResponse(suggestions=validated)


async def _call_group_suggestion(description: str, api_key: str) -> list[dict]:
    client = anthropic.AsyncAnthropic(api_key=api_key)
    prompt = GROUP_SUGGEST_PROMPT.format(description=description)
    try:
        response = await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (response.content[0].text if response.content else "").strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        group_ids = json.loads(match.group(0))
        return [
            {"group_id": gid, "label": gid}
            for gid in group_ids
            if isinstance(gid, str)
        ]
    except Exception as exc:
        logger.warning("suggest_group failed: %s", type(exc).__name__)
        return []
