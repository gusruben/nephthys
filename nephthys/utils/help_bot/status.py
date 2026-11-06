# Turns someone's Airtable rows into a status reply, or decides a human
# should handle it.
#
# This isn't a status checker. It catches people asking or complaining about
# their game in the jam under review (the newest jam with submissions), and
# only says one of three things: not reviewed yet, approved with the prize DM
# coming, or the prize DM already went out. Anything else, including anything
# about an older jam, is a normal ticket; the ticket post still shows what the
# bot found so helpers have it.
#
# Pure functions over the rows (no Slack, no network) so every branch has a
# unit test. From the jame gam pipeline (jamegam CLAUDE.md, ari's bridge.ts and
# its prize DM tab):
#
# - Approved and "Prize Only" both open the shop and both wait on Augie's
#   spotcheck before the prize DM. To the submitter both are just "approved".
# - "No Prize" is a rejection as far as the submitter knows.
# - Rejections are never posted in a thread.
import re
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime

from nephthys.utils.env import env
from nephthys.utils.help_bot.airtable import Order
from nephthys.utils.help_bot.airtable import Submission
from nephthys.utils.help_bot.jam_config import et_date_text
from nephthys.utils.help_bot.jam_config import et_text
from nephthys.utils.help_bot.jam_config import JamConfig

SHOP_STATUSES = {"Approved", "Prize Only"}
REJECTED_STATUSES = {"Rejected", "No Prize"}
WITHDRAWN = "Withdrawn"
# The detailed states the bot can answer, and which of its three replies each
# gets. Approved but not spotchecked is still in review as far as the asker
# knows (the spotcheck can change the outcome), so it never says approved.
REPLY_FOR_STATE = {
    "unreviewed": "status_unreviewed",
    "approved_checking": "status_unreviewed",
    "approved_ready": "status_approved",
    "approved_queued": "status_approved",
    "approved_dm_sent": "status_dm_sent",
}
URL = re.compile(r"https?://\S+")
# Something that looks like a carrier tracking number
TRACKING_NUMBER = re.compile(r"\b(?=[A-Z0-9]*\d)[A-Z0-9]{10,34}\b")
# Prizes that never ship in a box: codes, gift cards, licenses, grants
DIGITAL_HINTS = re.compile(
    r"\b(code|codes|gift ?card|license|licence|key|steam|email|e-mail|inbox|grant|hcb|dms?|direct message)\b",
    re.IGNORECASE,
)


@dataclass
class Outcome:
    """What the bot does with a status or tracking question.

    `answer` is the public thread reply; when it is None the ticket is left
    for a human. `resolve` means the bot resolves it (no helper credit) and the
    thread deletes itself later unless reopened."""

    summary: str
    action: str
    reason: str | None = None
    answer: str | None = None
    private: str | None = None
    resolve: bool = False
    ping_order: Order | None = None
    extra: dict = field(default_factory=dict)


def _copy():
    copy = env.transcript.help_bot
    if not copy:
        raise RuntimeError("help_bot copy is not configured for this program")
    return copy


def row_state(row: Submission) -> str:
    s = row.status
    if not s:
        return "unreviewed"
    if s in SHOP_STATUSES:
        if row.dm_sent_at:
            return "approved_dm_sent"
        if not row.spotchecked:
            return "approved_checking"
        if row.dm_queued:
            return "approved_queued"
        return "approved_ready"
    return {
        "Pending": "pending",
        "Needs Augie": "needs_augie",
        "Rejected": "rejected",
        "No Prize": "no_prize",
        "Stickers Only": "stickers_only",
        WITHDRAWN: "withdrawn",
    }.get(s, "other")


def _note(row: Submission) -> str:
    """The latest line of the row's notes, short enough for one line."""
    lines = [line.strip() for line in row.notes.splitlines() if line.strip()]
    note = lines[-1] if lines else "none"
    return note if len(note) <= 200 else note[:197] + "..."


def state_label(row: Submission) -> str:
    copy = _copy()
    state = row_state(row)
    return copy.ticket_post_states[state].format(
        date=et_date_text(row.dm_sent_at) if row.dm_sent_at else "",
        note=_note(row),
        status=row.status,
    )


