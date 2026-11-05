from nephthys.database.tables import Ticket
from nephthys.database.tables import User
from nephthys.macros.types import ReplyMacro
from nephthys.utils import faq
from nephthys.utils.env import env
from nephthys.utils.faq import faq_link_label
from nephthys.utils.faq import faq_section_url
from nephthys.utils.slack_user import get_user_profile
from nephthys.utils.ticket_methods import reply_to_ticket


class FAQ(ReplyMacro):
    """
    A simple macro reminding people to check the FAQ.

    With the help bot on, `?faq <topic>` posts that question's answer from the
    FAQ canvas instead (and resolves, like plain `?faq`).
    """

    name = "faq"
    message = env.transcript.faq_macro

    async def run(self, ticket: Ticket, helper: User, **kwargs) -> None:
        text: str = kwargs.get("text", "")
        topic = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
        copy = env.transcript.help_bot
        if not (topic and copy and env.new_flow_enabled()):
            return await super().run(ticket, helper, **kwargs)

        entry = faq.find_faq_entry(topic)
        if not entry:
            await env.slack_client.chat_postEphemeral(
                channel=env.slack_help_channel,
                thread_ts=ticket.msg_ts,
                user=helper.slack_id,
                text=copy.faq_topic_not_found.format(
                    topic=topic,
                    questions="\n".join(f"- {e.question}" for e in faq.faq_entries)
                    or "(the faq hasn't loaded)",
                ),
            )
            return

        sender = ticket.opened_by
        if isinstance(sender, int):
            sender = await User.objects().where(User.id == sender).first()
        name = (
            (await get_user_profile(sender.slack_id)).display_name() if sender else ""
        )
        reply = copy.faq_topic_macro.format(
            url=faq_section_url(entry),
            question=faq_link_label(entry),
            answer=entry.answer,
        ).replace("(user)", name)
        await reply_to_ticket(text=reply, ticket=ticket, client=env.slack_client)
        await self.resolve_after_reply(ticket, helper)
