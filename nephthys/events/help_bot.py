# The jame gam help bot: answers new tickets from the FAQ canvas, the live jam
# config or the asker's own Airtable rows, and leaves everything else for a
# human.
#
# Only runs in help channels listed in JAMEGAM_NEW_FLOW_CHANNELS (see
# env.new_flow_enabled). Everywhere else tickets behave exactly as before.
import asyncio
import logging
import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

from blockkit import Actions
from blockkit import Button
from blockkit import Context
from blockkit import Section
from blockkit import Text
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.database.enums import TicketStatus
from nephthys.database.tables import BotMessage
from nephthys.database.tables import HelpBotTicket
from nephthys.database.tables import Ticket
from nephthys.database.tables import User
from nephthys.utils import faq
from nephthys.utils.env import env
from nephthys.utils.faq import faq_link_label
from nephthys.utils.faq import faq_section_url
from nephthys.utils.help_bot import airtable
from nephthys.utils.help_bot.classify import Classification
from nephthys.utils.help_bot.classify import classify
from nephthys.utils.help_bot.jam_config import get_jam_config
from nephthys.utils.help_bot.jam_config import jam_facts_text
from nephthys.utils.help_bot.jam_config import JamConfig
from nephthys.utils.help_bot.status import Outcome
from nephthys.utils.help_bot.status import status_outcome
from nephthys.utils.help_bot.status import submission_summary
from nephthys.utils.help_bot.status import tracking_outcome
from nephthys.utils.logging import send_heartbeat
from nephthys.utils.ticket_methods import delete_message
from nephthys.utils.ticket_methods import get_question_message_link
from nephthys.utils.ticket_methods import ThreadGoneError

ANSWERED_ACTION = "help-bot-answered"
NEED_HELP_ACTION = "help-bot-need-help"
IMPERSONATE = re.compile(r"\[as (U[A-Z0-9]{6,})\]")


async def help_bot_context_lines(msg_ts: str) -> list[str]:
    """Extra lines for a ticket's post in the tickets channel: the asker's
    submission state, what the bot did, and whether they came back."""
    copy = env.transcript.help_bot
    if not copy:
        return []
    info = (
        await HelpBotTicket.objects()
        .where(HelpBotTicket.ticket.msg_ts == msg_ts)
        .first()
    )
    if not info:
        return []
    lines = []
    if info.submission_summary:
        lines.append(info.submission_summary)
    if info.action_summary:
        lines.append(copy.ticket_post_bot.format(what=info.action_summary))
    if info.still_need_help:
        lines.append(copy.ticket_post_still_need_help)
    if info.reopened:
        lines.append(copy.ticket_post_reopened)
    return lines


def lookup_slack_id(text: str, author_id: str, author_is_helper: bool) -> str:
    """Whose rows to look up. In the test channel a helper can write
    "[as U123]" to see what someone else would get."""
    if (
        env.jamegam_test_impersonation
        and author_is_helper
        and env.slack_help_channel == env.jamegam_test_channel
        and (match := IMPERSONATE.search(text))
    ):
        return match.group(1)
    return author_id


async def run_help_bot(
    ticket: Ticket, text: str, author_id: str, client: AsyncWebClient
) -> None:
    """Called once a new ticket exists. Never raises: a failure here just
    leaves a normal ticket."""
    if not env.new_flow_enabled():
        return
    try:
        await _run_help_bot(ticket, text, author_id, client)
    except ThreadGoneError:
        logging.warning(f"Help bot: thread gone for ticket_id={ticket.id}")
    except Exception as e:
        logging.error(f"Help bot failed ticket_id={ticket.id} error={e}", exc_info=True)
        await send_heartbeat(
            f"Help bot failed on ticket {ticket.id}: {e}",
            messages=[f"Ticket TS: {ticket.msg_ts}"],
        )


async def _fetch_rows(slack_id: str):
    try:
        rows, orders = await asyncio.gather(
            airtable.submissions_for(slack_id), airtable.orders_for(slack_id)
        )
        return rows, orders, None
    except airtable.AirtableError as e:
        logging.error(f"Help bot couldn't read Airtable slack_id={slack_id} error={e}")
        return [], [], e


