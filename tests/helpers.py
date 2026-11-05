from datetime import datetime
from datetime import timezone
from typing import Any

from nephthys.utils.help_bot.airtable import Order
from nephthys.utils.help_bot.airtable import Submission
from nephthys.utils.help_bot.jam_config import JamConfig


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def row(**kwargs) -> Submission:
    base: dict[str, Any] = dict(
        id="rec1",
        created_at=utc(2026, 10, 25, 12),
        title="Rooted",
        jam="2026-10",
        code_url="https://github.com/a/rooted",
        status="",
        spotchecked=False,
        dm_queued=False,
        dm_sent_at=None,
        notes="",
    )
    base.update(kwargs)
    return Submission(**base)


def order(**kwargs) -> Order:
    base: dict[str, Any] = dict(
        id="recO",
        jam="2026-10",
        prize="blahaj plushie",
        prize_type="prize",
        status="not fulfilled",
        info="",
        fulfilled_by_name="Gabin Tavernier",
        fulfilled_by_email="gabin@hackclub.com",
        fulfilled_dm_sent_at=None,
    )
    base.update(kwargs)
    return Order(**base)


def config(**jam) -> JamConfig:
    data = {
        "jam": {
            "name": "The MFG Gambler's Jam",
            "displayName": "the mfg gambler's jam",
            "month": "2026-12",
            "startDate": "2026-12-05T23:00:00Z",
            "endDate": "2026-12-13T17:00:00Z",
            "itchUrl": "https://itch.io/jam/the-mfg-gamblers-jam",
            "submitUrl": "",
            "submitWindowDays": 4,
            "hosted": True,
        },
        "betweenJams": False,
        "shop": {
            "cycles": {
                "2026-08": "the brackeys 2026.2 game jam",
                "2026-09": "the cozy fall jam",
                "2026-10": "the jame gam game jam",
            },
        },
    }
    data["jam"].update(jam)
    return JamConfig.model_validate(data)
