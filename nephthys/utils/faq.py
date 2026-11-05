# Reads the program's FAQ canvas so the bot can quote it.
#
# Ported from hackclub/nephthys-plus (nephthys/utils/faq.py): same canvas parser
# and refresh loop, minus the event-channel list and the Jev decisions call. Two
# additions: links in an answer are kept as Slack links, and an answer can be
# looked up by topic for the `?faq <topic>` macro.
import logging
import re
from html.parser import HTMLParser

from pydantic import BaseModel
from slack_sdk.web.async_client import AsyncWebClient
from thefuzz import fuzz

from nephthys.utils.env import env


class FaqEntry(BaseModel):
    """A single question in the FAQ canvas."""

    section_id: str
    question: str
    answer: str


# Last successfully fetched FAQ, kept if a later refresh fails
faq_entries: list[FaqEntry] = []
# Only log the first of several failures in a row
_last_refresh_failed = False

_EMOJI = re.compile(r":[a-z0-9_+\-]+:")
_MENTION = re.compile(r"@(U[A-Z0-9]{6,})")
_CHANNEL = re.compile(r"(?<![<\w])#(C[A-Z0-9]{6,})")
_HEADING_TAGS = {"h1", "h2", "h3"}
_BLOCK_TAGS = {"p", "td", "th"}
_LINK_TAGS = {"a", "lnk"}
# Struck-out text in the canvas isn't part of the answer
_STRUCK_TAGS = {"del", "s", "strike"}