async def _run_help_bot(
    ticket: Ticket, text: str, author_id: str, client: AsyncWebClient
) -> None:
    copy = env.transcript.help_bot
    assert copy
    author = await User.objects().where(User.slack_id == author_id).first()
    slack_id = lookup_slack_id(text, author_id, bool(author and author.helper))
    question = IMPERSONATE.sub("", text).strip()

    config, review_jam = await asyncio.gather(get_jam_config(), airtable.review_jam())
    (rows, orders, airtable_error), result = await asyncio.gather(
        _fetch_rows(slack_id),
        classify(question, faq.faq_entries, jam_context(config, review_jam)),
    )
    info = HelpBotTicket(ticket=ticket.id, route=result.route)
    if not airtable_error:
        info.submission_summary = submission_summary(rows, orders, config)
    if result.reason:
        info.action_summary = copy.ticket_post_actions["human"].format(
            reason=copy.ticket_post_reasons[result.reason]
        )

    now = datetime.now(timezone.utc)
    delete_after = now + timedelta(minutes=env.jamegam_delete_after_minutes)

    if result.route == "faq" and result.faq:
        entry = result.faq
        info.faq_question = entry.question
        info.action_summary = copy.ticket_post_actions["faq"].format(
            question=entry.question
        )
        reply = copy.faq_answer.format(
            url=faq_section_url(entry),
            question=faq_link_label(entry),
            # The template quotes the answer, so every line needs the quote
            answer=entry.answer.replace("\n", "\n> "),
        )
        info.answer_ts = await post_answer_with_buttons(ticket, reply, client)

    elif result.route == "jam_facts":
        reply = jam_facts_text(config, result.facts) if config else None
        if reply:
            info.action_summary = copy.ticket_post_actions["jam_facts"]
            # Unfurl the itch page when the answer links it
            info.answer_ts = await post_answer_with_buttons(
                ticket, reply, client, unfurl="jam_dates" in result.facts
            )
        else:
            info.route = "human"

    elif result.route in ("status", "tracking"):
        if airtable_error:
            reason = copy.ticket_post_reasons["airtable"]
            info.action_summary = copy.ticket_post_actions["human"].format(
                reason=reason
            )
        else:
            outcome = _status_or_tracking(
                result, rows, orders, config, review_jam, delete_after
            )
            await act_on_outcome(ticket, info, outcome, author_id, delete_after, client)

    await info.save()
    await refresh_ticket_post(ticket, client)


def jam_context(config: JamConfig | None, review_jam: str | None) -> str:
    """Tells the classifier which jam is under review, so it can tell when a
    post is about an older one."""
    if not review_jam:
        return ""
    name = config.jam_name(review_jam) if config else review_jam
    earlier = (
        [f"{v} ({k})" for k, v in config.shop.cycles.items() if k < review_jam]
        if config
        else []
    )
    text = f"The jam under review is {name} ({review_jam})."
    if earlier:
        text += " Earlier jams: " + ", ".join(earlier) + ", and older ones."
    return text


def _status_or_tracking(
    result: Classification, rows, orders, config, review_jam, delete_after
) -> Outcome:
    if result.route == "tracking" and orders:
        return tracking_outcome(rows, orders, config, delete_after, item=result.item)
    return status_outcome(rows, orders, config, review_jam, delete_after)


async def act_on_outcome(
    ticket: Ticket,
    info: HelpBotTicket,
    outcome: Outcome,
    asker_id: str,
    delete_after: datetime,
    client: AsyncWebClient,
) -> None:
    copy = env.transcript.help_bot
    assert copy
    info.submission_summary = outcome.summary
    info.action_summary = outcome.action
    if not outcome.answer:
        return
    if outcome.ping_order:
        fulfiller = await _fulfiller_mention(outcome.ping_order)
        await client.chat_postMessage(
            channel=env.slack_ticket_channel,
            thread_ts=ticket.ticket_ts,
            text=copy.tracking_ping_team.format(
                fulfiller=fulfiller,
                asker=asker_id,
                prize=outcome.ping_order.prize,
                date=outcome.extra.get("sent", ""),
                order_url=airtable.order_url(outcome.ping_order),
                thread_url=get_question_message_link(ticket),
            ),
        )
        info.action_summary = copy.ticket_post_actions["tracking_ping"].format(
            fulfiller=fulfiller
        )
        info.answer_ts = await post_reply(
            ticket, outcome.answer, client, blocks=_answer_blocks(outcome.answer)
        )
        await _send_private(ticket, asker_id, outcome.private, client)
        return

    if outcome.resolve:
        info.answer_ts = await post_reply(
            ticket,
            outcome.answer,
            client,
            blocks=[
                *_answer_blocks(outcome.answer),
                Context(elements=[Text(copy.status_footer)]),
                Actions(
                    elements=[
                        Button(
                            text=copy.reopen_button,
                            action_id="reopen-button",
                            value=f"{ticket.id}",
                        )
                    ]
                ),
            ],
        )
        await _send_private(ticket, asker_id, outcome.private, client)
        await bot_resolve(ticket, client)
        info.auto_resolved = True
        info.delete_after = delete_after
    else:
        info.answer_ts = await post_reply(
            ticket, outcome.answer, client, blocks=_answer_blocks(outcome.answer)
        )


