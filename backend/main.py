import json
import logging
import os
import re

from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

from google import genai
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from parse_serial import parse_serial_logic, ParseSerialResponse

ALLOWED_ORIGIN = os.environ.get("SPARK_ALLOWED_ORIGIN", "http://localhost:5500")

VALID_GROUP_IDS = {
    "ig:flooring", "ig:paint", "ig:doors", "ig:pest", "ig:misc",
    "kt:cabinets", "kt:counters", "kt:appliances",
    "ba:vanity", "ba:tub", "ba:tile",
    "as:hvac", "as:electrical", "as:structural", "as:insulation",
    "ex:fence", "ex:siding", "ex:windows", "ex:garage", "ex:trees",
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

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Field Repair Estimator API")
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
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
    logger.info("parse_serial called, media_type=%s", body.media_type)
    return await parse_serial_logic(body.image_b64, body.media_type, GEMINI_API_KEY)


@app.post("/suggest-group", response_model=SuggestGroupResponse)
@limiter.limit("15/minute")
async def suggest_group(request: Request, body: SuggestGroupRequest):
    logger.info("suggest_group called")
    raw_suggestions = await _call_group_suggestion(body.description, GEMINI_API_KEY)
    validated = [g for g in raw_suggestions if g.get("group_id") in VALID_GROUP_IDS]
    return SuggestGroupResponse(suggestions=validated)


async def _call_group_suggestion(description: str, api_key: str) -> list[dict]:
    client = genai.Client(api_key=api_key)
    prompt = GROUP_SUGGEST_PROMPT.format(description=description)
    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=[prompt],
        )
        text = (response.text or "").strip()
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
