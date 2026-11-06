# pyright: reportArgumentType=false, reportOptionalMemberAccess=false, reportAttributeAccessIssue=false
# Failure injection: every way the help bot's outside calls can break (the AI
# out of credits or rate limited or timing out or talking nonsense, Airtable,
# the FAQ canvas, the jam config, Slack) must end as a normal ticket, the same
# one today's bot makes, with no exception escaping.
#
# The ticket flow tests need TEST_DATABASE_URL (a migrated throwaway
# Postgres); the rest run without it.
import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx
import openai

import tests  # noqa: F401 (sets the fake environment first)
from tests.helpers import config
from tests.helpers import row
from tests.test_flow import FakeSlack

HAS_DB = bool(os.environ.get("TEST_DATABASE_URL"))
REQUEST = httpx.Request("POST", "https://ai.example/v1/chat/completions")


def status_error(cls, code: int):
    return cls(
        f"error {code}",
        response=httpx.Response(code, request=REQUEST),
        body={"error": {"message": f"error {code}"}},
    )


class FakeAI:
    """Stands in for env.ai_client. `outcomes` is what each call does in
    turn: an exception to raise, or the reply content to return."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.options: dict = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **options):
        self.options = options
        return self

    async def _create(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "NO_CHOICES":
            return SimpleNamespace(choices=[])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))]
        )


GOOD = json.dumps({"route": "status", "confidence": 0.9})


class AIFailureTests(unittest.IsolatedAsyncioTestCase):
    """The classifier itself: every failure is a plain human route."""

    async def classify_with(self, ai: FakeAI):
        from nephthys.utils.env import env
        from nephthys.utils.help_bot import classify as classify_mod

        with (
            mock.patch.object(env, "ai_client", ai),
            mock.patch.object(classify_mod, "AI_RETRY_PAUSE_SECONDS", 0),
            mock.patch.object(classify_mod, "report", mock.AsyncMock()) as report,
        ):
            result = await classify_mod.classify("did my game get approved?", [])
        return result, report

    async def test_out_of_credits_402(self):
        ai = FakeAI(status_error(openai.APIStatusError, 402))
        result, report = await self.classify_with(ai)
        self.assertEqual(result.route, "human")
        self.assertEqual(ai.calls, 1)  # no retry
        self.assertIn("ai 402", report.call_args.args[0])

    async def test_bad_key_401(self):
        ai = FakeAI(status_error(openai.AuthenticationError, 401))
        result, _ = await self.classify_with(ai)
        self.assertEqual((result.route, ai.calls), ("human", 1))

    async def test_rate_limited_429_retries_once(self):
        ai = FakeAI(status_error(openai.RateLimitError, 429))
        result, _ = await self.classify_with(ai)
        self.assertEqual((result.route, ai.calls), ("human", 2))

    async def test_429_then_ok(self):
        ai = FakeAI(status_error(openai.RateLimitError, 429), GOOD)
        result, _ = await self.classify_with(ai)
        self.assertEqual((result.route, ai.calls), ("status", 2))

    async def test_server_error_retries_once(self):
        ai = FakeAI(status_error(openai.InternalServerError, 503))
        result, _ = await self.classify_with(ai)
        self.assertEqual((result.route, ai.calls), ("human", 2))

    async def test_timeout_and_connection_error_no_retry(self):
        for error in [
            openai.APITimeoutError(request=REQUEST),
            openai.APIConnectionError(request=REQUEST),
            RuntimeError("anything at all"),
        ]:
            ai = FakeAI(error)
            result, _ = await self.classify_with(ai)
            self.assertEqual((result.route, ai.calls), ("human", 1), error)

    async def test_short_timeout_and_no_sdk_retries(self):
        ai = FakeAI(GOOD)
        await self.classify_with(ai)
        self.assertLessEqual(ai.options["timeout"], 15)
        self.assertEqual(ai.options["max_retries"], 0)

    async def test_garbage_replies(self):
        for content in [
            "",
            None,
            "NO_CHOICES",
            "sure thing!",
            "{not json",
            "[1, 2]",
            '{"route": 5}',
            '{"route": "banana", "confidence": 1}',
            '{"route": "faq", "faq": ["a list"], "confidence": 0.9}',
            '{"route": "jam_facts", "facts": "form", "confidence": 0.9}',
            '{"route": "status", "confidence": "NaN"}',
            '{"route": "status", "confidence": {"a": 1}}',
        ]:
            result, _ = await self.classify_with(FakeAI(content))
            self.assertEqual(result.route, "human", content)


class AirtableFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import importlib

        from nephthys.utils.help_bot import airtable

        self.airtable = importlib.reload(airtable)  # undo any test patches

    async def test_http_errors_and_timeouts(self):
        from nephthys.utils.env import env

        class Response:
            def __init__(self, status, body):
                self.status, self.body = status, body

            async def json(self):
                if isinstance(self.body, Exception):
                    raise self.body
                return self.body

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        cases = [
            Response(429, {"errors": [{"error": "RATE_LIMIT_REACHED"}]}),
            Response(500, {"error": "boom"}),
            Response(200, ValueError("not json")),
            Response(200, ["a", "list"]),
            Response(200, {"records": "nope"}),
        ]
        for response in cases:
            session = SimpleNamespace(get=lambda *a, r=response, **k: r)
            with mock.patch.object(env, "session", session, create=True):
                with self.assertRaises(self.airtable.AirtableError):
                    await self.airtable.submissions_for("U0ABCDEFG")
        # timeouts and connection errors
        def boom(*a, **k):
            raise asyncio.TimeoutError()

        with mock.patch.object(env, "session", SimpleNamespace(get=boom), create=True):
            with self.assertRaises(self.airtable.AirtableError):
                await self.airtable.orders_for("U0ABCDEFG")
            self.assertIsNone(await self.airtable.review_jam())

    def test_schema_surprises(self):
        a = self.airtable
        sub = a.submission_from_record(
            {
                "id": "rec1",
                "fields": {
                    "game_title": ["Rooted"],
                    "jam": 202610,
                    "review_status": {"name": "Approved"},
                    "approved_dm_sent_at": 12,
                    "notes": None,
                },
            }
        )
        self.assertEqual((sub.title, sub.jam, sub.status), ("Rooted", "", ""))
        order = a.order_from_record(
            {"id": "recO", "fields": {"fulfilled_by": "a string", "prize": 3}}
        )
        self.assertEqual((order.prize, order.fulfilled_by_email), ("prize", ""))
        self.assertEqual(a.submission_from_record({"id": "r", "fields": None}).title, "your game")


class CanvasAndConfigFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_canvas_failure_keeps_last_good_copy(self):
        from nephthys.utils import faq
        from tests.test_help_bot import FAQ_HTML

        good = faq.parse_faq_canvas(FAQ_HTML)
        with (
            mock.patch.object(faq, "faq_entries", good),
            mock.patch.object(faq, "_download_canvas", mock.AsyncMock(side_effect=RuntimeError("401"))),
            mock.patch("nephthys.utils.help_bot.report.send_heartbeat", mock.AsyncMock()),
        ):
            await faq.refresh_faq_entries()
            self.assertEqual(len(faq.faq_entries), len(good))
        # garbage canvas: parses to nothing, old copy stays
        with (
            mock.patch.object(faq, "faq_entries", good),
            mock.patch.object(faq, "_download_canvas", mock.AsyncMock(return_value="<html>no faq</html>")),
            mock.patch("nephthys.utils.help_bot.report.send_heartbeat", mock.AsyncMock()),
        ):
            await faq.refresh_faq_entries()
            self.assertEqual(len(faq.faq_entries), len(good))
        # never had one: no entries, so the classifier can't pick any
        with (
            mock.patch.object(faq, "faq_entries", []),
            mock.patch.object(faq, "_download_canvas", mock.AsyncMock(side_effect=RuntimeError("down"))),
            mock.patch("nephthys.utils.help_bot.report.send_heartbeat", mock.AsyncMock()),
        ):
            await faq.refresh_faq_entries()
            self.assertEqual(faq.faq_entries, [])
        from nephthys.utils.help_bot.classify import parse_reply

        r = parse_reply('{"route": "faq", "faq": "is AI allowed?", "confidence": 0.9}', [])
        self.assertEqual(r.route, "human")

    async def test_jam_config_failure(self):
        from nephthys.utils.env import env
        from nephthys.utils.help_bot import jam_config

        def boom(*a, **k):
            raise asyncio.TimeoutError()

        with (
            mock.patch.object(jam_config, "_cache", None),
            mock.patch.object(env, "session", SimpleNamespace(get=boom), create=True),
            mock.patch("nephthys.utils.help_bot.report.send_heartbeat", mock.AsyncMock()),
        ):
            self.assertIsNone(await jam_config.get_jam_config())


class ReportingTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_alert_per_kind_per_hour(self):
        from nephthys.utils.help_bot import report as report_mod

        with (
            mock.patch.object(report_mod, "_last_alert", {}),
            mock.patch.object(report_mod, "send_heartbeat", mock.AsyncMock()) as beat,
        ):
            for _ in range(50):
                await report_mod.report("ai 402", "out of credits")
            await report_mod.report("airtable", "down")
            self.assertEqual(beat.call_count, 2)

    async def test_reporting_never_raises(self):
        from nephthys.utils.help_bot import report as report_mod

        with (
            mock.patch.object(report_mod, "_last_alert", {}),
            mock.patch.object(report_mod, "send_heartbeat", mock.AsyncMock(side_effect=RuntimeError("slack down"))),
        ):
            await report_mod.report("x", "y")


class KillSwitchTests(unittest.TestCase):
    def test_switch(self):
        from nephthys.utils.env import env
        from nephthys.utils.env import test_channel_scope

        with test_channel_scope(True):
            self.assertTrue(env.new_flow_enabled())
            for value, disabled in [("true", True), ("1", True), ("false", False), ("typo", True)]:
                with mock.patch.dict(os.environ, {"JAMEGAM_HELP_BOT_DISABLED": value}):
                    self.assertEqual(env.help_bot_disabled, disabled, value)
                    self.assertEqual(env.new_flow_enabled(), not disabled, value)


class FailingSlack(FakeSlack):
    """FakeSlack that fails the calls `fail` picks."""

    def __init__(self, fail):
        super().__init__()
        self.fail = fail

    def __getattr__(self, name):
        async def call(**kwargs):
            self.calls.append((name, kwargs))
            if self.fail(name, kwargs):
                from slack_sdk.errors import SlackApiError

                raise SlackApiError("injected", {"ok": False, "error": "injected"})
            return self.answer_for(name, kwargs)

        return call

    def answer_for(self, name, kwargs):
        return FakeSlack._answer(self, name, kwargs)


@unittest.skipUnless(HAS_DB, "set TEST_DATABASE_URL to a migrated Postgres")
class TicketSurvivesTests(unittest.IsolatedAsyncioTestCase):
    """End to end: each failure still leaves today's normal ticket."""

    counter = 5000

    async def asyncSetUp(self):
        import importlib

        from aiohttp import ClientSession

        from nephthys.database.tables import BotMessage
        from nephthys.database.tables import HelpBotTicket
        from nephthys.database.tables import Ticket
        from nephthys.database.tables import User
        from nephthys.events import help_bot
        from nephthys.utils.env import env
        from nephthys.utils.help_bot import airtable
        from nephthys.utils.help_bot import classify as classify_mod

        importlib.reload(airtable)
        await HelpBotTicket.delete(force=True)
        await BotMessage.delete(force=True)
        await Ticket.delete(force=True)
        await User.delete(force=True)
        await User(slack_id="UHELPER", username="helper", helper=True).save()
        self.env = env
        env.session = ClientSession()
        self.help_bot = help_bot
        self.patches: list = []

        async def rows(slack_id):
            return [row(status="Approved", spotchecked=True)]

        async def no_orders(slack_id):
            return []

        async def review_jam():
            return "2026-10"

        async def live_config():
            return config()

        for target, name, value in [
            (help_bot, "classify", classify_mod.classify),
            (help_bot, "get_jam_config", live_config),
            (help_bot.airtable, "submissions_for", rows),
            (help_bot.airtable, "orders_for", no_orders),
            (help_bot.airtable, "review_jam", review_jam),
            (classify_mod, "AI_RETRY_PAUSE_SECONDS", 0),
            (env, "ai_client", FakeAI(GOOD)),
        ]:
            self.patch(target, name, value)
        self.beats = mock.AsyncMock()
        self.patch(__import__("nephthys.utils.help_bot.report", fromlist=["x"]), "send_heartbeat", self.beats)
        self.patch(__import__("nephthys.utils.help_bot.report", fromlist=["x"]), "_last_alert", {})

    def patch(self, target, name, value):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.env.session.close()

    async def ask(self, slack, text="did my game get approved?") -> Any:
        from nephthys.database.tables import HelpBotTicket
        from nephthys.database.tables import Ticket
        from nephthys.events.message_creation import on_message
        from nephthys.utils.env import set_test_scope

        self.env.slack_client = slack
        TicketSurvivesTests.counter += 1000
        slack.ts = TicketSurvivesTests.counter
        ts = slack._next_ts()

        async def handle():
            set_test_scope(True)
            await on_message({"channel": "CTESTHELP", "user": "UKID", "text": text, "ts": ts}, slack)

        await asyncio.create_task(handle())  # must not raise
        ticket = await Ticket.objects().where(Ticket.msg_ts == ts).first()
        info = (
            await HelpBotTicket.objects().where(HelpBotTicket.ticket == ticket.id).first()
            if ticket
            else None
        )
        return ticket, info

    def assert_normal_ticket(self, slack, ticket):
        """What today's bot does: the ticket row, the ticket post and the
        usual first reply, and the ticket left open."""
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.status, "OPEN")
        posts = slack.named("chat_postMessage")
        self.assertEqual(posts[0]["channel"], "CTESTTICKETS")
        self.assertEqual(posts[1]["channel"], "CTESTHELP")
        self.assertIn("all good now", str(posts[1]["blocks"]))

    async def test_ai_failures_leave_a_normal_ticket(self):
        for error in [
            status_error(openai.APIStatusError, 402),
            status_error(openai.RateLimitError, 429),
            openai.APITimeoutError(request=REQUEST),
            "here's my answer: it's approved!",
        ]:
            self.patch(self.env, "ai_client", FakeAI(error))
            slack = FakeSlack()
            ticket, info = await self.ask(slack)
            self.assert_normal_ticket(slack, ticket)
            self.assertEqual(len(slack.named("chat_postMessage")), 2, error)  # no answer
            self.assertEqual(info.route, "human")

    async def test_airtable_failure(self):
        from nephthys.utils.help_bot.airtable import AirtableError

        async def broken(slack_id):
            raise AirtableError("429 rate limited")

        self.patch(self.help_bot.airtable, "submissions_for", broken)
        slack = FakeSlack()
        ticket, info = await self.ask(slack)
        self.assert_normal_ticket(slack, ticket)
        self.assertEqual(len(slack.named("chat_postMessage")), 2)
        self.assertIn("couldn't read airtable", info.action_summary)
        self.assertIn("couldn't read airtable", str(slack.named("chat_update")[-1]["blocks"]))

    async def test_unexpected_airtable_exception(self):
        async def broken(slack_id):
            raise KeyError("fields")

        self.patch(self.help_bot.airtable, "orders_for", broken)
        slack = FakeSlack()
        ticket, info = await self.ask(slack)
        self.assert_normal_ticket(slack, ticket)
        self.assertIn("couldn't read airtable", info.action_summary)

    async def test_answer_post_fails(self):
        # The usual first reply works, the bot's own answer doesn't
        def fail(name, kw):
            return name == "chat_postMessage" and kw.get("channel") == "CTESTHELP" and "reopen-button" in str(kw.get("blocks"))

        slack = FailingSlack(fail)
        ticket, info = await self.ask(slack)
        self.assert_normal_ticket(slack, ticket)
        self.assertIsNotNone(info)  # still recorded
        self.assertFalse(info.auto_resolved)
        self.assertTrue(self.beats.called)

    async def test_ticket_post_edit_fails(self):
        slack = FailingSlack(lambda name, kw: name == "chat_update")
        ticket, info = await self.ask(slack)
        self.assertIsNotNone(ticket)
        self.assertTrue(info.auto_resolved)  # the answer itself went out fine

    async def test_backend_lines_failing_never_breaks_the_ticket_post(self):
        from nephthys.events.message import send_backend_message

        broken = mock.AsyncMock(side_effect=RuntimeError("db down"))
        with mock.patch("nephthys.events.help_bot.help_bot_context_lines", broken):
            blocks = await send_backend_message.backend_message_blocks("UKID", "1.1", 0)
        self.assertEqual(blocks[-1]["type"], "context")

    async def test_ephemeral_fails_keeps_ticket_open(self):
        from tests.helpers import order

        async def orders(slack_id):
            return [order(status="fulfilled", info="https://t.example/1")]

        self.patch(self.help_bot.airtable, "orders_for", orders)
        self.patch(self.env, "ai_client", FakeAI(json.dumps({"route": "tracking", "confidence": 0.9})))
        slack = FailingSlack(lambda name, kw: name == "chat_postEphemeral")
        ticket, info = await self.ask(slack, "where's my tracking")
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.status, "OPEN")
        self.assertFalse(info.auto_resolved)

    async def test_ping_fails(self):
        from tests.helpers import order

        async def orders(slack_id):
            return [order(status="fulfilled", info="on its way!")]

        self.patch(self.help_bot.airtable, "orders_for", orders)
        self.patch(self.env, "ai_client", FakeAI(json.dumps({"route": "tracking", "confidence": 0.9})))
        slack = FailingSlack(lambda name, kw: name == "chat_postMessage" and kw.get("channel") == "CTESTTICKETS" and kw.get("thread_ts"))
        ticket, info = await self.ask(slack, "where's my tracking")
        self.assert_normal_ticket(slack, ticket)
        self.assertIn("a human on the jame gam team", slack.named("chat_postMessage")[-1]["text"])

    async def test_kill_switch(self):
        with mock.patch.dict(os.environ, {"JAMEGAM_HELP_BOT_DISABLED": "true"}):
            slack = FakeSlack()
            ticket, info = await self.ask(slack)
        self.assert_normal_ticket(slack, ticket)
        self.assertIsNone(info)
        self.assertEqual(len(slack.named("chat_postMessage")), 2)

    async def test_whole_run_timeout(self):
        async def slow(text, entries, context=""):
            await asyncio.sleep(5)

        self.patch(self.help_bot, "classify", slow)
        self.patch(self.help_bot, "RUN_TIMEOUT_SECONDS", 0.05)
        slack = FakeSlack()
        ticket, info = await self.ask(slack)
        self.assert_normal_ticket(slack, ticket)
        self.assertIsNone(info)

    async def test_button_failure_tells_the_clicker(self):
        from nephthys.events.help_bot import handle_need_help_button

        slack = FakeSlack()
        self.env.slack_client = slack
        body = {"actions": [{"value": "not a number"}], "user": {"id": "UKID"}, "channel": {"id": "CTESTHELP"}, "message": {"ts": "1.1"}}
        await handle_need_help_button(body, slack)  # must not raise
        self.assertIn("didn't work", slack.named("chat_postEphemeral")[-1]["text"])

    async def test_delete_task_keeps_going(self):
        from datetime import datetime
        from datetime import timedelta
        from datetime import timezone

        from nephthys.database.tables import HelpBotTicket
        from nephthys.tasks import delete_bot_resolved

        slack = FakeSlack()
        first, _ = await self.ask(slack)
        second, _ = await self.ask(slack, "has my game been reviewed?")
        await HelpBotTicket.update(
            {HelpBotTicket.delete_after: datetime.now(timezone.utc) - timedelta(minutes=1)}
        ).where(HelpBotTicket.auto_resolved.eq(True))

        calls = {"n": 0}

        async def flaky(ticket):
            calls["n"] += 1
            if ticket.id == first.id:
                raise RuntimeError("slack down")

        with mock.patch.object(delete_bot_resolved, "_delete_thread", flaky):
            await delete_bot_resolved.delete_bot_resolved_threads()
        self.assertEqual(calls["n"], 2)
        one = await HelpBotTicket.objects().where(HelpBotTicket.ticket == first.id).first()
        two = await HelpBotTicket.objects().where(HelpBotTicket.ticket == second.id).first()
        self.assertIsNone(one.deleted_at)  # tried again next time
        self.assertIsNotNone(two.deleted_at)

    async def test_already_deleted_counts_as_deleted(self):
        from nephthys.tasks import delete_bot_resolved

        slack = FailingSlack(lambda name, kw: name == "conversations_replies")
        self.env.slack_client = slack
        from nephthys.utils import delete_thread

        self.patch(delete_thread, "client", slack)
        self.patch(self.env, "_workspace_admin_available", True)

        from slack_sdk.errors import SlackApiError

        async def gone(**kwargs):
            raise SlackApiError("gone", {"ok": False, "error": "thread_not_found"})

        self.patch(delete_thread.client, "conversations_replies", gone)
        ticket, _ = await self.ask(FakeSlack())
        await delete_bot_resolved._delete_thread(ticket)  # no exception


