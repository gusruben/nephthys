# pyright: reportArgumentType=false, reportOptionalMemberAccess=false, reportOptionalOperand=false
# End-to-end ticket flow against a real (local, throwaway) Postgres, with a
# fake Slack client and stubbed AI / Airtable / jam config.
#
# Needs TEST_DATABASE_URL pointing at a migrated database; skipped otherwise.
# It checks that the real help channel behaves exactly as before, and that the
# test channel gets the new flow without leaking into stats.
import asyncio
import os
import unittest
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any

import tests  # noqa: F401 (sets the fake environment first)
from tests.helpers import config
from tests.helpers import order
from tests.helpers import row

HAS_DB = bool(os.environ.get("TEST_DATABASE_URL"))


class FakeSlack:
    """Records every call; answers just enough for the bot's code paths."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.ts = 1000

    def _next_ts(self) -> str:
        self.ts += 1
        return f"{self.ts}.000100"

    def __getattr__(self, name):
        async def call(**kwargs):
            self.calls.append((name, kwargs))
            return self._answer(name, kwargs)

        return call

    def _answer(self, name: str, kwargs: dict) -> dict[str, Any]:
        if name == "chat_postMessage":
            msg = {"ts": self._next_ts(), "text": kwargs.get("text")}
            if kwargs.get("thread_ts"):
                msg["thread_ts"] = kwargs["thread_ts"]
            return {"ok": True, "ts": msg["ts"], "message": msg}
        if name == "users_info":
            return {"user": {"name": "kid", "profile": {"display_name": "kid"}}}
        if name == "conversations_members":
            return {"members": ["UHELPER"]}
        if name == "auth_test":
            return {"user_id": "UBOT"}
        if name == "users_lookupByEmail":
            return {"user": {"id": "UGABIN"}}
        if name == "conversations_replies":
            return {"messages": []}
        return {"ok": True}

    def named(self, name: str) -> list[dict]:
        return [kw for n, kw in self.calls if n == name]


@unittest.skipUnless(HAS_DB, "set TEST_DATABASE_URL to a migrated Postgres")
class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import ClientSession

        from nephthys.database.tables import BotMessage
        from nephthys.database.tables import HelpBotTicket
        from nephthys.database.tables import Ticket
        from nephthys.database.tables import User
        from nephthys.events import help_bot
        from nephthys.utils import delete_thread
        from nephthys.utils.env import env

        await HelpBotTicket.delete(force=True)
        await BotMessage.delete(force=True)
        await Ticket.delete(force=True)
        await User.delete(force=True)
        await User(slack_id="UHELPER", username="helper", helper=True).save()

        self.slack = FakeSlack()
        env.slack_client = self.slack  # type: ignore
        delete_thread.client = self.slack  # type: ignore
        env._workspace_admin_available = True
        env.session = ClientSession()
        self.env = env

        self.rows = [row(status="Approved", spotchecked=True)]
        self.orders: list = []
        self.route = "status"
        self.faq_entry = None

        async def fake_classify(text, entries, context=""):
            from nephthys.utils.help_bot.classify import Classification

            return Classification(route=self.route, confidence=0.9, faq=self.faq_entry)

        async def fake_rows(slack_id):
            return self.rows

        async def fake_orders(slack_id):
            return self.orders

        async def fake_config():
            return config()

        async def fake_review_jam():
            return "2026-10"

        help_bot.classify = fake_classify
        help_bot.get_jam_config = fake_config
        help_bot.airtable.submissions_for = fake_rows
        help_bot.airtable.orders_for = fake_orders
        help_bot.airtable.review_jam = fake_review_jam

    async def asyncTearDown(self):
        await self.env.session.close()

    async def ask(self, channel: str, text: str, user: str = "UKID") -> Any:
        """Post a question the way Slack would, in its own task (as Bolt does)."""
        from nephthys.database.tables import Ticket
        from nephthys.events.message_creation import on_message
        from nephthys.utils.env import set_test_scope

        ts = self.slack._next_ts()
        event = {"channel": channel, "user": user, "text": text, "ts": ts}

        async def handle():
            set_test_scope(self.env.is_test_channel(channel))
            await on_message(event, self.slack)  # type: ignore

        await asyncio.create_task(handle())
        return await Ticket.objects().where(Ticket.msg_ts == ts).first()

    async def test_real_channel_is_unchanged(self):
        from nephthys.database.tables import HelpBotTicket

        ticket = await self.ask("CREALHELP", "did my game get approved?")
        self.assertIsNone(ticket.test_channel)
        self.assertEqual(await HelpBotTicket.count(), 0)
        posts = self.slack.named("chat_postMessage")
        self.assertEqual([p["channel"] for p in posts], ["CREALTICKETS", "CREALHELP"])
        backend = posts[0]["blocks"]
        self.assertNotIn("submission:", str(backend))
        self.assertEqual(ticket.status, "OPEN")

    async def test_status_answer_in_test_channel(self):
        from nephthys.database.tables import HelpBotTicket
        from nephthys.database.tables import Ticket

        ticket = await self.ask("CTESTHELP", "did my game get approved?")
        self.assertEqual(ticket.test_channel, "CTESTHELP")
        posts = self.slack.named("chat_postMessage")
        self.assertEqual(
            [p["channel"] for p in posts], ["CTESTTICKETS", "CTESTHELP", "CTESTHELP"]
        )
        answer = posts[2]
        self.assertIn(
            "approved, nice work! your prize dm from <@U0BB0FJU7DM> is coming soon",
            answer["text"],
        )
        self.assertIn("reopen-button", str(answer["blocks"]))
        self.assertIn("'still need help'", str(answer["blocks"]))
        self.assertNotIn("'reopen'", str(answer["blocks"]))
        self.assertIn(
            "this thread will delete itself in 24 hours!", str(answer["blocks"])
        )
        # one grey note, not bot_note plus a footer
        self.assertEqual(
            [b["type"] for b in answer["blocks"]], ["section", "context", "actions"]
        )
        self.assertIn("a helper will take a look :)", str(answer["blocks"]))

        ticket = await Ticket.objects().where(Ticket.id == ticket.id).first()
        self.assertEqual(ticket.status, "CLOSED")
        self.assertIsNone(ticket.closed_by)  # no helper credit
        info = (
            await HelpBotTicket.objects()
            .where(HelpBotTicket.ticket == ticket.id)
            .first()
        )
        self.assertTrue(info.auto_resolved)
        self.assertIsNotNone(info.delete_after)

        # The ticket post was updated with the help bot lines, not deleted
        update = self.slack.named("chat_update")[-1]
        self.assertEqual(update["channel"], "CTESTTICKETS")
        self.assertIn(
            "approved and spotchecked, prize dm not sent", str(update["blocks"])
        )
        self.assertIn("answered their status and resolved it", str(update["blocks"]))
        self.assertFalse(
            [
                d
                for d in self.slack.named("chat_delete")
                if d["channel"] == "CTESTTICKETS"
            ]
        )

    async def test_test_tickets_stay_out_of_stats_and_api(self):
        from starlette.requests import Request

        from nephthys.api.tickets import tickets_list as tickets_api
        from nephthys.utils.stats import calculate_daily_stats
        from nephthys.utils.stats import calculate_overall_stats

        self.route = "human"
        await self.ask("CREALHELP", "real question")
        await self.ask("CTESTHELP", "test question")
        stats = await calculate_overall_stats()
        self.assertEqual(stats.tickets_total, 1)
        now = datetime.now(timezone.utc)
        daily = await calculate_daily_stats(
            now - timedelta(hours=1), now + timedelta(hours=1)
        )
        self.assertEqual(daily.new_tickets_total, 1)
        req = Request({"type": "http", "query_string": b"status=open", "headers": []})
        res = await tickets_api(req)
        self.assertEqual(len(__import__("json").loads(res.body)), 1)

    async def test_rejected_is_never_posted(self):
        from nephthys.database.tables import Ticket

        self.rows = [row(status="Rejected")]
        ticket = await self.ask("CTESTHELP", "was mine approved?")
        texts = " ".join(
            p.get("text") or "" for p in self.slack.named("chat_postMessage")
        )
        self.assertNotIn("reject", texts.lower())
        ticket = await Ticket.objects().where(Ticket.id == ticket.id).first()
        self.assertEqual(ticket.status, "OPEN")
        self.assertIn("rejected", str(self.slack.named("chat_update")[-1]["blocks"]))

    async def test_reopen_stops_deletion(self):
        from nephthys.actions.reopen import reopen
        from nephthys.database.tables import HelpBotTicket
        from nephthys.database.tables import Ticket
        from nephthys.database.tables import User
        from nephthys.utils.env import test_channel_scope

        ticket = await self.ask("CTESTHELP", "did my game get approved?")
        old_post = ticket.ticket_ts
        kid = await User.objects().where(User.slack_id == "UKID").first()
        with test_channel_scope(True):
            await reopen(
                await Ticket.objects().get(Ticket.id == ticket.id), kid, self.slack
            )
        info = (
            await HelpBotTicket.objects()
            .where(HelpBotTicket.ticket == ticket.id)
            .first()
        )
        self.assertTrue(info.reopened)
        self.assertIsNone(info.delete_after)
        deletes = self.slack.named("chat_delete")
        self.assertIn(
            ("CTESTTICKETS", old_post), [(d["channel"], d["ts"]) for d in deletes]
        )
        new_post = self.slack.named("chat_postMessage")[-1]
        self.assertEqual(new_post["channel"], "CTESTTICKETS")
        self.assertIn("reopened by the asker", str(new_post["blocks"]))

    async def test_deletes_after_the_window(self):
        from nephthys.database.tables import HelpBotTicket
        from nephthys.tasks.delete_bot_resolved import delete_bot_resolved_threads
        from nephthys.utils import delete_thread

        ticket = await self.ask("CTESTHELP", "did my game get approved?")
        await HelpBotTicket.update(
            {
                HelpBotTicket.delete_after: datetime.now(timezone.utc)
                - timedelta(minutes=1)
            }
        ).where(HelpBotTicket.ticket == ticket.id)
        await delete_bot_resolved_threads()
        deleted = [(d["channel"], d["ts"]) for d in self.slack.named("chat_delete")]
        self.assertIn(("CTESTTICKETS", ticket.ticket_ts), deleted)
        self.assertEqual(
            len([c for c, _ in deleted if c == "CTESTHELP"]), 2
        )  # both bot replies
        queued = []
        while not delete_thread.delete_queue.empty():
            queued.append(delete_thread.delete_queue.get_nowait())
        self.assertIn(("CTESTHELP", ticket.msg_ts), queued)
        info = (
            await HelpBotTicket.objects()
            .where(HelpBotTicket.ticket == ticket.id)
            .first()
        )
        self.assertIsNotNone(info.deleted_at)

    async def test_faq_buttons(self):
        from nephthys.database.tables import HelpBotTicket
        from nephthys.database.tables import Ticket
        from nephthys.events.help_bot import handle_answered_button
        from nephthys.events.help_bot import handle_need_help_button
        from nephthys.utils import faq
        from nephthys.utils.env import test_channel_scope
        from tests.test_help_bot import FAQ_HTML

        faq.faq_entries = faq.parse_faq_canvas(FAQ_HTML)
        self.route = "faq"
        self.faq_entry = faq.find_faq_entry("blahaj")
        ticket = await self.ask("CTESTHELP", "how big is the blahaj")
        answer = self.slack.named("chat_postMessage")[-1]
        self.assertIn("> full sized! (1 meter)", answer["text"])
        self.assertIn("did that help?", answer["text"])
        self.assertIn("a helper will take a look :)", str(answer["blocks"]))
        self.assertFalse(answer["unfurl_links"])
        self.assertIn("help-bot-answered", str(answer["blocks"]))

        body = {
            "actions": [{"value": str(ticket.id)}],
            "user": {"id": "UKID"},
            "channel": {"id": "CTESTHELP"},
            "message": {
                "ts": "1.1",
                "text": answer["text"],
                "blocks": answer["blocks"],
            },
        }
        with test_channel_scope(True):
            await handle_need_help_button(body, self.slack)  # type: ignore
        info = (
            await HelpBotTicket.objects()
            .where(HelpBotTicket.ticket == ticket.id)
            .first()
        )
        self.assertTrue(info.still_need_help)
        # The buttons come off the answer, with nothing in their place
        cleared = [
            u for u in self.slack.named("chat_update") if u["channel"] == "CTESTHELP"
        ][-1]
        self.assertEqual([b["type"] for b in cleared["blocks"]], ["section", "context"])
        self.assertIn("didn't help", str(self.slack.named("chat_update")[-1]["blocks"]))

        # Someone else can't use the buttons
        stranger = dict(body, user={"id": "USTRANGER"})
        from nephthys.database.tables import User

        await User(slack_id="USTRANGER", username="s").save()
        with test_channel_scope(True):
            await handle_answered_button(stranger, self.slack)  # type: ignore
        self.assertIn(
            "only the person who asked can use these buttons",
            self.slack.named("chat_postEphemeral")[-1]["text"],
        )

        with test_channel_scope(True):
            await handle_answered_button(body, self.slack)  # type: ignore
        ticket = await Ticket.objects().where(Ticket.id == ticket.id).first()
        self.assertEqual(ticket.status, "CLOSED")

    async def test_old_jam_gets_no_status_reply(self):
        from nephthys.database.tables import Ticket

        self.rows = [row(jam="2026-09", status="Approved")]
        ticket = await self.ask(
            "CTESTHELP", "i never got a dm about my submission from last jam"
        )
        posts = self.slack.named("chat_postMessage")
        self.assertEqual(len(posts), 2)  # ticket post + the usual first reply
        ticket = await Ticket.objects().where(Ticket.id == ticket.id).first()
        self.assertEqual(ticket.status, "OPEN")
        post = str(self.slack.named("chat_update")[-1]["blocks"])
        self.assertIn("the cozy fall jam (2026-09)", post)
        self.assertIn("nothing from them in the jam under review", post)

    async def test_jam_dates_unfurl(self):
        from nephthys.events import help_bot

        async def classify(text, entries, context=""):
            from nephthys.utils.help_bot.classify import Classification

            return Classification(
                route="jam_facts", confidence=0.9, facts=["jam_dates"]
            )

        help_bot.classify = classify
        await self.ask("CTESTHELP", "when is the next jam?")
        answer = self.slack.named("chat_postMessage")[-1]
        self.assertIn("the mfg gambler's jam", answer["text"])
        self.assertTrue(answer["unfurl_links"])

    async def test_tracking_ping(self):
        self.route = "tracking"
        self.orders = [order(status="fulfilled", info="on its way!")]
        self.rows = [
            row(
                status="Approved", dm_sent_at=datetime(2026, 10, 1, tzinfo=timezone.utc)
            )
        ]
        ticket = await self.ask("CTESTHELP", "where's my tracking?")
        posts = self.slack.named("chat_postMessage")
        ping = [
            p for p in posts if p["channel"] == "CTESTTICKETS" and p.get("thread_ts")
        ]
        self.assertEqual(len(ping), 1)
        self.assertTrue(ping[0]["text"].startswith("<@UGABIN> <@UKID>"))
        self.assertIn("a human on the jame gam team will reply", posts[-1]["text"])
        self.assertIn("help-bot-need-help", str(posts[-1]["blocks"]))
        self.assertEqual(ticket.status, "OPEN")

    async def test_middleware_routes_each_request(self):
        """Two requests at once, one from each channel, each sees its own."""
        from slack_bolt.async_app import AsyncApp
        from slack_bolt.authorization import AuthorizeResult
        from slack_bolt.request.async_request import AsyncBoltRequest

        from nephthys.utils.slack import route_test_channel

        async def authorize(**kwargs):
            return AuthorizeResult(
                enterprise_id=None, team_id="T1", bot_token="xoxb-x", bot_user_id="UBOT"
            )

        app = AsyncApp(authorize=authorize, request_verification_enabled=False)
        app.middleware(route_test_channel)
        seen: dict[str, str] = {}

        @app.action("probe")
        async def probe(ack, body):
            await ack()
            await asyncio.sleep(0.01)
            seen[body["channel"]["id"]] = self.env.slack_help_channel

        def request(channel):
            import json

            body = {
                "type": "block_actions",
                "team": {"id": "T1"},
                "user": {"id": "U1"},
                "channel": {"id": channel},
                "actions": [{"action_id": "probe", "value": "1", "type": "button"}],
            }
            return AsyncBoltRequest(
                body="payload="
                + __import__("urllib.parse").parse.quote(json.dumps(body)),
                mode="socket_mode",
            )

        await asyncio.gather(
            asyncio.create_task(app.async_dispatch(request("CTESTHELP"))),
            asyncio.create_task(app.async_dispatch(request("CREALHELP"))),
        )
        await asyncio.sleep(0.1)
        self.assertEqual(seen, {"CTESTHELP": "CTESTHELP", "CREALHELP": "CREALHELP"})


if __name__ == "__main__":
    unittest.main()
