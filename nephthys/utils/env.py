import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from aiohttp import ClientSession
from dotenv import load_dotenv
from openai import AsyncOpenAI
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.transcripts import transcripts
from nephthys.transcripts.transcript import Transcript

load_dotenv(override=True)

# True while handling an event, button or task that belongs to the test help
# channel. A ContextVar so it follows the asyncio task handling that one event
# and never leaks into others. Set with `test_channel_scope`.
_test_scope: ContextVar[bool] = ContextVar("jamegam_test_scope", default=False)


def set_test_scope(is_test: bool) -> None:
    """Like test_channel_scope, for the rest of the current task. Only for
    code that runs once per task, like the Slack middleware."""
    _test_scope.set(is_test)


@contextmanager
def test_channel_scope(is_test: bool):
    """Route env.slack_help_channel / env.slack_ticket_channel to the test
    channels (or explicitly not) for the code inside the block."""
    token = _test_scope.set(is_test)
    try:
        yield
    finally:
        _test_scope.reset(token)


def get_environ_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, None)
    if value is None:
        return default
    value = value.strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean env var {name}={value!r}")


class Environment:
    def __init__(self):
        self.slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "unset")
        self.slack_user_token = os.environ.get("SLACK_USER_TOKEN", "unset")
        self.slack_signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "unset")
        self.slack_app_token = os.environ.get("SLACK_APP_TOKEN")

        self.uptime_url = os.environ.get("UPTIME_URL")
        self.hack_club_ai_api_key = os.environ.get("HACK_CLUB_AI_API_KEY")
        self.ai_base_url = os.environ.get(
            "HACK_CLUB_AI_BASE_URL", "https://ai.hackclub.com/proxy/v1"
        )
        self.ai_title_model = os.environ.get("AI_TITLE_MODEL", "openai/gpt-oss-120b")
        self.ai_tag_model = os.environ.get(
            "AI_TAG_MODEL", "google/gemini-3-flash-preview"
        )

        # jame gam help bot features (FAQ answers, status lookups, live jam
        # facts). Only used by transcripts that set `help_bot`, and only in the
        # help channels listed in JAMEGAM_NEW_FLOW_CHANNELS.
        self.jamegam_new_flow_channels = {
            c.strip()
            for c in os.environ.get("JAMEGAM_NEW_FLOW_CHANNELS", "").split(",")
            if c.strip()
        }
        # A second, private help channel (and its own tickets channel) the bot
        # also watches, for trying changes out. Its tickets are kept out of
        # stats, leaderboards and the API.
        self.jamegam_test_channel = os.environ.get("JAMEGAM_TEST_CHANNEL") or None
        self.jamegam_test_ticket_channel = (
            os.environ.get("JAMEGAM_TEST_TICKET_CHANNEL") or None
        )
        self.ai_jamegam_model = os.environ.get(
            "AI_JAMEGAM_MODEL", "anthropic/claude-haiku-5.5"
        )
        self.ai_jamegam_min_confidence = float(
            os.environ.get("AI_JAMEGAM_MIN_CONFIDENCE", "0.6")
        )
        # Read-only Airtable token for the jame gam base (submissions + orders)
        self.jamegam_airtable_token = os.environ.get("JAMEGAM_AIRTABLE_TOKEN")
        self.jamegam_airtable_base = os.environ.get(
            "JAMEGAM_AIRTABLE_BASE", "apprhNJsC9D4nlFRB"
        )
        # Live jam config served by the jame gam site
        self.jamegam_config_url = os.environ.get(
            "JAMEGAM_CONFIG_URL", "https://jamegam.hackclub.com/api/jam"
        )
        # How long a bot-resolved status thread stays up unless it's reopened
        self.jamegam_delete_after_minutes = int(
            os.environ.get("JAMEGAM_DELETE_AFTER_MINUTES", str(24 * 60))
        )
        # Test channel only: "[as U123]" in a helper's question looks up U123's
        # submissions instead of the asker's
        self.jamegam_test_impersonation = get_environ_bool(
            "JAMEGAM_TEST_IMPERSONATION", default=False
        )

        self.otel_logs_url = os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
        self.otel_service_name = os.environ.get("OTEL_SERVICE_NAME", "nephthys")
        # Allows easily providing HTTP Basic Auth credentials formatted as user:pass
        self.otel_logs_basic_auth = os.environ.get("OTEL_EXPORTER_OTLP_LOGS_BASIC_AUTH")

        self.environment = os.environ.get("ENVIRONMENT", "development")
        default_log_level = (
            logging.WARNING if self.environment == "production" else logging.INFO
        )
        self.log_level_stderr = (
            os.environ.get("LOG_LEVEL_STDERR")
            or os.environ.get("LOG_LEVEL")
            or default_log_level
        )
        self.log_level_otel = os.environ.get("LOG_LEVEL_OTEL", logging.INFO)
        self.base_url: str = os.environ.get(
            "BASE_URL", "https://nephthys.hackclub.com"
        ).rstrip("/")

        self._slack_help_channel = os.environ.get("SLACK_HELP_CHANNEL", "unset")
        self._slack_ticket_channel = os.environ.get("SLACK_TICKET_CHANNEL", "unset")
        self.slack_bts_channel = os.environ.get("SLACK_BTS_CHANNEL", "unset")
        self.slack_maintainer_id = os.environ.get("SLACK_MAINTAINER_ID", "unset")
        self.program = os.environ.get("PROGRAM", "summer_of_making")
        self.daily_summary = get_environ_bool("DAILY_SUMMARY", default=True)
        self.heartbeat_errors_only = get_environ_bool(
            "HEARTBEAT_ERRORS_ONLY", default=False
        )
        self.enable_feedback = get_environ_bool("ENABLE_FEEDBACK", default=False)
        self.app_title = os.environ.get("APP_TITLE", "helper heidi")

        self.port = int(os.environ.get("PORT", 3000))

        self.slack_heartbeat_channel = os.environ.get("SLACK_HEARTBEAT_CHANNEL")

        # Stale ticket auto-close: number of days of inactivity before closing
        # Set to a positive integer to enable, leave unset to disable
        stale_days_str = os.environ.get("STALE_TICKET_DAYS")
        if stale_days_str:
            try:
                stale_days = int(stale_days_str)
                self.stale_ticket_days = stale_days if stale_days > 0 else None
                if stale_days <= 0:
                    logging.warning(
                        f"STALE_TICKET_DAYS must be positive, got {stale_days}. Disabling."
                    )
            except ValueError:
                logging.warning(
                    f"Invalid STALE_TICKET_DAYS value: {stale_days_str}. Disabling."
                )
                self.stale_ticket_days = None
        else:
            self.stale_ticket_days = None

        unset = [key for key, value in self.__dict__.items() if value == "unset"]

        if unset:
            raise ValueError(f"Missing environment variables: {', '.join(unset)}")

        transcript_instances = [program() for program in transcripts]
        valid_programs = [
            program.program_snake_case for program in transcript_instances
        ]
        if self.program not in valid_programs:
            raise ValueError(
                f"Invalid PROGRAM environment variable: {self.program}. "
                f"Must be one of {valid_programs}"
            )

        self.session: ClientSession
        self.transcript = next(
            (
                program
                for program in transcript_instances
                if program.program_snake_case == self.program
            ),
            Transcript(),
        )

        self.slack_client = AsyncWebClient(token=self.slack_bot_token)
        self.ai_client = (
            AsyncOpenAI(
                base_url=self.ai_base_url,
                api_key=self.hack_club_ai_api_key,
            )
            if self.hack_club_ai_api_key
            else None
        )

        # Cache whether the user token has workspace admin privileges
        self._workspace_admin_available: bool | Literal["unchecked"] = "unchecked"

    @property
    def slack_help_channel(self) -> str:
        """The help channel for whatever is being handled right now: the test
        channel while handling something from it, else the real one."""
        if _test_scope.get():
            return self.jamegam_test_channel or self._slack_help_channel
        return self._slack_help_channel

    @property
    def slack_ticket_channel(self) -> str:
        """The tickets channel that goes with slack_help_channel."""
        if _test_scope.get():
            return self.jamegam_test_ticket_channel or self._slack_ticket_channel
        return self._slack_ticket_channel

    def help_channels(self) -> set[str]:
        """Every help channel the bot answers in."""
        channels = {self._slack_help_channel}
        if self.jamegam_test_channel:
            channels.add(self.jamegam_test_channel)
        return channels

    def is_test_channel(self, channel_id: str | None) -> bool:
        return bool(channel_id) and channel_id in {
            self.jamegam_test_channel,
            self.jamegam_test_ticket_channel,
        }

    @property
    def help_bot_disabled(self) -> bool:
        """JAMEGAM_HELP_BOT_DISABLED=true turns every help bot feature off.
        Read on every call, so it takes effect as soon as the env changes."""
        try:
            return get_environ_bool("JAMEGAM_HELP_BOT_DISABLED", default=False)
        except ValueError:
            return True  # a typo in the switch should fail safe, i.e. off

    def new_flow_enabled(self) -> bool:
        """Whether the help bot features are on for the current help channel."""
        return (
            self.transcript.help_bot is not None
            and not self.help_bot_disabled
            and self.slack_help_channel in self.jamegam_new_flow_channels
        )

    async def workspace_admin_available(self) -> bool:
        """Check if the provided user token has workspace admin privileges."""
        if self._workspace_admin_available != "unchecked":
            return self._workspace_admin_available
        user_token_identity = await self.slack_client.auth_test(
            token=self.slack_user_token
        )
        user_id = user_token_identity["user_id"]
        if not user_id:
            raise ValueError(
                f"Unable to get my user ID from Slack API: {user_token_identity}"
            )
        user_info_response = await self.slack_client.users_info(user=user_id)
        user_info = user_info_response["user"]
        if not user_info:
            raise ValueError("Failed to get user info from Slack API.")
        self._workspace_admin_available = user_info["is_admin"]
        return user_info["is_admin"]


env = Environment()
