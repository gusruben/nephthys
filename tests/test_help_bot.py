# pyright: reportArgumentType=false, reportOptionalMemberAccess=false, reportOptionalOperand=false
import re
import unittest
from datetime import timedelta
from pathlib import Path

import tests  # noqa: F401 (sets the fake environment first)
from nephthys.utils import faq
from nephthys.utils.env import env
from nephthys.utils.env import test_channel_scope
from nephthys.utils.help_bot.classify import apply_guards
from nephthys.utils.help_bot.classify import Classification
from nephthys.utils.help_bot.classify import DIRECT_THEME
from nephthys.utils.help_bot.classify import parse_reply
from nephthys.utils.help_bot.jam_config import jam_facts_text
from nephthys.utils.help_bot.status import has_tracking
from nephthys.utils.help_bot.status import status_outcome
from nephthys.utils.help_bot.status import submission_summary
from nephthys.utils.help_bot.status import tracking_outcome
from tests.helpers import config
from tests.helpers import order
from tests.helpers import row
from tests.helpers import utc

FAQ_HTML = (Path(__file__).parent / "fixtures" / "jamegam_faq.html").read_text()
NOW = utc(2026, 11, 5, 18)
DELETE_AFTER = NOW + timedelta(hours=24)


def status(rows, orders=(), cfg=None, review_jam="2026-10"):
    return status_outcome(
        list(rows), list(orders), cfg or config(), review_jam, DELETE_AFTER
    )


class FaqParserTests(unittest.TestCase):
    def setUp(self):
        self.entries = faq.parse_faq_canvas(FAQ_HTML)
        faq.faq_entries = self.entries

    def test_reads_every_heading(self):
        self.assertEqual(len(self.entries), 22)
        self.assertEqual(
            self.entries[0].question, "how do i know if my project is approved?"
        )

    def test_keeps_links_and_mentions(self):
        by_q = {e.question: e for e in self.entries}
        self.assertIn(
            "<@U0BB0FJU7DM>", by_q["how do i know if my project is approved?"].answer
        )
        self.assertIn(
            "<https://hackclub.slack.com/archives/C0BBFQASBV2/p1785118322552799|this thread>",
            by_q[
                "my code is too big to upload to github, what do i do? (i'm using unity)"
            ].answer,
        )
        self.assertIn(
            "<https://jamegam.hackclub.com/#faq-effort|this rule>",
            by_q["can i get a prize just by doing art or music?"].answer,
        )

    def test_struck_text_and_channels(self):
        by_q = {e.question: e for e in self.entries}
        ai = by_q["is AI allowed?"].answer
        self.assertNotIn("20%", ai)  # struck out in the canvas
        self.assertIn("completely disallowed", ai)
        self.assertIn(
            "<#C0BBDUEMF0E>", by_q["i missed the deadline to submit to the jam!"].answer
        )

    def test_section_ids_deep_link(self):
        entry = self.entries[0]
        self.assertTrue(entry.section_id.startswith("temp:C:"))
        self.assertIn("?focus_section_id=temp:C:", faq.faq_section_url(entry))

    def test_topic_lookup(self):
        self.assertEqual(
            faq.find_faq_entry("blahaj").question, "what size is the blåhaj?"
        )
        self.assertEqual(faq.find_faq_entry("AI").question, "is AI allowed?")
        self.assertEqual(faq.find_faq_entry("teams").question, "how do teams work?")
        self.assertIn("github", faq.find_faq_entry("github too big").question)
        self.assertIsNone(faq.find_faq_entry("zzzz qqqq"))

    def test_bullet_layout_still_works(self):
        html = '<ul><li id="q1">Is it free?</li><ul><li>yes</li></ul></ul>'
        [entry] = faq.parse_faq_canvas(html)
        self.assertEqual((entry.question, entry.answer), ("Is it free?", "- yes"))


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.entries = faq.parse_faq_canvas(FAQ_HTML)
        faq.faq_entries = self.entries

    def test_parses_faq_pick(self):
        r = parse_reply(
            '{"route": "faq", "faq": "is AI allowed?", "facts": [], "confidence": 0.9}',
            self.entries,
        )
        self.assertEqual(r.route, "faq")
        self.assertEqual(r.faq.question, "is AI allowed?")

    def test_unknown_faq_becomes_human(self):
        r = parse_reply(
            '{"route": "faq", "faq": "made up?", "confidence": 0.9}', self.entries
        )
        self.assertEqual(r.route, "human")

    def test_garbage_becomes_human(self):
        self.assertEqual(parse_reply("sure! it's an faq", self.entries).route, "human")
        self.assertEqual(
            parse_reply('{"route": "banana"}', self.entries).route, "human"
        )

    def test_jam_facts_needs_facts(self):
        r = parse_reply(
            '{"route": "jam_facts", "facts": ["moon"], "confidence": 1}', self.entries
        )
        self.assertEqual(r.route, "human")
        r = parse_reply(
            '{"route": "jam_facts", "facts": ["form"], "confidence": 1}', self.entries
        )
        self.assertEqual((r.route, r.facts), ("jam_facts", ["form"]))

    def test_reads_older_jam_and_item(self):
        r = parse_reply(
            '{"route": "tracking", "item": "blahaj", "older_jam": true, "confidence": 0.9}',
            self.entries,
        )
        self.assertEqual((r.item, r.older_jam), ("blahaj", True))

    def test_older_jam_goes_to_a_human(self):
        for route in ["status", "faq"]:
            r = Classification(
                route=route, confidence=0.9, older_jam=True, faq=self.entries[0]
            )
            out = apply_guards("did anyone get a dm for the cozy fall jam? i didn't", r)
            self.assertEqual((out.route, out.reason), ("human", "older_jam"), route)
        # prizes from an older jam too: no order lookup
        r = Classification(route="tracking", confidence=0.9, older_jam=True)
        self.assertEqual(apply_guards("where's my gmtk blahaj", r).route, "human")

    def test_low_confidence_is_human(self):
        r = Classification(route="status", confidence=0.4)
        self.assertEqual(apply_guards("did i get approved", r).route, "human")

    def test_judgment_calls_never_get_faq_answers(self):
        entry = self.entries[0]
        for text in [
            "can i appeal my rejection?",
            "i missed the form deadline, can i still submit?",
            "can i change my address on my order",
            "will i have to pay customs on the blahaj",
            "i submitted late because of exams, is there an exception?",
        ]:
            r = Classification(route="faq", confidence=0.95, faq=entry)
            self.assertEqual(apply_guards(text, r).route, "human", text)

    def test_ordinary_questions_pass(self):
        entry = self.entries[0]
        r = Classification(route="faq", confidence=0.9, faq=entry)
        self.assertEqual(
            apply_guards("how do i know if it's approved or rejected?", r).route, "faq"
        )


