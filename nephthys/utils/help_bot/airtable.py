# Read-only lookups in the jame gam Airtable base.
#
# The bot only ever reads: submissions and shop orders for one Slack ID, and a
# count of games still waiting for review. Table IDs (not names) so a rename in
# Airtable can't break it.
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from urllib.parse import quote

from aiohttp import ClientTimeout

from nephthys.utils.env import env

SUBMISSION_TABLE = "tblmnQSVJBu5EBoVY"
ORDER_TABLE = "tblqVjQ3ROSx7OuAq"
API = "https://api.airtable.com/v0"
# Per request. Each lookup is one or two requests, run alongside the AI call.
TIMEOUT_SECONDS = 8
SLACK_ID = re.compile(r"^[UW][A-Z0-9]{5,}$")

SUBMISSION_FIELDS = [
    "game_title",
    "jam",
    "code_url",
    "playable_url",
    "review_status",
    "augie_spotchecked",
    "action_send_approved_dm",
    "approved_dm_sent_at",
    "notes",
]
ORDER_FIELDS = [
    "jam",
    "prize",
    "prize_type",
    "games",
    "status",
    "user_fulfillment_info",
    "fulfilled_by",
    "fulfilled_dm_sent_at",
]


class AirtableError(Exception):
    """Airtable couldn't be read, so the bot must not guess."""


@dataclass
class Submission:
    id: str
    created_at: datetime
    title: str
    jam: str
    code_url: str
    status: str
    spotchecked: bool
    dm_queued: bool
    dm_sent_at: datetime | None
    notes: str
    # The game's itch page
    playable_url: str = ""


@dataclass
class Order:
    id: str
    jam: str
    prize: str
    prize_type: str
    status: str
    info: str
    fulfilled_by_name: str
    fulfilled_by_email: str
    fulfilled_dm_sent_at: datetime | None


def _time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _text(value) -> str:
    """A text field, whatever shape Airtable sends it in."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0].strip()
    return ""


def submission_from_record(record: dict) -> Submission:
    f = record.get("fields") or {}
    return Submission(
        id=record["id"],
        created_at=_time(record.get("createdTime"))
        or datetime.min.replace(tzinfo=timezone.utc),
        title=_text(f.get("game_title")) or "your game",
        jam=_text(f.get("jam")),
        code_url=_text(f.get("code_url")).lower().rstrip("/"),
        status=_text(f.get("review_status")),
        spotchecked=bool(f.get("augie_spotchecked")),
        dm_queued=bool(f.get("action_send_approved_dm")),
        dm_sent_at=_time(f.get("approved_dm_sent_at")),
        notes=_text(f.get("notes")),
        playable_url=_text(f.get("playable_url")),
    )


def order_from_record(record: dict) -> Order:
    f = record.get("fields") or {}
    by = f.get("fulfilled_by") or {}
    prize = _text(f.get("prize"))
    if not prize and _text(f.get("prize_type")) == "indie games":
        prize = "indie games"
    return Order(
        id=record["id"],
        jam=_text(f.get("jam")),
        prize=prize or "prize",
        prize_type=_text(f.get("prize_type")),
        status=_text(f.get("status")) or "not fulfilled",
        info=_text(f.get("user_fulfillment_info")),
        fulfilled_by_name=_text(by.get("name")) if isinstance(by, dict) else "",
        fulfilled_by_email=_text(by.get("email")) if isinstance(by, dict) else "",
        fulfilled_dm_sent_at=_time(f.get("fulfilled_dm_sent_at")),
    )


def order_url(order: Order) -> str:
    return f"https://airtable.com/{env.jamegam_airtable_base}/{ORDER_TABLE}/{order.id}"


async def _list(table: str, formula: str, fields: list[str]) -> list[dict]:
    token = env.jamegam_airtable_token
    if not token:
        raise AirtableError("JAMEGAM_AIRTABLE_TOKEN is not set")
    records: list[dict] = []
    offset = None
    for _ in range(20):  # 2000 rows is far more than any of these return
        params = [("filterByFormula", formula), ("pageSize", "100")]
        params += [("fields[]", f) for f in fields]
        if offset:
            params.append(("offset", offset))
        query = "&".join(f"{k}={quote(v)}" for k, v in params)
        url = f"{API}/{env.jamegam_airtable_base}/{table}?{query}"
        try:
            async with env.session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=ClientTimeout(total=TIMEOUT_SECONDS),
            ) as res:
                body = await res.json()
                if res.status != 200:
                    raise AirtableError(f"Airtable {res.status}: {body}")
        except AirtableError:
            raise
        except Exception as e:
            raise AirtableError(str(e)) from e
        if not isinstance(body, dict) or not isinstance(body.get("records", []), list):
            raise AirtableError(f"unexpected Airtable reply: {str(body)[:200]}")
        records += [r for r in body.get("records", []) if isinstance(r, dict)]
        offset = body.get("offset")
        if not offset:
            break
    return records


async def submissions_for(slack_id: str) -> list[Submission]:
    if not SLACK_ID.match(slack_id):
        return []
    records = await _list(
        SUBMISSION_TABLE, f"{{slack_id}}='{slack_id}'", SUBMISSION_FIELDS
    )
    try:
        return [submission_from_record(r) for r in records]
    except Exception as e:
        raise AirtableError(f"couldn't read a submission row: {e}") from e


async def orders_for(slack_id: str) -> list[Order]:
    if not SLACK_ID.match(slack_id):
        return []
    records = await _list(ORDER_TABLE, f"{{slack_id}}='{slack_id}'", ORDER_FIELDS)
    try:
        return [order_from_record(r) for r in records]
    except Exception as e:
        raise AirtableError(f"couldn't read an order row: {e}") from e


_review_jam: tuple[float, str] | None = None


async def review_jam() -> str | None:
    """The jam under review: the newest `jam` on any submission. Cached for
    ten minutes. None if Airtable can't be read."""
    global _review_jam
    if _review_jam and time.monotonic() - _review_jam[0] < 600:
        return _review_jam[1]
    token = env.jamegam_airtable_token
    if not token:
        return None
    query = "&".join(
        f"{k}={quote(v)}"
        for k, v in [
            ("fields[]", "jam"),
            ("filterByFormula", "NOT({jam}='')"),
            ("sort[0][field]", "jam"),
            ("sort[0][direction]", "desc"),
            ("maxRecords", "1"),
        ]
    )
    url = f"{API}/{env.jamegam_airtable_base}/{SUBMISSION_TABLE}?{query}"
    try:
        async with env.session.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=ClientTimeout(total=TIMEOUT_SECONDS),
        ) as res:
            body = await res.json()
            if res.status != 200:
                raise AirtableError(f"Airtable {res.status}: {body}")
        jam = _text((body.get("records") or [{}])[0].get("fields", {}).get("jam"))
    except Exception as e:
        logging.warning(f"Failed to read the jam under review: {e}")
        return _review_jam[1] if _review_jam else None
    if jam:
        _review_jam = (time.monotonic(), jam)
    return jam or None