def _answer_blocks(text: str) -> list:
    """An answer the bot gives on its own, with the line saying it's the bot."""
    copy = env.transcript.help_bot
    assert copy
    return [Section(text), Context(elements=[Text(copy.bot_note)])]


async def _send_private(
    ticket: Ticket, asker_id: str, text: str | None, client: AsyncWebClient
) -> None:
    """Details only the asker should see (they can include an email)."""
    if text:
        await client.chat_postEphemeral(
            channel=env.slack_help_channel,
            thread_ts=ticket.msg_ts,
            user=asker_id,
            text=text,
        )


async def _fulfiller_mention(order: airtable.Order) -> str:
    """<@U123> for whoever marked the order fulfilled, or their name."""
    if order.fulfilled_by_email:
        try:
            res = await env.slack_client.users_lookupByEmail(
                email=order.fulfilled_by_email
            )
            user: dict = res.get("user") or {}
            if user.get("id"):
                return f"<@{user['id']}>"
        except SlackApiError as e:
            logging.warning(
                f"Couldn't find fulfiller on Slack email={order.fulfilled_by_email} error={e.response.get('error')}"
            )
    return order.fulfilled_by_name or "whoever sent it"


async def post_reply(
    ticket: Ticket,
    text: str,
    client: AsyncWebClient,
    blocks: list | None = None,
    unfurl: bool = False,
) -> str:
    """Like reply_to_ticket, but returns the message's ts."""
    response = await client.chat_postMessage(
        channel=env.slack_help_channel,
        thread_ts=ticket.msg_ts,
        text=text,
        blocks=[b.build() for b in blocks] if blocks else None,
        unfurl_links=unfurl,
        unfurl_media=unfurl,
    )
    msg: dict = response["message"]  # type: ignore (assuming message exists)
    ts = msg.get("ts")
    if not ts:
        raise ValueError(f"Bot message has no ts: {msg}")
    if not msg.get("thread_ts"):
        await client.chat_delete(channel=env.slack_help_channel, ts=ts)
        raise ThreadGoneError()
    await BotMessage(ts=ts, channel_id=env.slack_help_channel, ticket=ticket.id).save()
    return ts


def answer_buttons(ticket_id: int) -> Actions:
    copy = env.transcript.help_bot
    assert copy
    return Actions(
        elements=[
            Button(
                text=copy.faq_answered_button,
                action_id=ANSWERED_ACTION,
                value=f"{ticket_id}",
                style="primary",
            ),
            Button(
                text=copy.faq_still_need_help_button,
                action_id=NEED_HELP_ACTION,
                value=f"{ticket_id}",
            ),
        ]
    )


async def post_answer_with_buttons(
    ticket: Ticket, text: str, client: AsyncWebClient, unfurl: bool = False
) -> str:
    return await post_reply(
        ticket,
        text,
        client,
        blocks=[*_answer_blocks(text), answer_buttons(ticket.id)],
        unfurl=unfurl,
    )


async def bot_resolve(ticket: Ticket, client: AsyncWebClient) -> None:
    """Resolve a ticket the bot answered itself. Nobody gets credit for it
    (closed_by stays empty) and the ticket post stays up, so helpers can see
    what the bot said, until the thread is deleted."""
    await Ticket.update(
        {
            Ticket.status: TicketStatus.CLOSED,
            Ticket.closed_by: None,
            Ticket.closed_at: datetime.now(timezone.utc),
        }
    ).where(Ticket.id == ticket.id)
    try:
        await client.reactions_add(
            channel=env.slack_help_channel,
            name="white_check_mark",
            timestamp=ticket.msg_ts,
        )
        await client.reactions_remove(
            channel=env.slack_help_channel,
            name="thinking_face",
            timestamp=ticket.msg_ts,
        )
    except SlackApiError as e:
        logging.warning(f"Help bot reaction update failed: {e.response.get('error')}")


