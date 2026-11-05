import logging
from datetime import datetime
from datetime import timezone

from nephthys.database.enums import TicketStatus
from nephthys.database.tables import HelpBotTicket
from nephthys.database.tables import Ticket
from nephthys.utils.delete_thread import add_thread_to_delete_queue
from nephthys.utils.env import env
from nephthys.utils.env import test_channel_scope
from nephthys.utils.logging import send_heartbeat
from nephthys.utils.ticket_methods import delete_bot_replies
from nephthys.utils.ticket_methods import delete_message


async def delete_bot_resolved_threads():
    """Delete threads the help bot resolved itself once their time is up,
    unless the asker reopened them. The status reply says this will happen."""
    now = datetime.now(timezone.utc)
    due = await HelpBotTicket.objects(HelpBotTicket.ticket).where(
        (HelpBotTicket.delete_after <= now)
        & HelpBotTicket.deleted_at.is_null()
        & HelpBotTicket.reopened.eq(False)
        & HelpBotTicket.auto_resolved.eq(True)
    )
    for info in due:
        ticket: Ticket = info.ticket
        if not ticket.id or ticket.status != TicketStatus.CLOSED:
            # Reopened some other way, or the ticket is gone: leave it alone
            await HelpBotTicket.update({HelpBotTicket.delete_after: None}).where(
                HelpBotTicket.id == info.id
            )
            continue
        with test_channel_scope(ticket.test_channel is not None):
            try:
                await _delete_thread(ticket)
            except Exception as e:
                logging.error(
                    f"Failed to delete bot-resolved thread ticket_id={ticket.id} error={e}",
                    exc_info=True,
                )
                await send_heartbeat(
                    f"Failed to delete bot-resolved thread for ticket {ticket.id}: {e}"
                )
                continue
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
    # The question itself (and anything the asker said after) needs the
    # admin user token, the same one the resolve flow uses for ticket posts
    if await env.workspace_admin_available():
        await add_thread_to_delete_queue(
            channel_id=env.slack_help_channel, thread_ts=ticket.msg_ts
        )