@unittest.skipUnless(HAS_DB, "set TEST_DATABASE_URL to a migrated Postgres")
class SameChannelTests(unittest.IsolatedAsyncioTestCase):
    """Test tickets channel == test help channel (both #augie-bot-spam)."""

    async def test_own_ticket_posts_are_not_questions(self):
        from nephthys.database.tables import Ticket
        from nephthys.events.message_creation import on_message
        from nephthys.utils.env import set_test_scope

        before = await Ticket.count()
        slack = FakeSlack()
        for event in [
            # the bot's own ticket post, top level in the same channel
            {"channel": "CTESTHELP", "bot_id": "B1", "user": "UBOT", "text": "New question from <@UKID>", "ts": "9.1"},
            # the fulfiller ping, a bot reply in that post's thread
            {"channel": "CTESTHELP", "bot_id": "B1", "user": "UBOT", "text": "ping", "ts": "9.2", "thread_ts": "9.1"},
            # the bot editing its ticket post
            {"channel": "CTESTHELP", "subtype": "message_changed", "message": {"text": "x"}, "ts": "9.3"},
        ]:
            async def handle(event=event):
                set_test_scope(True)
                await on_message(event, slack)

            await asyncio.create_task(handle())
        self.assertEqual(await Ticket.count(), before)
        self.assertEqual(slack.calls, [])


class HttpModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_in_http_mode(self):
        """The live app takes events over HTTP: two requests at once, one per
        channel, each sees its own channels."""
        import urllib.parse

        from slack_bolt.async_app import AsyncApp
        from slack_bolt.authorization import AuthorizeResult
        from slack_bolt.request.async_request import AsyncBoltRequest

        from nephthys.utils.env import env
        from nephthys.utils.slack import route_test_channel

        async def authorize(**kwargs):
            return AuthorizeResult(enterprise_id=None, team_id="T1", bot_token="xoxb-x", bot_user_id="UBOT")

        app = AsyncApp(authorize=authorize, request_verification_enabled=False)
        app.middleware(route_test_channel)
        seen: dict = {}

        @app.event("message")
        async def on_event(event):
            await asyncio.sleep(0.01)
            seen["event " + event["channel"]] = env.slack_help_channel

        @app.action("probe")
        async def probe(ack, body):
            await ack()
            await asyncio.sleep(0.01)
            seen["action " + body["channel"]["id"]] = env.slack_ticket_channel

        def event_request(channel):
            body = {"type": "event_callback", "team_id": "T1", "event": {"type": "message", "channel": channel, "user": "U1", "text": "hi", "ts": "1.1"}}
            return AsyncBoltRequest(body=json.dumps(body), headers={"content-type": ["application/json"]})

        def action_request(channel):
            body = {"type": "block_actions", "team": {"id": "T1"}, "user": {"id": "U1"}, "channel": {"id": channel}, "actions": [{"action_id": "probe", "value": "1", "type": "button"}]}
            return AsyncBoltRequest(
                body="payload=" + urllib.parse.quote(json.dumps(body)),
                headers={"content-type": ["application/x-www-form-urlencoded"]},
            )

        await asyncio.gather(
            *(asyncio.create_task(app.async_dispatch(r)) for r in [
                event_request("CTESTHELP"), event_request("CREALHELP"),
                action_request("CTESTHELP"), action_request("CREALHELP"),
            ])
        )
        await asyncio.sleep(0.1)
        self.assertEqual(
            seen,
            {
                "event CTESTHELP": "CTESTHELP",
                "event CREALHELP": "CREALHELP",
                "action CTESTHELP": "CTESTTICKETS",
                "action CREALHELP": "CREALTICKETS",
            },
        )


class ImpersonationTests(unittest.TestCase):
    def test_forms(self):
        from nephthys.events.help_bot import lookup_slack_id
        from nephthys.utils.env import env
        from nephthys.utils.env import test_channel_scope

        with mock.patch.object(env, "jamegam_test_impersonation", True), test_channel_scope(True):
            for text in ["[as U0ABCDEFG] did i get approved", "did i get approved [as <@U0ABCDEFG>]", "[as <@U0ABCDEFG|kid>] hi"]:
                self.assertEqual(lookup_slack_id(text, "UAUGIE", True), "U0ABCDEFG", text)
            # not a helper, or not the test channel: their own rows
            self.assertEqual(lookup_slack_id("[as U0ABCDEFG]", "UKID", False), "UKID")
        with mock.patch.object(env, "jamegam_test_impersonation", True):
            self.assertEqual(lookup_slack_id("[as U0ABCDEFG]", "UAUGIE", True), "UAUGIE")


if __name__ == "__main__":
    unittest.main()