class StatusTests(unittest.TestCase):
    def test_unreviewed(self):
        out = status([row()])
        self.assertTrue(out.resolve)
        self.assertIn("not reviewed yet", out.answer)
        self.assertNotIn("before yours", out.answer)  # no queue count
        self.assertIn("deletes", out.action)

    def test_not_spotchecked_reads_as_in_review(self):
        for r in [
            row(status="Approved"),
            row(status="Prize Only"),
            row(status="Approved", dm_queued=True),
        ]:
            out = status([r])
            self.assertTrue(out.resolve)
            self.assertIn("not reviewed yet", out.answer)
            self.assertNotIn("approved", out.answer)
            # helpers still see the real state
            self.assertIn("approved, waiting on the spotcheck", out.summary)

    def test_approved_once_spotchecked(self):
        for r in [
            row(status="Approved", spotchecked=True),
            row(status="Approved", spotchecked=True, dm_queued=True),
            row(status="Prize Only", spotchecked=True),
        ]:
            out = status([r])
            self.assertTrue(out.resolve)
            self.assertIn(
                "your prize dm from <@U0BB0FJU7DM> is coming soon", out.answer
            )
            self.assertNotIn("prize only", (out.answer + out.summary).lower())

    def test_dm_sent(self):
        for r in [
            row(status="Approved", spotchecked=True, dm_sent_at=utc(2026, 11, 3, 15)),
            row(status="Prize Only", dm_sent_at=utc(2026, 10, 3, 15)),
        ]:
            out = status([r], orders=[order(status="fulfilled")])
            self.assertIn("check your dms from <@U0BB0FJU7DM>", out.answer)
            self.assertNotIn("picked", out.answer)  # orders are tracking's job

    def test_older_jam_gets_no_reply(self):
        # "i never got a dm about my submission from last jam"
        out = status([row(jam="2026-09", status="Approved")], review_jam="2026-10")
        self.assertIsNone(out.answer)
        self.assertEqual(out.reason, "not_current")
        self.assertIn("2026-10", out.action)
        self.assertIn("the cozy fall jam (2026-09)", out.summary)  # helper context

    def test_unknown_review_jam_is_human(self):
        self.assertEqual(status([row()], review_jam=None).reason, "not_current")

    def test_current_jam_only(self):
        out = status([row(jam="2026-09", status="Rejected"), row(id="r2", status="")])
        self.assertTrue(out.resolve)
        self.assertIn("not reviewed yet", out.answer)

    def test_rejections_never_posted(self):
        for s in ["Rejected", "No Prize"]:
            out = status([row(status=s)])
            self.assertIsNone(out.answer, s)
            self.assertFalse(out.resolve)
            self.assertIn("rejected", out.summary)

    def test_unclear_states_go_to_a_human(self):
        for r in [
            row(status="Pending", notes="pending (ari, 2026-11-01): need a video"),
            row(status="Needs Augie"),
            row(status="Stickers Only"),
            row(status="Something New"),
        ]:
            out = status([r])
            self.assertIsNone(out.answer, r.status)
            self.assertEqual(out.reason, "unclear")
        self.assertIn(
            "need a video",
            status(
                [row(status="Pending", notes="a\npending (ari): need a video")]
            ).summary,
        )

    def test_no_rows_and_withdrawn(self):
        self.assertEqual(status([]).reason, "no_rows")
        self.assertEqual(status([row(status="Withdrawn")]).reason, "only_withdrawn")

    def test_withdrawn_copy_is_ignored(self):
        out = status(
            [
                row(status="Withdrawn"),
                row(id="rec2", status="Approved", spotchecked=True),
            ]
        )
        self.assertIn("approved", out.answer)

    def test_conflicting_rows(self):
        out = status([row(status="Approved"), row(id="rec2", status="")])
        self.assertEqual(out.reason, "conflict")

    def test_duplicate_rows_fold(self):
        out = status([row(status="Approved"), row(id="rec2", status="Approved")])
        self.assertIn("here's what i can see for *Rooted*", out.answer)

    def test_two_games(self):
        out = status(
            [
                row(status="Approved", spotchecked=True),
                row(id="rec2", title="Second", code_url="https://github.com/a/second"),
            ]
        )
        self.assertIn("- *Rooted*: approved, nice work!", out.answer)
        self.assertIn("- *Second*: submitted", out.answer)

    def test_game_links_to_itch(self):
        url = "https://kid.itch.io/rooted"
        out = status([row(status="Approved", playable_url=url)])
        self.assertIn(f"here's what i can see for <{url}|Rooted>:", out.answer)
        self.assertIn(f"<{url}|Rooted> approved", out.summary)
        # no link (or a broken one): plain bold
        for bad in ["", "kid.itch.io/rooted", "https://x|y"]:
            out = status([row(status="Approved", playable_url=bad)])
            self.assertIn("here's what i can see for *Rooted*:", out.answer, bad)
        out = status([row(title="a <b> & c", playable_url=url)])
        self.assertIn(f"<{url}|a &lt;b&gt; &amp; c>", out.answer)

    def test_summary_names_the_jam(self):
        s = submission_summary([row(status="Approved")], [order()], config())
        self.assertIn("the jame gam game jam (2026-10)", s)
        self.assertIn("approved, waiting on the spotcheck", s)
        self.assertIn("order: blahaj plushie, not fulfilled", s)