async def refresh_ticket_post(ticket: Ticket, client: AsyncWebClient) -> None:
    """Re-render the ticket's post in the tickets channel with the help bot
    lines (backend_message_blocks reads them)."""
    from nephthys.events.message.send_backend_message import backend_message_blocks
    from nephthys.events.message.send_backend_message import (
        backend_message_fallback_text,
    )

    fresh = (
        await Ticket.objects(Ticket.opened_by, Ticket.reopened_by)
        .where(Ticket.id == ticket.id)
        .first()
    )
    if not fresh or not fresh.opened_by:
        return
    past_tickets = await Ticket.count().where(
        (Ticket.opened_by == fresh.opened_by.id) & (Ticket.id != fresh.id)
    )
    reopened_by = fresh.reopened_by if fresh.reopened_by.id else None
    try:
        await client.chat_update(
            channel=env.slack_ticket_channel,
            ts=fresh.ticket_ts,
            text=backend_message_fallback_text(
                fresh.opened_by.slack_id, fresh.description, reopened_by
            ),
            blocks=await backend_message_blocks(
                author_user_id=fresh.opened_by.slack_id,
                msg_ts=fresh.msg_ts,
                past_tickets=past_tickets,
                current_category_tag_id=fresh.category_tag,
                reopened_by=reopened_by,
            ),
        )
    except SlackApiError as e:
        logging.warning(
            f"Couldn't update ticket post ticket_id={ticket.id} error={e.response.get('error')}"
        )


async def on_ticket_reopened(ticket: Ticket) -> None:
    """Called by reopen() before it posts the new ticket post. Stops the
    pending thread deletion, and removes the old post the bot kept up."""
    info = (
        await HelpBotTicket.objects().where(HelpBotTicket.ticket == ticket.id).first()
    )
    if not info:
        return
    if info.auto_resolved and ticket.ticket_ts:
        await delete_message(env.slack_ticket_channel, ticket.ticket_ts)
    await HelpBotTicket.update(
        {HelpBotTicket.reopened: True, HelpBotTicket.delete_after: None}
    ).where(HelpBotTicket.id == info.id)


async def _ticket_for_button(body: dict[str, Any]) -> tuple[Ticket, User] | None:
    ticket_id = int(body["actions"][0]["value"])
    ticket = await Ticket.objects().where(Ticket.id == ticket_id).first()
    user = await User.objects().where(User.slack_id == body["user"]["id"]).first()
    if not ticket or not user:
        return None
    return ticket, user


async def _clear_buttons(body: dict[str, Any], context_text: str | None) -> None:
    """Swap the answer's buttons for a short context line (or nothing)."""
    message = body.get("message") or {}
    blocks = [b for b in message.get("blocks", []) if b.get("type") != "actions"]
    if context_text:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": context_text}]}
        )
    try:
        await env.slack_client.chat_update(
            channel=body["channel"]["id"],
            ts=message["ts"],
            text=message.get("text", ""),
            blocks=blocks,
        )
    except (SlackApiError, KeyError) as e:
        logging.warning(f"Couldn't clear help bot buttons: {e}")


async def _may_use_buttons(ticket: Ticket, user: User) -> bool:
    return bool(user.helper) or ticket.opened_by == user.id


async def handle_answered_button(body: dict[str, Any], client: AsyncWebClient):
    from nephthys.actions.resolve import resolve

    copy = env.transcript.help_bot
    found = await _ticket_for_button(body)
    if not copy or not found:
        return
    ticket, user = found
    if not await _may_use_buttons(ticket, user):
        await client.chat_postEphemeral(
            channel=env.slack_help_channel,
            thread_ts=ticket.msg_ts,
            user=user.slack_id,
            text=copy.buttons_not_yours,
        )
        return
    await _clear_buttons(body, None)
    if ticket.status != TicketStatus.CLOSED:
        await resolve(ticket.msg_ts, user.slack_id, client)


async def handle_need_help_button(body: dict[str, Any], client: AsyncWebClient):
    copy = env.transcript.help_bot
    found = await _ticket_for_button(body)
    if not copy or not found:
        return
    ticket, user = found
    if not await _may_use_buttons(ticket, user):
        await client.chat_postEphemeral(
            channel=env.slack_help_channel,
            thread_ts=ticket.msg_ts,
            user=user.slack_id,
            text=copy.buttons_not_yours,
        )
        return
    await _clear_buttons(
        body, copy.faq_still_need_help_context.format(user_id=user.slack_id)
    )
    await HelpBotTicket.update({HelpBotTicket.still_need_help: True}).where(
        HelpBotTicket.ticket == ticket.id
    )
    await post_reply(ticket, copy.faq_still_need_help_reply, client)
    await refresh_ticket_post(ticket, client)
