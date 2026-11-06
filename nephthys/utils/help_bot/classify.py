# Decides what kind of question a new ticket is, with one AI call.
#
# The model only picks from fixed routes and the FAQ canvas's own questions, so
# it can't make up an answer: everything the bot posts comes from the canvas,
# the live jam config or the asker's Airtable rows.
import json
import logging
import re
from dataclasses import dataclass
from dataclasses import field

from openai import OpenAIError

from nephthys.utils.env import env
from nephthys.utils.faq import entry_by_question
from nephthys.utils.faq import FaqEntry
from nephthys.utils.help_bot.jam_config import JAM_FACTS

ROUTES = ("status", "tracking", "jam_facts", "faq", "human", "none")

# Never auto-answered, whatever the model says: these need a person's call.
# Matched on top of the model's own "human" route, as a backstop.
JUDGMENT_CALL = re.compile(
    r"\b(appeal|re-?review|second chance|why (was|did|got) (i|my|it|mine)\b.*\breject"
    r"|(got|was|been|i'?m|am) rejected|too late to submit|late (submission|submit)|submit(ted)? (too )?late|missed (the|my)? ?(deadline|form|submission)"
    r"|extension|exception|customs|duties|import tax|change (my|the) (order|prize|address|size|shipping)"
    r"|wrong (address|size|prize|item)|cancel (my|the)? ?order|refund"
    # A missed prize pick window is an exception request too
    r"|missed (the |my )?(pick|picking|shop|window|chance to pick)|never (got to )?pick(ed)?|shop (already )?closed (for me|before)"
    r"|pick(ing)? window (closed|ended|passed|is over)"
    # Prize currency / amount questions need a person
    r"|currenc(y|ies)"
    # Accusations of unfairness go to a person, even when an FAQ fits
    r"|rigged|unfair|not fair|favou?ritism|playing favou?rites|biased)",
    re.IGNORECASE,
)

# Something went wrong with an order that was sent: a person has to look,
# even though the order has tracking the bot could share
ORDER_PROBLEM = re.compile(
    r"\b(delivered|fulfilled|shipped)\b[^.?!]{0,60}?\b(never|didn'?t|not|nothing|hasn'?t)\b[^.?!]{0,30}?\b(got|get|arrived|arrive|showed|show|here|came|come|received)\b"
    r"|\bdelivered but\b|\bstolen\b|\blost in the mail\b|\bgot lost\b|\bdamaged\b|\barrived broken\b|\bwrong (item|prize|size|color|colour|one)\b",
    re.IGNORECASE,
)


@dataclass
class Classification:
    route: str
    confidence: float = 0.0
    faq: FaqEntry | None = None
    facts: list[str] = field(default_factory=list)
    # The post is about the asker's own submission, DM or prize from a jam
    # before the one under review
    older_jam: bool = False
    # The prize they asked about, if they named one ("blahaj")
    item: str | None = None
    # Why a guard turned it into a human ticket (a ticket_post_reasons key)
    reason: str | None = None
    raw: str = ""