class TrackingTests(unittest.TestCase):
    def test_has_tracking(self):
        yes = [
            order(
                info="https://tools.usps.com/go/TrackConfirmAction?tLabels=9334611043900019711853"
            ),
            order(info="tracking 1Z999AA10123456784"),
            order(prize="steam gift card", info="Check your DMs from <@U07F2QA059B>!"),
            order(prize="minecraft", info="Code sent in DM by @Gabin "),
            order(prize_type="indie games", info="sent!"),
        ]
        no = [
            order(info=""),
            order(
                info="Ordered but not shipped yet, DM Gabin on Slack in a few days for the tracking link."
            ),
            order(info="wiley.com order# CR, will send tracking when I have it"),
            order(
                info="Heya! This order has been fulfilled and is on it's way to you!"
            ),
        ]
        for o in yes:
            self.assertTrue(has_tracking(o), o.info)
        for o in no:
            self.assertFalse(has_tracking(o), o.info)

    def test_not_shipped(self):
        out = tracking_outcome([row()], [order()], config(), DELETE_AFTER)
        self.assertTrue(out.resolve)
        self.assertEqual(
            out.answer, "i checked on your 'blahaj plushie': it hasn't been sent yet"
        )

    def test_fulfilled_with_tracking_is_private(self):
        o = order(
            status="fulfilled",
            info="https://tools.usps.com/x\nsent to kid@example.com",
            fulfilled_dm_sent_at=utc(2026, 10, 20, 15),
        )
        out = tracking_outcome([row()], [o], config(), DELETE_AFTER)
        self.assertTrue(out.resolve)
        self.assertIn("october 20", out.answer)
        self.assertNotIn("example.com", out.answer)
        self.assertIn("kid@example.com", out.private)

    def test_fulfilled_without_tracking_pings(self):
        o = order(
            status="fulfilled",
            info="on its way!",
            fulfilled_dm_sent_at=utc(2026, 10, 20),
        )
        out = tracking_outcome([row()], [o], config(), DELETE_AFTER)
        self.assertFalse(out.resolve)
        self.assertIs(out.ping_order, o)
        self.assertIn("a human on the jame gam team will reply", out.answer)

    def test_no_contact_goes_to_human(self):
        o = order(
            status="fulfilled", info="", fulfilled_by_name="", fulfilled_by_email=""
        )
        self.assertEqual(
            tracking_outcome([row()], [o], config(), DELETE_AFTER).reason,
            "no_tracking_contact",
        )

    ORDERS = [
        order(id="a", jam="2026-07", status="fulfilled", info="https://t.co/x"),
        order(id="b", jam="2026-10", prize="kodak charmera", status="processing"),
        order(id="c", jam="2026-09", prize="hack club t-shirt", status="not fulfilled"),
    ]

    def test_prefix_match(self):
        from nephthys.utils.help_bot.status import matches_item

        self.assertTrue(matches_item(order(prize="godot plush"), "plushie"))
        self.assertTrue(matches_item(order(prize="8bitdo controller"), "controllers"))
        self.assertFalse(matches_item(order(prize="kodak charmera"), "car"))
        self.assertFalse(matches_item(order(prize="hack club hoodie"), "t-shirt"))

    def test_newest_jam_by_default(self):
        out = tracking_outcome([row()], self.ORDERS, config(), DELETE_AFTER)
        self.assertTrue(out.resolve)
        self.assertIn("kodak charmera", out.answer)
        self.assertNotIn("blahaj", out.answer)

    def test_named_item(self):
        for item, prize in [
            ("blåhaj", "blahaj plushie"),
            ("hack club shirt", "hack club t-shirt"),
            ("charmera", "kodak charmera"),
            ("plushie", "blahaj plushie"),
        ]:
            out = tracking_outcome(
                [row()], self.ORDERS, config(), DELETE_AFTER, item=item
            )
            self.assertIn(prize, out.answer, item)
            self.assertEqual(out.answer.count("your "), 1, item)
        out = tracking_outcome([row()], self.ORDERS, config(), DELETE_AFTER, item="ps5")
        self.assertEqual(out.reason, "no_matching_order")
        self.assertIn("ps5", out.action)


