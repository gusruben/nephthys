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


def submission_from_record(record: dict) -> Submission:
    f = record.get("fields", {})
    return Submission(
        id=record["id"],
        created_at=_time(record.get("createdTime"))
        or datetime.min.replace(tzinfo=timezone.utc),
        title=(f.get("game_title") or "").strip() or "your game",
        jam=f.get("jam") or "",
        code_url=(f.get("code_url") or "").strip().lower().rstrip("/"),
        status=f.get("review_status") or "",
        spotchecked=bool(f.get("augie_spotchecked")),
        dm_queued=bool(f.get("action_send_approved_dm")),
        dm_sent_at=_time(f.get("approved_dm_sent_at")),
        notes=(f.get("notes") or "").strip(),
        playable_url=(f.get("playable_url") or "").strip(),
    )


def order_from_record(record: dict) -> Order:
    f = record.get("fields", {})
    by = f.get("fulfilled_by") or {}
    prize = f.get("prize") or ""
    if not prize and f.get("prize_type") == "indie games":
        prize = "indie games"
    return Order(
        id=record["id"],
        jam=f.get("jam") or "",
        prize=prize or "prize",
        prize_type=f.get("prize_type") or "",
        status=f.get("status") or "not fulfilled",
        info=(f.get("user_fulfillment_info") or "").strip(),
        fulfilled_by_name=by.get("name", "") if isinstance(by, dict) else "",
        fulfilled_by_email=by.get("email", "") if isinstance(by, dict) else "",
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
                timeout=ClientTimeout(total=15),
            ) as res:
                body = await res.json()
                if res.status != 200:
                    raise AirtableError(f"Airtable {res.status}: {body}")
        except AirtableError:
            raise
        except Exception as e:
            raise AirtableError(str(e)) from e
        records += body.get("records", [])
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
    return [submission_from_record(r) for r in records]


async def orders_for(slack_id: str) -> list[Order]:
    if not SLACK_ID.match(slack_id):
        return []
    records = await _list(ORDER_TABLE, f"{{slack_id}}='{slack_id}'", ORDER_FIELDS)
    return [order_from_record(r) for r in records]


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
            timeout=ClientTimeout(total=15),
        ) as res:
            body = await res.json()
            if res.status != 200:
                raise AirtableError(f"Airtable {res.status}: {body}")
        jam = (body.get("records") or [{}])[0].get("fields", {}).get("jam")
    except Exception as e:
        logging.warning(f"Failed to read the jam under review: {e}")
        return _review_jam[1] if _review_jam else None
    if jam:
        _review_jam = (time.monotonic(), jam)
    return jam
