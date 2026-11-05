from nephthys.transcripts.transcript import HelpBotCopy
from nephthys.transcripts.transcript import Transcript

# Every user-facing string the jame gam bot sends lives in this file. COPY.md
# next to it lists each one with when it's sent and an example rendering, so
# keep the two in step.

JAMEGAM_BOT = "U0BB0FJU7DM"  # sends the approved / rejected DMs
ANNOUNCEMENTS_CHANNEL = "C0BBDUFF7K8"  # #jame-gam-announcements


class JameGam(Transcript):
    """Transcript for jame gam"""

    program_name: str = "Jame Gam"
    program_owner: str = "U07FCRNHS1J"  # @augie

    help_channel: str = "C0BBDUEMF0E"
    ticket_channel: str = "C0C2EHLQ96X"
    team_channel: str = "C0C2EHLQ96X"

    faq_link: str = "https://hackclub.enterprise.slack.com/docs/T0266FRGM/F0BBT4JD9P0"
    faq_canvas_id: str | None = "F0BBT4JD9P0"

    first_ticket_create: str = f"""
hi (user) :jamegam-yay:
welcome to <#{help_channel}>. someone will be along soon, but check the <{faq_link}|faq> first, it answers a lot.
if your question gets answered, hit the button below to mark it resolved
"""
    ticket_create: str = f"someone will be along soon! in the meantime check the <{faq_link}|faq>. if your question gets answered, hit the button below to mark it resolved"
    resolve_ticket_button: str = "all good now"
    ticket_resolve: str = f":jamegam-yay: this one was marked resolved by <@{{user_id}}>! more questions? make a new post in <#{help_channel}>"

    help_bot: HelpBotCopy | None = HelpBotCopy(
        # ---- FAQ answers ----
        faq_answer="i think the faq might answer this one!\n\n> *<{url}|{question}>*\n> {answer}\n\ndid that help?",
        faq_answered_button="that answered it",
        faq_still_need_help_button="still need help",
        faq_still_need_help_reply="no worries, a helper will be along soon to take a look",
        faq_still_need_help_context="<@{user_id}> still needs help, a helper is on the way",
        buttons_not_yours="only helpers or the person who asked can use these buttons",
        bot_note="i'm the help bot, just trying to help. if this doesn't answer it, a helper will take a look",
        faq_topic_macro="hey (user)! the faq covers this one:\n\n*<{url}|{question}>*\n{answer}",
        faq_topic_not_found="couldn't find a faq question for `{topic}`. the questions are:\n{questions}",
        # ---- live jam facts ----
        jam_current="the current jam is *{name}*, running from {start} to {end}. here's the <{itch_url}|itch page>",
        jam_next="the next jam is *{name}*, running from {start} to {end}. here's the <{itch_url}|itch page>",
        jam_ended=f"*{{name}}* wrapped up {{end}}, and the next jam hasn't been announced yet. keep an eye on <#{ANNOUNCEMENTS_CHANNEL}>!",
        theme_upcoming="the theme will be announced when the jam starts, {start}",
        theme_out="the theme is already out! it's on the <{itch_url}|itch page>",
        form_open="the submission form is open, <{url}|submit here>. it closes {closes}",
        form_not_open="the submission form isn't up yet. it will be posted near the end of the jam, and you'll have {days} days after the jam ends to submit",
        # ---- status of their submission in the jam under review ----
        status_intro_one="here's what i can see for *{title}*: {line}",
        status_intro_many="here's what i can see for your games:\n{lines}",
        status_line_many="- *{title}*: {line}",
        status_unreviewed=f"submitted, but not reviewed yet. reviews can take 3 to 4 weeks, and you'll get a dm from <@{JAMEGAM_BOT}> as soon as it's done",
        status_approved=f"approved! your prize dm from <@{JAMEGAM_BOT}> is coming soon",
        status_dm_sent=f"approved, and your prize dm already went out. check your dms from <@{JAMEGAM_BOT}>",
        status_footer="this thread deletes itself in 24 hours unless you hit reopen",
        reopen_button="reopen",
        # ---- order lines (tracking) ----
        order_not_fulfilled="it hasn't been sent yet",
        order_processing="it's being processed right now",
        order_fulfilled="it was sent {date}",
        order_held_idv="it's on hold until your identity verification goes through",
        # ---- tracking ----
        tracking_info_public="your {prize} was sent {date}! i've put the shipping details just below, only you can see them",
        tracking_info_private="here's what the person who sent your {prize} wrote:\n>{info}",
        tracking_not_shipped="about your {prize}: {order}",
        tracking_pinged_public="your {prize} was sent {date} but there's no tracking on it yet, so i've pinged the person who sent it. they'll reply here",
        tracking_ping_team="{fulfiller} <@{asker}> is asking for tracking on their {prize}, marked sent {date} with no tracking. <{order_url}|order> | <{thread_url}|thread>",
        # ---- ticket post lines (tickets channel, helpers only) ----
        ticket_post_submission="*submission:* {summary}",
        ticket_post_no_submission="*submission:* none found for this slack id",
        ticket_post_bot="*bot:* {what}",
        ticket_post_reopened="*reopened by the asker* after the bot's answer",
        ticket_post_still_need_help="*the asker said the bot's answer didn't help*",
        ticket_post_actions={
            "faq": "answered from the faq: {question}",
            "jam_facts": "answered with live jam info",
            "status": "answered their status and resolved it. the thread deletes {when} unless they reopen",
            "tracking_info": "sent them their shipping details and resolved it. the thread deletes {when} unless they reopen",
            "tracking_ping": "pinged {fulfiller} for tracking",
            "human": "left this for a human: {reason}",
        },
        ticket_post_reasons={
            "no_rows": "no submission found for their slack id",
            "only_withdrawn": "their only submissions are withdrawn",
            "unclear": "their submission needs a human look (see above)",
            "conflict": "they have rows for the same game that disagree",
            "not_current": "nothing from them in the jam under review ({jam}), so no status reply",
            "no_order": "no order found for them",
            "order_unclear": "their order needs a human look",
            "airtable": "couldn't read airtable",
            "no_tracking_contact": "their order has no tracking and i couldn't find who sent it",
        },
        ticket_post_states={
            "unreviewed": "not reviewed yet",
            "approved_checking": "approved, waiting on the spotcheck",
            "approved_ready": "approved and spotchecked, prize dm not sent",
            "approved_queued": "approved, prize dm queued",
            "approved_dm_sent": "approved, prize dm sent {date}",
            "pending": "pending, note: {note}",
            "needs_augie": "needs augie",
            "rejected": "rejected",
            "no_prize": "no prize (they see it as rejected)",
            "stickers_only": "stickers only",
            "withdrawn": "withdrawn",
            "other": "status {status}",
            "order": "order: {prize}, {status}",
        },
    )