def _clean(text: str) -> str:
    """Collapse whitespace, drop :emoji: codes and turn @U123 / #C123 into
    a mention / channel link."""
    text = " ".join(_EMOJI.sub("", text).split())
    text = _CHANNEL.sub(r"<#\1>", text)
    return _MENTION.sub(r"<@\1>", text)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _CanvasFaqParser(HTMLParser):
    """Pulls questions and answers out of a canvas's HTML.

    Two layouts are understood, and can be mixed in one canvas:

    - Bullets: each top-level bullet is a question, and the bullets nested
      under it are the answer:
      <ul><li id="...">Question</li><ul><li>Answer</li></ul></ul>
    - Sections: a heading is the question, and the text under it (paragraphs,
      table cells, plain bullets) up to the next heading is the answer. Only
      used for a section that has no bullet questions of its own.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries: list[FaqEntry] = []
        self.ul_depth = 0
        self.li: dict | None = None
        self.question: dict | None = None
        # Section layout
        self.heading: dict | None = None
        self.heading_text: list[str] | None = None
        self.heading_id: str | None = None
        self.block: list[str] | None = None
        self.lines: list[str] = []
        self.section_start = 0
        # Open link, kept so its text becomes <href|text>
        self.link_href: str | None = None
        self.link_text: list[str] = []
        self.struck = 0

    def _buffer(self) -> list[str] | None:
        if self.heading_text is not None:
            return self.heading_text
        if self.li:
            return self.li["text"]
        return self.block

    def handle_starttag(self, tag, attrs):
        if tag in _STRUCK_TAGS:
            self.struck += 1
        elif tag in _HEADING_TAGS:
            self._finish_section()
            self.heading_text = []
            self.heading_id = dict(attrs).get("id")
        elif tag == "ul":
            self.ul_depth += 1
        elif tag == "li":
            self.li = {"id": dict(attrs).get("id"), "text": []}
        elif tag in _BLOCK_TAGS and self.block is None and not self.li:
            self.block = []
        elif tag in _LINK_TAGS:
            href = dict(attrs).get("href")
            if href and self.heading_text is None:
                self.link_href = href
                self.link_text = []

    def handle_endtag(self, tag):
        if tag in _STRUCK_TAGS:
            self.struck = max(0, self.struck - 1)
        elif tag in _LINK_TAGS and self.link_href is not None:
            text = "".join(self.link_text).strip()
            buffer = self._buffer()
            if buffer is not None:
                label = _escape(text) if text else self.link_href
                buffer.append(f"<{self.link_href}|{label}>")
            self.link_href = None
            self.link_text = []
        elif tag in _HEADING_TAGS and self.heading_text is not None:
            text = _clean("".join(self.heading_text))
            self.heading = {"id": self.heading_id, "text": text} if text else None
            self.heading_text = None
        elif tag in _BLOCK_TAGS and self.block is not None:
            text = _clean("".join(self.block))
            if text:
                self.lines.append(text)
            self.block = None
        elif tag == "ul":
            self.ul_depth -= 1
            if self.ul_depth == 0:
                self._finish_question()
        elif tag == "li" and self.li:
            text = _clean("".join(self.li["text"]))
            if self.ul_depth == 1:
                self._finish_question()
                if text and self.li["id"]:
                    self.question = {"id": self.li["id"], "text": text, "answer": []}
                elif text:
                    self.lines.append(f"- {text}")
            elif self.question and text:
                indent = "  " * (self.ul_depth - 2)
                self.question["answer"].append(f"{indent}- {text}")
            self.li = None

    def handle_data(self, data):
        if self.struck:
            return
        if self.link_href is not None:
            self.link_text.append(data)
            return
        buffer = self._buffer()
        if buffer is not None:
            buffer.append(data)

    def _finish_question(self):
        if self.question and self.question["answer"]:
            self.entries.append(
                FaqEntry(
                    section_id=self.question["id"],
                    question=self.question["text"],
                    answer="\n".join(self.question["answer"]),
                )
            )
        elif self.question:
            # A bullet with nothing under it is just text in the section
            self.lines.append(f"- {self.question['text']}")
        self.question = None

    def _finish_section(self):
        """Turn the section that just ended into an entry, unless it already
        has bullet questions."""
        heading = self.heading
        if heading and heading["id"] and len(self.entries) == self.section_start:
            lines = [
                line
                for line in self.lines
                if not line.lower().startswith("back to table of contents")
            ]
            answer = "\n".join(lines)
            if answer and "table of contents" not in heading["text"].lower():
                self.entries.append(
                    FaqEntry(
                        section_id=heading["id"],
                        question=heading["text"],
                        answer=answer,
                    )
                )
        self.lines = []
        self.section_start = len(self.entries)
        self.heading = None


def parse_faq_canvas(html: str) -> list[FaqEntry]:
    """Parse FAQ entries from canvas HTML. If a question appears more than
    once, the last copy wins."""
    parser = _CanvasFaqParser()
    parser.feed(html)
    parser.close()
    parser._finish_section()

    deduped = {_key(entry.question): entry for entry in parser.entries}
    return list(deduped.values())


def _key(question: str) -> str:
    return re.sub(r"[\u2018\u2019]", "'", question).lower()


async def _download_canvas(canvas_id: str, token: str) -> str:
    info = await AsyncWebClient(token=token).files_info(file=canvas_id)
    file: dict = info["file"]  # type: ignore (assuming file exists)
    url = file.get("url_private_download") or file["url_private"]
    async with env.session.get(
        url, headers={"Authorization": f"Bearer {token}"}
    ) as res:
        res.raise_for_status()
        return await res.text()


async def refresh_faq_entries() -> None:
    """Re-fetch the FAQ canvas, keeping the previous copy on failure."""
    global faq_entries, _last_refresh_failed
    canvas_id = env.transcript.faq_canvas_id
    if not canvas_id:
        return
    entries: list[FaqEntry] = []
    # The user token first (as nephthys-plus does), then the bot token, since
    # either may be the one with files:read
    for token in (env.slack_user_token, env.slack_bot_token):
        try:
            entries = parse_faq_canvas(await _download_canvas(canvas_id, token))
        except Exception as e:
            logging.warning(
                f"Failed to fetch FAQ canvas canvas_id={canvas_id} error={e}"
            )
            continue
        if entries:
            break
    if not entries:
        if not _last_refresh_failed:
            logging.error(
                f"Couldn't read any questions from FAQ canvas {canvas_id}, keeping the old copy. Check the logs!"
            )
        _last_refresh_failed = True
        return
    _last_refresh_failed = False
    faq_entries = entries
    logging.info(f"Loaded {len(entries)} FAQ entries from canvas {canvas_id}")


def faq_link_label(entry: FaqEntry) -> str:
    """The question, escaped for use inside a Slack <url|label> link."""
    return _escape(entry.question)


def faq_section_url(entry: FaqEntry) -> str:
    """Deep link to a single question in the FAQ canvas."""
    return f"{env.transcript.faq_link}?focus_section_id={entry.section_id}"


def entry_by_question(question: str | None) -> FaqEntry | None:
    """The entry whose question is exactly this text (as the AI returns it)."""
    if not question:
        return None
    key = _key(question.strip())
    return next((e for e in faq_entries if _key(e.question) == key), None)


def find_faq_entry(topic: str) -> FaqEntry | None:
    """Best entry for a helper's `?faq <topic>`: a question containing the
    topic wins, else the closest fuzzy match if it's close enough."""
    topic = topic.strip().lower()
    if not topic or not faq_entries:
        return None
    contains = [e for e in faq_entries if topic in e.question.lower()]
    if contains:
        return min(contains, key=lambda e: len(e.question))
    scored = [
        (
            max(
                fuzz.partial_ratio(topic, e.question.lower()),
                fuzz.token_set_ratio(topic, e.question.lower()),
            ),
            e,
        )
        for e in faq_entries
    ]
    score, entry = max(scored, key=lambda pair: pair[0])
    return entry if score >= 70 else None