SYSTEM_PROMPT = """You triage new posts in the help channel for jame gam, Hack Club's monthly game jam for teenagers. A bot answers some posts by itself; you decide which kind each post is.

Pick exactly one route:
- "status": the asker asks or complains about where THEIR OWN submission stands: has mine been reviewed, was mine approved, where is my approval or prize DM, i still haven't heard anything about my game. Use it when they want their own state looked up.
  If they ask whether THEIR OWN submission is done, approved or reviewed ("is mine done too", "was mine approved", "has mine been looked at"), it is "status", even when the post also mentions a friend or teammate.
  A question about how the process works or whether something is normal, with no ask about their own submission ("do you tell us either way", "how long does review take", "is it normal that my teammate got a dm and i didn't") is "faq" when an FAQ answers it.
- "tracking": the asker wants to know where THEIR OWN prize is: tracking number, has it shipped or been sent, when will it arrive, they haven't received it. This includes digital prizes (game keys, codes, gift cards). If they name the prize, return it in "item". Stickers are not a prize order: sticker questions are "human".
- "jam_facts": a question about the current or next jam's facts. Also return which facts in "facts":
  - jam_dates: which jam is next or running, when it starts or ends, how often jams happen, whether the jam's itch page is up, and whether they can still join or start (joining late is not a late submission). Only the current or next jam: a question about a past jam ("the october jam", "last month's jam") is "human".
  - theme: ONLY when the post directly asks what the theme is, or when or where the theme comes out. If the post mentions the theme but asks something else (does my game have to fit it, is my idea ok for it, can i ignore it, what does it mean), do not use theme; that post is "human" (or "faq" if an FAQ answers it).
  - form: whether the jame gam submission form is open yet, or when it closes.
  How many days they get to pick a prize, or when the shop closes, is "faq", not jam_facts.
- "faq": a general question that one of the FAQ questions below answers. Return that FAQ question's exact text in "faq". Using work from before the jam (an old project, a prototype, something started early) is answered by the FAQ question about submitting to other events. Lean slightly toward this route: if an FAQ answer would genuinely help, pick it even if a human might add a little more.
- "human": needs a person's judgment or account access: appeals or "why was I rejected", late or missed submissions and asking for exceptions or extensions, customs or duties or shipping-to-my-country exceptions, changing or cancelling an order (address, prize, size), errors or bugs on the form, the site or itch, a tracking link that doesn't work, a prize marked delivered or sent that never arrived (or arrived lost, damaged or wrong), a missed prize pick window, something about their submission or order that looks wrong or mixed up, problems with an account, identity verification, reviewer or helper applications and reviewer pay, or anything the FAQ and the routes above don't cover.
- "none": not a question for helpers: thanks, chatter, introductions, looking for teammates, announcements.

Never pick faq, jam_facts, status or tracking for an appeal, a late or missed submission, a customs exception or an order change: those are always "human".

Also set "older_jam" to true when the post is about the asker's own submission, review, DM or prize from a jam EARLIER than the one under review (named below), for example by naming an earlier jam or saying "last jam". Otherwise false.

Reply with only a JSON object, no prose:
{"route": "...", "faq": "<exact FAQ question or null>", "facts": [...], "item": "<prize they named or null>", "older_jam": <true or false>, "confidence": <0 to 1>}"""


def _prompt_faq(entries: list[FaqEntry]) -> str:
    return "\n".join(f"- {e.question}\n  answer: {e.answer[:400]}" for e in entries)


def parse_reply(content: str, entries: list[FaqEntry]) -> Classification:
    """Read the model's JSON. Anything malformed becomes a plain human ticket."""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return Classification(route="human", raw=content)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return Classification(route="human", raw=content)
    route = data.get("route")
    if route not in ROUTES:
        return Classification(route="human", raw=content)
    try:
        confidence = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    facts = [f for f in data.get("facts") or [] if f in JAM_FACTS]
    older_jam = data.get("older_jam") is True
    item = data.get("item") if isinstance(data.get("item"), str) else None
    faq = None
    if route == "faq":
        faq = _match_entry(data.get("faq"), entries)
        if not faq:
            route = "human"
    if route == "jam_facts" and not facts:
        route = "human"
    return Classification(
        route=route,
        confidence=confidence,
        faq=faq,
        facts=facts,
        older_jam=older_jam,
        item=(item.strip() or None) if item else None,
        raw=content,
    )


def _match_entry(question: str | None, entries: list[FaqEntry]) -> FaqEntry | None:
    if not question:
        return None
    key = question.strip().lower()
    for entry in entries:
        if entry.question.lower() == key:
            return entry
    return entry_by_question(question)


# About a jam that already happened: the config only knows the current or
# next one, so a jam facts answer would be about the wrong jam
PAST_JAM = re.compile(
    r"\b(when|how long|what)\b[^.?!]{0,30}\b(last|previous|past|first|very first)\s+(month'?s?\s+|year'?s?\s+)?(jam|jame gam|one)\b"
    r"|\b(did|was|were)\b[^.?!]{0,40}\bjam\b[^.?!]{0,25}\b(run|end|start|last|go|happen)",
    re.IGNORECASE,
)


# Asks whether the asker's OWN submission is done, approved or reviewed.
# That's a status lookup even when the post also mentions a friend or
# teammate ("my friend got approved, is mine done too").
OWN_ROW_ASK = re.compile(
    r"\b(is|was|has|did|have) (mine|my (game|submission|project|entry|one))( been| gotten| got| also| too)*"
    r" (done|approved|accepted|reviewed|looked at|checked|through|in)\b"
    r"|\b(is|was|has|did) mine\b[^.?!]{0,20}\b(too|also|yet|as well)\b"
    r"|\bwhat about (mine|my (game|submission|project))\b",
    re.IGNORECASE,
)