def game_text(row: Submission) -> str:
    """The game's title, linked to its itch page when the row has one."""
    copy = _copy()
    title = row.title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    url = row.playable_url
    if url.startswith(("https://", "http://")) and "|" not in url and ">" not in url:
        return copy.game_link.format(itch_url=url, title=title.replace("|", "/"))
    return copy.game_no_link.format(title=title)


def _group_key(row: Submission) -> str:
    return row.code_url or row.title.lower()


def relevant_rows(rows: list[Submission]) -> list[Submission]:
    """Their rows from the newest jam they submitted to, without withdrawn
    copies, and with exact duplicates (same game, same state) folded."""
    live = [r for r in rows if r.status != WITHDRAWN]
    if not live:
        return []
    newest = max(r.jam for r in live)
    seen: set[tuple[str, str]] = set()
    out = []
    for row in sorted(live, key=lambda r: r.created_at):
        if row.jam != newest:
            continue
        key = (_group_key(row), row_state(row))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def has_conflict(rows: list[Submission]) -> bool:
    """Two rows for the same game that say different things."""
    states: dict[str, set[str]] = {}
    for row in rows:
        states.setdefault(_group_key(row), set()).add(row_state(row))
    return any(len(s) > 1 for s in states.values())


def submission_summary(
    rows: list[Submission], orders: list[Order], config: JamConfig | None
) -> str:
    """The ticket post's one line about this person."""
    copy = _copy()
    shown = relevant_rows(rows) or [r for r in rows]
    if not shown:
        return copy.ticket_post_no_submission
    jam = max(r.jam for r in shown)
    jam_name = config.jam_name(jam) if config else jam
    parts = [f"{game_text(r)} {state_label(r)}" for r in shown if r.jam == jam]
    for order in orders:
        if order.jam == jam:
            parts.append(
                copy.ticket_post_states["order"].format(
                    prize=order.prize, status=order.status
                )
            )
    return copy.ticket_post_submission.format(
        summary=f"{jam_name} ({jam}): " + "; ".join(parts)
    )


def _when(delete_after: datetime) -> str:
    return et_text(delete_after)


def _human(summary: str, reason: str, **kwargs) -> Outcome:
    copy = _copy()
    text = copy.ticket_post_reasons[reason].format(**kwargs)
    return Outcome(
        summary=summary,
        action=copy.ticket_post_actions["human"].format(reason=text),
        reason=reason,
    )


def _order_line(order: Order) -> str | None:
    copy = _copy()
    if order.status == "not fulfilled":
        return copy.order_not_fulfilled
    if order.status == "processing":
        return copy.order_processing
    if order.status == "held for id verification":
        return copy.order_held_idv
    if order.status == "fulfilled":
        return copy.order_fulfilled.format(date=_order_sent(order))
    return None


def status_outcome(
    rows: list[Submission],
    orders: list[Order],
    config: JamConfig | None,
    review_jam: str | None,
    delete_after: datetime,
) -> Outcome:
    """`review_jam` is the jam under review: the newest jam anyone has
    submitted to. Without it (Airtable couldn't be read) a human answers."""
    copy = _copy()
    summary = submission_summary(rows, orders, config)
    if not review_jam:
        return _human(summary, "airtable")
    if not rows:
        return _human(summary, "no_rows")
    live = [r for r in rows if r.status != WITHDRAWN]
    if not live:
        return _human(summary, "only_withdrawn")
    current = [r for r in relevant_rows(live) if review_jam and r.jam == review_jam]
    if not current:
        return _human(summary, "not_current", jam=review_jam or "unknown")
    if has_conflict(current):
        return _human(summary, "conflict")
    if any(row_state(r) not in REPLY_FOR_STATE for r in current):
        return _human(summary, "unclear")

    lines = [(r, getattr(copy, REPLY_FOR_STATE[row_state(r)])) for r in current]
    if len(lines) == 1:
        row, line = lines[0]
        body = copy.status_intro_one.format(game=game_text(row), line=line)
    else:
        body = copy.status_intro_many.format(
            lines="\n".join(
                copy.status_line_many.format(game=game_text(row), line=line)
                for row, line in lines
            )
        )
    return Outcome(
        summary=summary,
        action=copy.ticket_post_actions["status"].format(when=_when(delete_after)),
        answer=body,
        resolve=True,
    )