class JamFactsTests(unittest.TestCase):
    def test_before_the_jam(self):
        text = jam_facts_text(config(), ["jam_dates", "theme", "form"], now=NOW)
        self.assertIn("the next jam is *the mfg gambler's jam*", text)
        self.assertIn("saturday, december 5 at 6pm ET", text)
        self.assertIn("the theme will be announced when the jam starts", text)
        self.assertIn("you'll have 4 days after the jam ends", text)

    def test_during_the_jam(self):
        text = jam_facts_text(config(), ["jam_dates", "theme"], now=utc(2026, 12, 7))
        self.assertIn("the current jam is", text)
        self.assertIn("the theme is already out", text)

    def test_form_open_until_end_plus_window(self):
        cfg = config(submitUrl="https://forms.example/jg")
        self.assertIn(
            "it closes thursday, december 17 at 12pm ET",
            jam_facts_text(cfg, ["form"], now=utc(2026, 12, 14)),
        )
        # After the window: no answer, a normal ticket
        self.assertIsNone(jam_facts_text(cfg, ["form"], now=utc(2026, 12, 18)))
        # No window set: can't say when it closes, so no answer
        self.assertIsNone(
            jam_facts_text(
                config(submitUrl="https://f", submitWindowDays=None),
                ["form"],
                now=utc(2026, 12, 14),
            )
        )

    def test_after_the_jam(self):
        text = jam_facts_text(config(), ["jam_dates", "theme"], now=utc(2026, 12, 15))
        self.assertIn("wrapped up", text)
        self.assertNotIn("theme", text)

    def test_nothing_answerable(self):
        self.assertIsNone(jam_facts_text(config(), ["form"], now=utc(2026, 12, 15)))
        self.assertIsNone(jam_facts_text(config(), ["pick_window"], now=NOW))


