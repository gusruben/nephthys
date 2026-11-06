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
from nephthys.utils.help_bot.report import report
from nephthys.utils.help_bot.status import Outcome
from nephthys.utils.help_bot.status import status_outcome
from nephthys.utils.help_bot.status import submission_summary
from nephthys.utils.help_bot.status import tracking_outcome
from nephthys.utils.ticket_methods import delete_message
from nephthys.utils.ticket_methods import get_question_message_link
from nephthys.utils.ticket_methods import ThreadGoneError

ANSWERED_ACTION = "help-bot-answered"
NEED_HELP_ACTION = "help-bot-need-help"
# "[as U123]", or "[as @name]", which Slack turns into "[as <@U123>]"
IMPERSONATE = re.compile(r"\[as <?@?(U[A-Z0-9]{6,})(?:\|[^>\]]*)?>?\]")
# The most a new ticket's help bot run may take, all calls included. The
# ticket itself already exists by then.
RUN_TIMEOUT_SECONDS = 45


async def help_bot_context_lines(msg_ts: str) -> list[str]:
    """Extra lines for a ticket's post in the tickets channel: the asker's
    submission state, what the bot did, and whether they came back."""
    copy = env.transcript.help_bot
    if not copy:
        return []
    try:
        info = (
            await HelpBotTicket.objects()
            .where(HelpBotTicket.ticket.msg_ts == msg_ts)
            .first()
        )
    except Exception as e:
        await report("ticket post lines", f"couldn't read help bot info: {e}", e)
        return []
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
    """Called once a new ticket exists. Never raises: on any failure the
    ticket stays exactly what today's bot makes, a normal ticket."""
    try:
        if not env.new_flow_enabled():
            return
        await asyncio.wait_for(
            _run_help_bot(ticket, text, author_id, client), RUN_TIMEOUT_SECONDS
        )
    except ThreadGoneError:
        logging.warning(f"Help bot: thread gone for ticket_id={ticket.id}")
    except asyncio.TimeoutError:
        await report(
            "timeout", f"gave up on ticket {ticket.id} after {RUN_TIMEOUT_SECONDS}s"
        )
    except Exception as e:
        await report(f"run {type(e).__name__}", f"failed on ticket {ticket.id}: {e}", e)


async def _fetch_rows(slack_id: str):
    try:
        rows, orders = await asyncio.gather(
            airtable.submissions_for(slack_id), airtable.orders_for(slack_id)
        )
        return rows, orders, None
    except Exception as e:
        await report("airtable", f"couldn't read rows for {slack_id}: {e}")
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
    try:
        if not airtable_error:
            info.submission_summary = submission_summary(rows, orders, config)
        if result.reason:
            info.action_summary = copy.ticket_post_actions["human"].format(
                reason=copy.ticket_post_reasons[result.reason]
            )
    except Exception as e:
        await report(
            "summary", f"couldn't summarise rows for ticket {ticket.id}: {e}", e
        )

    try:
        await _answer(
            ticket,
            info,
            result,
            rows,
            orders,
            airtable_error,
            config,
            review_jam,
            author_id,
            client,
        )
    except ThreadGoneError:
        raise
    except Exception as e:
        # The answer (or part of it) didn't go out: the ticket is still a
        # normal open ticket, so record what we know and move on
        await report(
            f"answer {type(e).__name__}", f"couldn't post on ticket {ticket.id}: {e}", e
        )

    try:
        await info.save()
    except Exception as e:
        await report(
            "db", f"couldn't save help bot info for ticket {ticket.id}: {e}", e
        )
        return
    await refresh_ticket_post(ticket, client)