_FILLER = {"the", "a", "my", "hack", "club", "prize", "jam", "game", "new"}


def _words(text: str) -> set[str]:
    text = text.lower().replace("\u00e5", "a").replace("t-shirt", "shirt")
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", text)} - _FILLER


def matches_item(order: Order, item: str) -> bool:
    """Whether the order is the prize they named ("blahaj" matches "blahaj
    plushie", "hack club shirt" matches "hack club t-shirt")."""
    asked, have = _words(item), _words(order.prize)
    # "plushie" is a "plush", "controllers" a "controller"
    return any(
        a == h or (min(len(a), len(h)) >= 4 and (a.startswith(h) or h.startswith(a)))
        for a in asked
        for h in have
    )


def has_tracking(order: Order) -> bool:
    """Whether the fulfillment note gives them a way to track or receive it:
    a link or tracking number, or a digital prize that was sent another way."""
    info = order.info
    if not info:
        return False
    if URL.search(info) or TRACKING_NUMBER.search(info):
        return True
    # "will send tracking when i have it" / "dm me for the tracking link"
    if re.search(r"\btracking\b", info, re.IGNORECASE):
        return False
    return order.prize_type == "indie games" or bool(DIGITAL_HINTS.search(info))


def _order_sent(order: Order) -> str:
    sent = order.fulfilled_dm_sent_at
    return et_date_text(sent) if sent else "a while back"


def tracking_outcome(
    rows: list[Submission],
    orders: list[Order],
    config: JamConfig | None,
    delete_after: datetime,
    item: str | None = None,
) -> Outcome:
    """Answer for the order they asked about: the one matching the prize they
    named, or else their orders from the newest jam they ordered in."""
    copy = _copy()
    summary = submission_summary(rows, orders, config)
    live = [o for o in orders if o.status != "canceled"]
    if not live:
        return _human(summary, "no_order")
    if item:
        recent = [o for o in live if matches_item(o, item)]
        if not recent:
            return _human(summary, "no_matching_order", item=item)
    else:
        newest = max(o.jam for o in live)
        recent = [o for o in live if o.jam == newest]

    public: list[str] = []
    private: list[str] = []
    to_ping: Order | None = None
    for order in sorted(recent, key=lambda o: o.jam, reverse=True):
        if order.status in {"not fulfilled", "processing", "held for id verification"}:
            public.append(
                copy.tracking_not_shipped.format(
                    prize=order.prize, order=_order_line(order)
                )
            )
        elif order.status != "fulfilled":
            return _human(summary, "order_unclear")
        elif has_tracking(order):
            public.append(
                copy.tracking_info_public.format(
                    prize=order.prize, date=_order_sent(order)
                )
            )
            private.append(
                copy.tracking_info_private.format(
                    prize=order.prize, info=order.info.replace("\n", "\n> ")
                )
            )
        else:
            if to_ping or not (order.fulfilled_by_email or order.fulfilled_by_name):
                return _human(summary, "no_tracking_contact")
            to_ping = order
            public.append(
                copy.tracking_pinged_public.format(
                    prize=order.prize, date=_order_sent(order)
                )
            )

    answer = "\n".join(public)
    if to_ping:
        return Outcome(
            summary=summary,
            action="",  # filled in once we know who got pinged
            answer=answer,
            private="\n\n".join(private) or None,
            ping_order=to_ping,
            extra={"sent": _order_sent(to_ping)},
        )
    action = "tracking_info" if private else "status"
    return Outcome(
        summary=summary,
        action=copy.ticket_post_actions[action].format(when=_when(delete_after)),
        answer=answer,
        private="\n\n".join(private) or None,
        resolve=True,
    )
