import logging
from datetime import datetime
from datetime import timezone

from slack_sdk.errors import SlackApiError

from nephthys.database.enums import TicketStatus
from nephthys.database.tables import HelpBotTicket
from nephthys.database.tables import Ticket
from nephthys.utils.delete_thread import add_thread_to_delete_queue
from nephthys.utils.env import env
from nephthys.utils.env import test_channel_scope
from nephthys.utils.help_bot.report import report
from nephthys.utils.ticket_methods import delete_bot_replies
from nephthys.utils.ticket_methods import delete_message

# Slack errors that mean the message is already gone
GONE = {"message_not_found", "thread_not_found", "channel_not_found"}


async def delete_bot_resolved_threads():
    """Delete threads the help bot resolved itself once their time is up,
    unless the asker reopened them. The status reply says this will happen.
    One bad row never stops the rest, and nothing here raises."""
    if env.help_bot_disabled:
        return
    now = datetime.now(timezone.utc)
    try:
        due = await HelpBotTicket.objects(HelpBotTicket.ticket).where(
            (HelpBotTicket.delete_after <= now)
            & HelpBotTicket.deleted_at.is_null()
            & HelpBotTicket.reopened.eq(False)
            & HelpBotTicket.auto_resolved.eq(True)
        )
    except Exception as e:
        await report("delete task", f"couldn't list threads to delete: {e}", e)
        return
    for info in due:
        try:
            await _handle_row(info, now)
        except Exception as e:
            await report("delete task", f"row {info.id} failed: {e}", e)


async def _handle_row(info: HelpBotTicket, now: datetime) -> None:
    ticket: Ticket = info.ticket
    if not ticket.id or ticket.status != TicketStatus.CLOSED:
        # Reopened some other way, or the ticket is gone: leave it alone
        await HelpBotTicket.update({HelpBotTicket.delete_after: None}).where(
            HelpBotTicket.id == info.id
        )
        return
    with test_channel_scope(ticket.test_channel is not None):
        await _delete_thread(ticket)
    await HelpBotTicket.update({HelpBotTicket.deleted_at: now}).where(
        HelpBotTicket.id == info.id
    )


async def _delete_thread(ticket: Ticket):
    logging.info(f"Deleting bot-resolved thread ticket_id={ticket.id}")
    await delete_message(env.slack_ticket_channel, ticket.ticket_ts)
    try:
        await delete_bot_replies(ticket.id)
    except ValueError:
        pass  # no bot replies left
    except SlackApiError as e:
        if e.response.get("error") not in GONE:
            raise
    # The question itself (and anything the asker said after) needs the
    # admin user token, the same one the resolve flow uses for ticket posts
    if await env.workspace_admin_available():
        try:
            await add_thread_to_delete_queue(
                channel_id=env.slack_help_channel, thread_ts=ticket.msg_ts
            )
        except SlackApiError as e:
            # Already gone counts as deleted
            if e.response.get("error") not in GONE:
                raise