async def _answer(
    ticket: Ticket,
    info: HelpBotTicket,
    result: Classification,
    rows,
    orders,
    airtable_error,
    config: JamConfig | None,
    review_jam: str | None,
    author_id: str,
    client: AsyncWebClient,
) -> None:
    copy = env.transcript.help_bot
    assert copy
    now = datetime.now(timezone.utc)
    delete_after = now + timedelta(minutes=env.jamegam_delete_after_minutes)

    if result.route == "faq" and result.faq:
        entry = result.faq
        info.faq_question = entry.question
        info.action_summary = copy.ticket_post_actions["faq"].format(
            question=entry.question
        )
        reply = copy.faq_answer.format(
            faq_link=env.transcript.faq_link,
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


def jam_context(config: JamConfig | None, review_jam: str | None) -> str:
    """Tells the classifier which jam is under review, so it can tell when a
    post is about an older one."""
    hosted = ""
    if config and config.jam.hosted:
        # "submit to both jams" means the partner's itch jam plus the jame gam
        # form, which the other-events FAQ answers with "sorry, no!"
        hosted = (
            f"The current jam, {config.jam.displayName}, is run together with a partner on itch. "
            "Entering it means submitting the game on its itch jam page AND filling out the jame gam "
            "(hack club) submission form, so a question about submitting to both jams, submitting twice, "
            'or "the other jam" is about that, and is answered by the FAQ question about how to submit. '
            "The FAQ question about submitting to another game jam or event is only for entering a "
            "different, unrelated event as well: never pick it for a post that asks about submitting to both "
            "jams, even if the post also asks something else. Whether the jame gam form is open yet, or when it opens "
            "or closes, is still jam_facts form."
        )
    if not review_jam:
        return hosted
    name = config.jam_name(review_jam) if config else review_jam
    earlier = (
        [f"{v} ({k})" for k, v in config.shop.cycles.items() if k < review_jam]
        if config
        else []
    )
    text = f"The jam under review is {name} ({review_jam})."
    if earlier:
        text += " Earlier jams: " + ", ".join(earlier) + ", and older ones."
    return f"{hosted}\n{text}".strip()


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
        try:
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
        except Exception as e:
            # The ticket stays open in the tickets channel either way, so a
            # helper still sees it
            await report(
                "ping", f"couldn't ping the fulfiller on ticket {ticket.id}: {e}"
            )
        info.answer_ts = await post_reply(
            ticket,
            outcome.answer,
            client,
            blocks=[*_answer_blocks(outcome.answer), need_help_button(ticket.id)],
        )
        await _send_private(ticket, asker_id, outcome.private, client)
        return

    if outcome.resolve:
        info.answer_ts = await post_reply(
            ticket,
            outcome.answer,
            client,
            blocks=[
                # One note that also says the thread deletes itself
                Section(outcome.answer),
                Context(elements=[Text(copy.status_footer)]),
                # Resolved, so "still need help" reopens it (the existing
                # reopen action)
                Actions(
                    elements=[
                        Button(
                            text=copy.faq_still_need_help_button,
                            action_id="reopen-button",
                            value=f"{ticket.id}",
                        )
                    ]
                ),
            ],
        )
        if not await _send_private(ticket, asker_id, outcome.private, client):
            # They didn't get the details, so don't close it on them
            return
        await bot_resolve(ticket, client)
        info.auto_resolved = True
        info.delete_after = delete_after
    else:
        info.answer_ts = await post_reply(
            ticket,
            outcome.answer,
            client,
            blocks=[*_answer_blocks(outcome.answer), need_help_button(ticket.id)],
        )


def _answer_blocks(text: str) -> list:
    """An answer the bot gives on its own, with the line saying it's the bot."""
    copy = env.transcript.help_bot
    assert copy
    return [Section(text), Context(elements=[Text(copy.bot_note)])]


async def _send_private(
    ticket: Ticket, asker_id: str, text: str | None, client: AsyncWebClient
) -> bool:
    """Details only the asker should see (they can include an email).
    False if they couldn't be sent."""
    if not text:
        return True
    try:
        await client.chat_postEphemeral(
            channel=env.slack_help_channel,
            thread_ts=ticket.msg_ts,
            user=asker_id,
            text=text,
        )
        return True
    except Exception as e:
        await report(
            "ephemeral", f"couldn't send private details on ticket {ticket.id}: {e}"
        )
        return False


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
        except Exception as e:
            logging.warning(
                f"Couldn't find fulfiller on Slack email={order.fulfilled_by_email} error={e}"
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


def need_help_button(ticket_id: int) -> Actions:
    """Just "still need help", for answers on a ticket that stays open."""
    copy = env.transcript.help_bot
    assert copy
    return Actions(
        elements=[
            Button(
                text=copy.faq_still_need_help_button,
                action_id=NEED_HELP_ACTION,
                value=f"{ticket_id}",
            )
        ]
    )


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
    except Exception as e:
        logging.warning(f"Help bot reaction update failed: {e}")


async def refresh_ticket_post(ticket: Ticket, client: AsyncWebClient) -> None:
    """Re-render the ticket's post in the tickets channel with the help bot
    lines (backend_message_blocks reads them)."""
    from nephthys.events.message.send_backend_message import backend_message_blocks
    from nephthys.events.message.send_backend_message import (
        backend_message_fallback_text,
    )

    try:
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
    except Exception as e:
        await report(
            "ticket post", f"couldn't update ticket post for ticket {ticket.id}: {e}"
        )


async def on_ticket_reopened(ticket: Ticket) -> None:
    """Called by reopen() before it posts the new ticket post. Stops the
    pending thread deletion, and removes the old post the bot kept up. Never
    raises, so reopening always works."""
    try:
        info = (
            await HelpBotTicket.objects()
            .where(HelpBotTicket.ticket == ticket.id)
            .first()
        )
        if not info:
            return
        # Stop the deletion first: that's the part that matters
        await HelpBotTicket.update(
            {HelpBotTicket.reopened: True, HelpBotTicket.delete_after: None}
        ).where(HelpBotTicket.id == info.id)
        if info.auto_resolved and ticket.ticket_ts:
            await delete_message(env.slack_ticket_channel, ticket.ticket_ts)
    except Exception as e:
        await report(
            "reopen",
            f"couldn't update help bot info on reopen of ticket {ticket.id}: {e}",
            e,
        )


async def tell_clicker_it_failed(
    body: dict[str, Any], client: AsyncWebClient, e: Exception
) -> None:
    """A button or macro broke: say so, only to whoever clicked it."""
    await report(f"button {type(e).__name__}", f"a click failed: {e}", e)
    copy = env.transcript.help_bot
    try:
        channel = (body.get("channel") or {}).get("id") or env.slack_help_channel
        message = body.get("message") or {}
        await client.chat_postEphemeral(
            channel=channel,
            thread_ts=message.get("thread_ts") or message.get("ts"),
            user=(body.get("user") or {})["id"],
            text=copy.action_failed
            if copy
            else "something went wrong, please try again",
        )
    except Exception as e2:
        logging.error(f"Couldn't tell the clicker a button failed: {e2}")


async def _ticket_for_button(body: dict[str, Any]) -> tuple[Ticket, User] | None:
    ticket_id = int(body["actions"][0]["value"])
    ticket = await Ticket.objects().where(Ticket.id == ticket_id).first()
    user = await User.objects().where(User.slack_id == body["user"]["id"]).first()
    if not ticket or not user:
        return None
    return ticket, user


async def _clear_buttons(body: dict[str, Any]) -> None:
    """Take the buttons off the answer once one was clicked."""
    message = body.get("message") or {}
    blocks = [b for b in message.get("blocks", []) if b.get("type") != "actions"]
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
    try:
        await _handle_answered_button(body, client)
    except Exception as e:
        await tell_clicker_it_failed(body, client, e)


async def handle_need_help_button(body: dict[str, Any], client: AsyncWebClient):
    try:
        await _handle_need_help_button(body, client)
    except Exception as e:
        await tell_clicker_it_failed(body, client, e)


async def _handle_answered_button(body: dict[str, Any], client: AsyncWebClient):
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
    await _clear_buttons(body)
    if ticket.status != TicketStatus.CLOSED:
        await resolve(ticket.msg_ts, user.slack_id, client)


async def _handle_need_help_button(body: dict[str, Any], client: AsyncWebClient):
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
    await _clear_buttons(body)
    await HelpBotTicket.update({HelpBotTicket.still_need_help: True}).where(
        HelpBotTicket.ticket == ticket.id
    )
    await post_reply(ticket, copy.faq_still_need_help_reply, client)
    await refresh_ticket_post(ticket, client)