# A post that directly asks what the theme is or when it comes out: it
# mentions the theme, asks about it, and isn't asking whether something fits
# it, what it means, or whether it can be ignored.
THEME_WORD = re.compile(r"\b(theme|topic|thema)s?\b", re.IGNORECASE)
THEME_ASK = re.compile(
    r"\b(what|whats|what's|which|when|whens|when's|where|has|have|is|isn'?t|did|any|anyone know"
    r"|find out|know|reveal|revealed|reveals|drop|dropped|drops|announce|announced|out"
    r"|posted|released|tell)\b|^\W*(theme|topic)\W*$",
    re.IGNORECASE,
)
THEME_NOT_ASKING = re.compile(
    r"\b(fit|fits|fitting|match|matches|matching|follow|following|ignor\w*|mean|means|meaning"
    r"|interpret\w*|stretch|relate\w*|idea|ideas|allowed|count|counts|reskin|hate|drift\w*"
    r"|stick to|be about|explain|example|ok|okay|alright|change|vague|need|needs|required|require|have to|has to|must)\b",
    re.IGNORECASE,
)


class _DirectTheme:
    """re-like `search` so tests and callers can treat it as a pattern."""

    def search(self, text: str) -> bool:
        return bool(
            THEME_WORD.search(text)
            and THEME_ASK.search(text)
            and not THEME_NOT_ASKING.search(text)
        )


DIRECT_THEME = _DirectTheme()


def apply_guards(text: str, result: Classification) -> Classification:
    """The backstops that don't depend on the model: low confidence and
    judgment calls both become a plain ticket, and the theme answer only goes
    to posts that plainly ask what the theme is or when it's out."""
    if result.route in ("human", "none"):
        return result
    # Anything about their own older jam is for a human, prizes included
    if result.older_jam:
        return Classification(
            route="human",
            confidence=result.confidence,
            older_jam=True,
            reason="older_jam",
            raw=result.raw,
        )
    if result.route == "jam_facts" and "theme" in result.facts:
        if not DIRECT_THEME.search(text):
            facts = [f for f in result.facts if f != "theme"]
            if not facts:
                return Classification(
                    route="human", confidence=result.confidence, raw=result.raw
                )
            result = Classification(
                route=result.route,
                confidence=result.confidence,
                facts=facts,
                raw=result.raw,
            )
    if result.confidence < env.ai_jamegam_min_confidence:
        return Classification(
            route="human", confidence=result.confidence, raw=result.raw
        )
    if result.route == "jam_facts" and PAST_JAM.search(text):
        return Classification(
            route="human", confidence=result.confidence, raw=result.raw
        )
    if result.route in ("faq", "jam_facts") and JUDGMENT_CALL.search(text):
        return Classification(
            route="human", confidence=result.confidence, raw=result.raw
        )
    if result.route in ("tracking", "status") and (
        ORDER_PROBLEM.search(text) or JUDGMENT_CALL.search(text)
    ):
        return Classification(
            route="human", confidence=result.confidence, raw=result.raw
        )
    # "is mine done too" is a lookup of their own row, not the process FAQ
    if result.route == "faq" and OWN_ROW_ASK.search(text):
        return Classification(
            route="status", confidence=result.confidence, raw=result.raw
        )
    return result


async def classify(
    text: str, entries: list[FaqEntry], context: str = ""
) -> Classification:
    if not env.ai_client or not text.strip():
        return Classification(route="human")
    try:
        response = await env.ai_client.chat.completions.create(
            model=env.ai_jamegam_model,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"{context}\n\nFAQ questions:\n{_prompt_faq(entries)}\n\nThe post:\n<post>\n{text}\n</post>".strip(),
                },
            ],
        )
    except OpenAIError as e:
        logging.error(f"Help bot classification failed error={e}")
        return Classification(route="human")
    content = (
        response.choices[0].message.content
        if response.choices and response.choices[0].message.content
        else ""
    )
    result = apply_guards(text, parse_reply(content, entries))
    logging.info(
        f"Help bot classification route={result.route} confidence={result.confidence} faq={result.faq.question if result.faq else None} facts={result.facts} older_jam={result.older_jam} item={result.item}"
    )
    return result
