from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator


class HelpBotCopy(BaseModel):
    """Every user-facing string the help bot features use (FAQ answers, status
    lookups, tracking, live jam facts and the extra ticket post lines).

    Only set for programs that turn those features on. Placeholders in braces
    are filled in with str.format, so every one listed here must be passed.
    """

    class Config:
        extra = "forbid"

    # FAQ answers ({url}, {question}, {answer}; each answer line is quoted)
    faq_answer: str
    faq_answered_button: str
    faq_still_need_help_button: str
    faq_still_need_help_reply: str
    buttons_not_yours: str
    # Small line under every answer the bot gives on its own, saying it's the bot
    bot_note: str

    # `?faq <topic>` ({url}, {question}, {answer}; (user) like other macros)
    faq_topic_macro: str
    # Ephemeral to the helper ({topic}, {questions})
    faq_topic_not_found: str

    # Live jam facts ({name}, {start}, {end}, {itch_url}, {url}, {closes}, {days})
    jam_current: str
    jam_next: str
    jam_ended: str
    theme_upcoming: str
    theme_out: str
    form_open: str
    form_not_open: str

    # Status of their submission in the jam under review
    # How a game's title shows ({itch_url}, {title}); used as {game} below
    game_link: str
    game_no_link: str
    status_intro_one: str
    status_intro_many: str
    status_line_many: str
    status_unreviewed: str
    status_approved: str
    status_dm_sent: str
    # Replaces bot_note on answers the bot resolves itself (they delete in 24h)
    status_footer: str

    # Order lines, used by the tracking answers
    order_not_fulfilled: str
    order_processing: str
    order_fulfilled: str
    order_held_idv: str

    # Tracking
    tracking_info_public: str
    tracking_info_private: str
    tracking_not_shipped: str
    tracking_pinged_public: str
    tracking_ping_team: str

    # Extra lines on the ticket post in the tickets channel (helpers only)
    ticket_post_submission: str
    ticket_post_no_submission: str
    ticket_post_bot: str
    ticket_post_reopened: str
    ticket_post_still_need_help: str
    # What the bot did, keyed by action ({question}, {when}, {fulfiller}, {reason})
    ticket_post_actions: dict[str, str]
    # Why it left a ticket for a human, keyed by reason ({note}, ...)
    ticket_post_reasons: dict[str, str]
    # One submission's state, keyed by state ({date}, {note}, {status})
    ticket_post_states: dict[str, str]


