# The live jam config, read from the jame gam site's /api/jam endpoint.
#
# The site's src/lib/jam.js (and shop.js for the jam names) is the source of
# truth for the current jam, its dates and the submission form. The bot never
# copies those: it reads them here, and if the endpoint can't be reached it says
# nothing it would have needed them for.
#
# The form counts as open when jam.js has a `submitUrl` and it's before the jam
# ends plus `submitWindowDays`. There's deliberately no separate close time.
import time
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

from aiohttp import ClientTimeout
from pydantic import BaseModel

from nephthys.utils.env import env

ET = ZoneInfo("America/New_York")
CACHE_SECONDS = 10 * 60


class JamInfo(BaseModel):
    name: str
    displayName: str
    month: str
    startDate: datetime
    endDate: datetime
    itchUrl: str = ""
    submitUrl: str = ""
    submitWindowDays: int | None = None
    hosted: bool = False

    def submit_closes_at(self) -> datetime | None:
        if not self.submitWindowDays:
            return None
        return self.endDate + timedelta(days=self.submitWindowDays)


class ShopInfo(BaseModel):
    cycles: dict[str, str] = {}


class JamConfig(BaseModel):
    jam: JamInfo
    betweenJams: bool = False
    shop: ShopInfo = ShopInfo()

    def jam_name(self, jam: str) -> str:
        if jam == self.jam.month:
            return self.jam.displayName
        return self.shop.cycles.get(jam, jam)


_cache: tuple[float, JamConfig] | None = None


async def get_jam_config() -> JamConfig | None:
    """The live config, cached for a few minutes. None if it can't be read."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    url = env.jamegam_config_url
    if not url:
        return None
    try:
        async with env.session.get(url, timeout=ClientTimeout(total=10)) as res:
            res.raise_for_status()
            config = JamConfig.model_validate(await res.json())
    except Exception as e:
        from nephthys.utils.help_bot.report import report

        await report("jam config", f"couldn't read {url}, so no jam facts answers: {e}")
        # A stale copy beats nothing
        return _cache[1] if _cache else None
    _cache = (time.monotonic(), config)
    return config


def et_text(at: datetime) -> str:
    """'saturday, december 5 at 6pm ET', the way the site words times."""
    local = at.astimezone(ET)
    hour = local.strftime("%I").lstrip("0")
    minute = "" if local.minute == 0 else local.strftime(":%M")
    ampm = local.strftime("%p").lower()
    day = f"{local.strftime('%A, %B')} {local.day}".lower()
    return f"{day} at {hour}{minute}{ampm} ET"


def et_date_text(at: datetime) -> str:
    """'october 31', for dates where the time doesn't matter."""
    local = at.astimezone(ET)
    return f"{local.strftime('%B').lower()} {local.day}"


JAM_FACTS = ("jam_dates", "theme", "form")


def jam_facts_text(
    config: JamConfig, facts: list[str], now: datetime | None = None
) -> str | None:
    """Answer the requested facts from the live config, one line each.
    Returns None if none of them can be answered."""
    copy = env.transcript.help_bot
    if not copy:
        return None
    now = now or datetime.now(ET)
    jam = config.jam
    started = now >= jam.startDate
    ended = now > jam.endDate
    lines: list[str] = []
    for fact in facts:
        if fact == "jam_dates":
            if ended:
                template = copy.jam_ended
            elif started:
                template = copy.jam_current
            else:
                template = copy.jam_next
            lines.append(
                template.format(
                    name=jam.displayName,
                    start=et_text(jam.startDate),
                    end=et_text(jam.endDate),
                    itch_url=jam.itchUrl,
                )
            )
        elif fact == "theme":
            if ended:
                continue
            if started:
                lines.append(copy.theme_out.format(itch_url=jam.itchUrl))
            else:
                lines.append(copy.theme_upcoming.format(start=et_text(jam.startDate)))
        elif fact == "form":
            closes = jam.submit_closes_at()
            if jam.submitUrl and closes and now < closes:
                lines.append(
                    copy.form_open.format(url=jam.submitUrl, closes=et_text(closes))
                )
            elif not jam.submitUrl and jam.submitWindowDays and not ended:
                lines.append(copy.form_not_open.format(days=jam.submitWindowDays))
            # After the window a form question is a normal ticket
    return "\n".join(lines) if lines else None