class CaseRunGuardTests(unittest.TestCase):
    """Rules tightened after the 330-case run (claude-workspace/test-gen)."""

    def guard(self, text, route, **kw):
        return apply_guards(text, Classification(route=route, confidence=0.9, **kw))

    def test_joining_late_is_not_a_late_submission(self):
        for text in ["am i too late to join this thing?", "is it too late to start"]:
            out = self.guard(text, "jam_facts", facts=["jam_dates"])
            self.assertEqual(out.route, "jam_facts", text)
        self.assertEqual(
            self.guard(
                "i submitted too late, does it still count", "faq", faq=None
            ).route,
            "human",
        )

    def test_missed_pick_window_is_human(self):
        for text in [
            "i think the shop already closed for me and i never picked anything",
            "i missed the pick window, can i still choose",
        ]:
            self.assertEqual(self.guard(text, "faq").route, "human", text)

    def test_delivered_but_missing_is_human(self):
        for text in [
            "tracking says delivered but i never got anything",
            "it's been marked as delivered for a week now and theres nothing here",
            "my order shows 'fulfilled' but it literally never showed up",
            "my blahaj arrived damaged",
        ]:
            self.assertEqual(self.guard(text, "tracking").route, "human", text)

    def test_own_row_ask_is_status(self):
        for text in [
            "my friend said he got approved already is mine done too",
            "my teammate got the dm, was mine approved?",
            "has my game been reviewed yet? my friend's was",
            "my teammate got approved, what about mine",
        ]:
            out = self.guard(text, "faq")
            self.assertEqual(out.route, "status", text)

    def test_process_questions_stay_faq(self):
        for text in [
            "is it normal for my teammate to get a dm and i havent gotten one?",
            "do you send something when you've looked at our game, or do we never find out",
            "why would only one of us get contacted if we submitted the same game together",
            "how long does review usually take",
        ]:
            self.assertEqual(self.guard(text, "faq").route, "faq", text)

    def test_past_jams_get_no_jam_facts(self):
        for text in [
            "how long did the october jam run for",
            "when did last months jam end, trying to remember",
            "when was the very first jame gam",
        ]:
            out = self.guard(text, "jam_facts", facts=["jam_dates"])
            self.assertEqual(out.route, "human", text)
        for text in [
            "when does the next jam start",
            "is there a jam going on right now",
        ]:
            out = self.guard(text, "jam_facts", facts=["jam_dates"])
            self.assertEqual(out.route, "jam_facts", text)

    def test_unfairness_and_currency_are_human(self):
        for text in [
            "its kinda sus that another team got approved in 2 hours, is review rigged",
            "this feels unfair, mine has been waiting forever",
            "can the steam gift card be sent in a different currency",
        ]:
            for route in ("faq", "status"):
                self.assertEqual(self.guard(text, route).route, "human", text)

    def test_plain_tracking_questions_pass(self):
        for text in [
            "has my prize shipped yet? still nothing",
            "my order shows fulfilled, can i get the tracking?",
            "it's been 3 weeks and my blahaj never arrived",
        ]:
            self.assertEqual(self.guard(text, "tracking").route, "tracking", text)