class Transcript(BaseModel):
    """Class to hold all the transcript messages and links used in the bot."""

    class Config:
        """Configuration for the Pydantic model."""

        extra = "forbid"

    program_name: str = Field(
        default="Summer of Making", description="Name of the program"
    )
    program_owner: str = Field(
        default="U054VC2KM9P",
        description="Slack ID of the support manager",
    )
    help_channel: str = Field(
        default="",
        description="Slack channel ID for help requests",
    )
    ticket_channel: str = Field(
        default="",
        description="Slack channel ID for ticket creation",
    )
    team_channel: str = Field(
        default="",
        description="Slack channel ID for team discussions and stats",
    )
    ticket_reopen: str = Field(
        default="",
        description="Message when ticket is reopened",
    )

    @property
    def program_snake_case(self) -> str:
        """Snake case version of the program name."""
        return self.program_name.lower().replace(" ", "_")

    faq_link: str = Field(
        default="https://hackclub.slack.com/docs/T0266FRGM/F093F8D7EE9",
        description="FAQ link URL",
    )

    faq_canvas_id: str | None = Field(
        default=None,
        description="File ID of the FAQ canvas. When set, the bot reads the canvas every 15 minutes so it can quote answers from it",
    )

    help_bot: HelpBotCopy | None = Field(
        default=None,
        description="Copy for the help bot features (FAQ answers, status lookups, tracking, live jam facts). Leave unset to keep them off",
    )

    summer_help_channel: str = Field(
        default="C091D312J85", description="Summer help channel ID"
    )

    identity_help_channel: str = Field(
        default="C092833JXKK", description="Identity help channel ID"
    )

    first_ticket_create: str = Field(
        default="", description="Message for first-time ticket creators"
    )

    ticket_create: str = Field(default="", description="Message for ticket creation")

    resolve_ticket_button: str = Field(
        default="Mark as resolved",
        description="Text for the green resolve-ticket button",
    )

    ticket_resolve: str = Field(
        default="", description="Message when ticket is resolved"
    )

    ticket_resolve_stale: str = Field(
        default="",
        description="Message when ticket is resolved due to being stale",
    )

    thread_broadcast_delete: str = Field(
        default="hey! please keep your messages *all in one thread* to make it easier to read! i've gone ahead and removed that message from the channel for ya :D",
    )

    ticket_feedback_text: str = Field(default="Feedback really helps us! Thanks <3")

    ## MACROS ##

    faq_macro: str = Field(
        default="", description="Message to be sent when the FAQ macro is used"
    )

    fraud_macro: str = Field(
        default="Hiya (user)! Would you mind directing any fraud related queries to <@U091HC53CE8>? :rac_cute:\n\nIt'll keep your case confidential and make it easier for the fraud team to keep track of!",
        description="Message to be sent when the fraud macro is used",
    )

    shipwrights_macro: str = Field(
        default="Hey, (user)!\nPlease ask questions about project shipping or certifications in <#C099P9FQQ91>.\n\nThe Shipwrights Team will help with your question!",
        description="Message to be sent when the shipwrights macro is used",
    )

    identity_macro: str = Field(
        default="", description="Message to be sent when the identity macro is used"
    )

    ship_cert_queue_macro: str | None = Field(
        default=None,
        description="Message to be sent when the ship cert queue macro is used (only applies to Flavortown and SoM)",
    )

    hackatime_macro: str = Field(
        default="Hi (user), could you send that question to the Hackatime team at https://letterbird.co/hackatime? :rac_cute:\n\nThey'll be able to provide better help for Hackatime-specific issues!\n\n_I've marked this thread as resolved_",
        description="Message to be sent when the Hackatime macro is used",
    )

    vote_queue_macro: str | None = Field(
        default=None,
        description="Message to inform users that there's a large voting backlog",
    )

    vote_quality_macro: str | None = Field(
        default=None,
        description="Message to inform users that low-quality votes will get rejected",
    )

    max_tokens_macro: str | None = Field(
        default=None,
        description="Message explaining max_tokens for OpenRouter",
    )

    no_money_macro: str | None = Field(
        default=None,
        description="Message for credits ran out",
    )

    stale_tickets_macro: str = Field(
        default="Hey, (user)! It seems like this ticket has been inactive for some days so I'll be closing it.\nIf your question wasn't answered, please feel free to make a new one. Thanks!",
        description="Message for closing stale tickets",
    )

    search_macro: str | None = Field(
        default=None,
        description="Message explaining web search and Exa API recommendation for Hack Club AI",
    )

    not_allowed_channel: str = Field(
        default="", description="Message for unauthorized channel access"
    )

    @model_validator(mode="after")
    def set_default_messages(self):
        """Set default values for messages that reference other fields"""
        if not self.first_ticket_create:
            self.first_ticket_create = f"""oh, hey (user) it looks like this is your first time here, welcome! someone should be along to help you soon but in the mean time i suggest you read the faq <{self.faq_link}|here>, it answers a lot of common questions.
if your question has been answered, please hit the button below to mark it as resolved
    """

        if not self.ticket_create:
            self.ticket_create = f"""someone should be along to help you soon but in the mean time i suggest you read the faq <{self.faq_link}|here> to make sure your question hasn't already been answered. if it has been, please hit the button below to mark it as resolved :D
    """

        if not self.ticket_resolve:
            self.ticket_resolve = f"""oh, oh! it looks like this post has been marked as resolved by <@{{user_id}}>! if you have any more questions, please make a new post in <#{self.help_channel}> and someone'll be happy to help you out! not me though, i'm just a silly racoon ^-^
    """

        if not self.ticket_resolve_stale:
            self.ticket_resolve_stale = f""":rac_nooo: it looks like this post is a bit old! if you still need help, please make a new post in <#{self.help_channel}> and someone'll be happy to help you out! ^~^
        """

        if not self.faq_macro:
            self.faq_macro = f"hey, (user)! this question is answered in the faq i sent earlier, please make sure to check it out! :rac_cute:\n\n<{self.faq_link}|here it is again>"

        if not self.identity_macro:
            self.identity_macro = f"hey, (user)! please could you ask questions about identity verification in <#{self.identity_help_channel}>? :rac_cute:\n\nit helps the verification team keep track of questions easier!"

        if not self.not_allowed_channel:
            self.not_allowed_channel = f"heya, it looks like you're not supposed to be in that channel, pls talk to <@{self.program_owner}> if that's wrong"

        if not self.ticket_reopen:
            self.ticket_reopen = "hey hey! it looks like <@{helper_slack_id}> has reopened this post! someone'll be with you shortly, ty!"

        return self
