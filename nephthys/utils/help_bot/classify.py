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
    r"|(got|was|been|i'?m|am) rejected|too late|late (submission|submit)|submit(ted)? late|missed (the|my)? ?(deadline|form|submission)"
    r"|extension|exception|customs|duties|import tax|change (my|the) (order|prize|address|size|shipping)"
    r"|wrong (address|size|prize|item)|cancel (my|the)? ?order|refund)",
    re.IGNORECASE,
)


@dataclass
class Classification:
    route: str
    confidence: float = 0.0
    faq: FaqEntry | None = None
    facts: list[str] = field(default_factory=list)
    raw: str = ""


SYSTEM_PROMPT = """You triage new posts in the help channel for jame gam, Hack Club's monthly game jam for teenagers. A bot answers some posts by itself; you decide which kind each post is.

Pick exactly one route:
- "status": the asker wants to know where THEIR OWN submission is: has it been reviewed, was it approved, when will it be reviewed, where is their approval or prize DM, can they pick their prize yet, their teammate got a DM but they didn't. Personal questions about their own project's review.
- "tracking": the asker wants to know where THEIR OWN prize or order is: tracking number, has it shipped, when will it arrive, they haven't received it.
- "jam_facts": a question about the current or next jam's facts. Also return which facts in "facts":
  - jam_dates: which jam is next or running, when it starts or ends, how often jams happen.
  - theme: ONLY when the post directly asks what the theme is, or when or where the theme comes out. If the post mentions the theme but asks something else (does my game have to fit it, is my idea ok for it, can i ignore it, what does it mean), do not use theme; that post is "human" (or "faq" if an FAQ answers it).
  - form: whether the jame gam submission form is open yet, or when it closes.
  How many days they get to pick a prize, or when the shop closes, is "faq", not jam_facts.
- "faq": a general question that one of the FAQ questions below answers. Return that FAQ question's exact text in "faq". Lean slightly toward this route: if an FAQ answer would genuinely help, pick it even if a human might add a little more.
- "human": needs a person's judgment or account access: appeals or "why was I rejected", late or missed submissions and asking for exceptions or extensions, customs or duties or shipping-to-my-country exceptions, changing or cancelling an order (address, prize, size), errors or bugs on the form, the site or itch, a tracking link that doesn't work, problems with an account, identity verification, reviewer or helper applications and reviewer pay, or anything the FAQ and the routes above don't cover.
- "none": not a question for helpers: thanks, chatter, introductions, looking for teammates, announcements.

Never pick faq, jam_facts, status or tracking for an appeal, a late or missed submission, a customs exception or an order change: those are always "human".

Reply with only a JSON object, no prose:
{"route": "...", "faq": "<exact FAQ question or null>", "facts": [...], "confidence": <0 to 1>}"""


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
    faq = None
    if route == "faq":
        faq = _match_entry(data.get("faq"), entries)
        if not faq:
            route = "human"
    if route == "jam_facts" and not facts:
        route = "human"
    return Classification(
        route=route, confidence=confidence, faq=faq, facts=facts, raw=content
    )


def _match_entry(question: str | None, entries: list[FaqEntry]) -> FaqEntry | None:
    if not question:
        return None
    key = question.strip().lower()
    for entry in entries:
        if entry.question.lower() == key:
            return entry
    return entry_by_question(question)


# A post that directly asks what the theme is or when it comes out
DIRECT_THEME = re.compile(
    r"\bwhat(?:'s| is| will be| was)?\s+(?:the\s+|this\s+)?(?:(?:jam|month'?s?|next)\s+)?(?:theme|topic)\b(?!\s+(?:means|mean))"
    r"|\b(?:when|where)\b[^.?!]{0,40}\b(?:theme|topic)\b[^.?!]{0,30}\b(?:announced|revealed|released|out|drop|drops|come|comes|posted|decided|get|given|shown|be)\b"
    r"|\b(?:when|where)(?:'s| is| do we get| will we get)\s+(?:the\s+)?(?:theme|topic)\b"
    r"|\b(?:theme|topic)\b\s+(?:is\s+)?(?:out|announced|revealed|released)(?:\s+yet)?\b"
    r"|\bis\s+(?:the\s+)?(?:theme|topic)\s+(?:out|announced|revealed|released)\b"
    r"|\b(?:any|is there a|is there an?y?|got a)\s+(?:specific\s+)?(?:theme|topic)\s+(?:yet|for)\b"
    r"|^\W*(?:theme|topic)\W*$",
    re.IGNORECASE,
)


def apply_guards(text: str, result: Classification) -> Classification:
    """The backstops that don't depend on the model: low confidence and
    judgment calls both become a plain ticket, and the theme answer only goes
    to posts that plainly ask what the theme is or when it's out."""
    if result.route in ("human", "none"):
        return result
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
    if result.route in ("faq", "jam_facts") and JUDGMENT_CALL.search(text):
        return Classification(
            route="human", confidence=result.confidence, raw=result.raw
        )
    return result


async def classify(text: str, entries: list[FaqEntry]) -> Classification:
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
                    "content": f"FAQ questions:\n{_prompt_faq(entries)}\n\nThe post:\n<post>\n{text}\n</post>",
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
        f"Help bot classification route={result.route} confidence={result.confidence} faq={result.faq.question if result.faq else None} facts={result.facts}"
    )
    return result
