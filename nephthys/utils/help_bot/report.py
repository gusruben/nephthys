# Error reporting for the help bot. Everything is logged; the heartbeat
# channel gets at most one alert per kind of error per hour, so an outage at
# the AI provider or Airtable doesn't flood it. Reporting itself never raises.
import logging
import time

from nephthys.utils.logging import send_heartbeat

ALERT_EVERY_SECONDS = 60 * 60
_last_alert: dict[str, float] = {}


async def report(kind: str, message: str, exc: BaseException | None = None) -> None:
    """Log an error and, at most once an hour per `kind`, send a heartbeat."""
    logging.error(f"Help bot {kind}: {message}", exc_info=exc)
    now = time.monotonic()
    last = _last_alert.get(kind)
    if last is not None and now - last < ALERT_EVERY_SECONDS:
        return
    _last_alert[kind] = now
    try:
        await send_heartbeat(
            f"Help bot error ({kind}): {message}",
            messages=["More of these in the next hour only go to the logs."],
        )
    except Exception as e:
        logging.error(f"Help bot couldn't send an error heartbeat: {e}")