class ThemeStrictnessTests(unittest.TestCase):
    DIRECT = [
        "what's the theme?",
        "what is the theme for this jam",
        "when will the theme be announced?",
        "when does the theme drop",
        "where will the jam theme be announced?",
        "is the theme out yet",
        "theme?",
        "when do we get the theme",
        "whats the theme for this jam",
        "has the theme dropped yet or we still waiting",
        "whats this jams theme, i cant find it posted anywhere",
        "when do we find out the theme, same moment the jam starts?",
        "people keep talking about a theme reveal, has it happened",
    ]
    NOT_DIRECT = [
        "does my game have to fit the theme?",
        "can i make a game that ignores the theme",
        "is my idea ok for the theme? its a space game",
        "what does the theme mean",
        "Can you make any game you want for the next Jam, or does it have to be about a specific theme?",
        "Guys can you explain the theme and an example project",
        "my game kinda drifted from the theme is that ok",
        "my teammates hate the theme, can we just reskin an old project once it drops",
        "Will the project get rejected if it doesn't fully match the theme?",
        "i picked a THEME and after i make a game how can i send it",
    ]

    def test_direct_theme_questions(self):
        for text in self.DIRECT:
            self.assertTrue(DIRECT_THEME.search(text), text)
        for text in self.NOT_DIRECT:
            self.assertFalse(DIRECT_THEME.search(text), text)

    def test_theme_mentions_go_to_a_human(self):
        for text in self.NOT_DIRECT:
            r = Classification(route="jam_facts", confidence=0.9, facts=["theme"])
            self.assertEqual(apply_guards(text, r).route, "human", text)

    def test_other_facts_survive(self):
        r = Classification(
            route="jam_facts", confidence=0.9, facts=["jam_dates", "theme"]
        )
        out = apply_guards("when does the next jam start, and does it need a theme?", r)
        self.assertEqual((out.route, out.facts), ("jam_facts", ["jam_dates"]))

    def test_direct_theme_kept(self):
        r = Classification(route="jam_facts", confidence=0.9, facts=["theme"])
        self.assertEqual(
            apply_guards("when does the theme come out?", r).facts, ["theme"]
        )


EMOJI = re.compile(r":[a-z0-9_+\-]+:|[\U0001F300-\U0001FAFF☀-➿]")
EMOTICON = re.compile(r"(?<!\w)[:;]-?[)(DPp3]|\^[-_~]?\^|<3")


class CopyRulesTests(unittest.TestCase):
    """Augie's copy rules, checked on every help bot string."""

    def strings(self):
        copy = env.transcript.help_bot
        for name, value in copy.model_dump().items():
            if isinstance(value, dict):
                for key, text in value.items():
                    yield f"{name}.{key}", text
            else:
                yield name, value

    def test_no_dashes_or_middle_dots(self):
        for name, text in self.strings():
            self.assertNotRegex(text, "[—–·]", name)
            self.assertNotIn(" - ", text, name)

    def test_never_prize_only(self):
        for name, text in self.strings():
            self.assertNotIn("prize only", text.lower(), name)

    def test_one_emoji_at_most_and_emoticons_end_paragraphs(self):
        for name, text in self.strings():
            marks = EMOJI.findall(text) + EMOTICON.findall(text)
            self.assertLessEqual(len(marks), 1, name)
            for match in EMOTICON.finditer(text):
                rest = text[match.end() :].split("\n", 1)[0].strip()
                self.assertEqual(rest, "", f"{name}: text after an emoticon")

    def test_every_placeholder_is_filled(self):
        # The templates only use the names the code passes; a typo would
        # raise KeyError at runtime, so render each with dummy values.
        values = {
            k: "x"
            for k in [
                "url",
                "question",
                "answer",
                "user_id",
                "topic",
                "questions",
                "name",
                "start",
                "end",
                "itch_url",
                "closes",
                "days",
                "old_days",
                "title",
                "line",
                "lines",
                "prize",
                "order",
                "date",
                "info",
                "fulfiller",
                "asker",
                "order_url",
                "thread_url",
                "summary",
                "what",
                "when",
                "reason",
                "note",
                "status",
                "jam",
                "item",
                "faq_link",
                "game",
            ]
        }
        for name, text in self.strings():
            text.format(**values)


class ChannelRoutingTests(unittest.TestCase):
    def test_real_channel_by_default(self):
        self.assertEqual(env.slack_help_channel, "CREALHELP")
        self.assertEqual(env.slack_ticket_channel, "CREALTICKETS")
        self.assertFalse(env.new_flow_enabled())

    def test_test_scope(self):
        with test_channel_scope(True):
            self.assertEqual(env.slack_help_channel, "CTESTHELP")
            self.assertEqual(env.slack_ticket_channel, "CTESTTICKETS")
            self.assertTrue(env.new_flow_enabled())
        self.assertEqual(env.slack_help_channel, "CREALHELP")

    def test_channel_lists(self):
        self.assertEqual(env.help_channels(), {"CREALHELP", "CTESTHELP"})
        self.assertTrue(env.is_test_channel("CTESTTICKETS"))
        self.assertFalse(env.is_test_channel("CREALHELP"))
        self.assertFalse(env.is_test_channel(None))


if __name__ == "__main__":
    unittest.main()
