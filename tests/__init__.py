# Unit tests. Run with: uv run python -m unittest discover tests
#
# The bot reads its settings at import time, so tests set a fake environment
# here before anything from nephthys is imported. Nothing here talks to Slack,
# Airtable or the AI; the integration test (test_flow.py) also needs a local
# Postgres in TEST_DATABASE_URL and is skipped without one.
import os

FAKE_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_USER_TOKEN": "xoxp-test",
    "SLACK_SIGNING_SECRET": "test",
    "SLACK_HELP_CHANNEL": "CREALHELP",
    "SLACK_TICKET_CHANNEL": "CREALTICKETS",
    "SLACK_BTS_CHANNEL": "CREALTICKETS",
    "SLACK_MAINTAINER_ID": "U07FCRNHS1J",
    "PROGRAM": "jame_gam",
    "JAMEGAM_TEST_CHANNEL": "CTESTHELP",
    "JAMEGAM_TEST_TICKET_CHANNEL": "CTESTTICKETS",
    "JAMEGAM_NEW_FLOW_CHANNELS": "CTESTHELP",
    "JAMEGAM_AIRTABLE_TOKEN": "pat-test",
    "HACK_CLUB_AI_API_KEY": "",
    "SLACK_HEARTBEAT_CHANNEL": "",
    "ENVIRONMENT": "test",
}
for key, value in FAKE_ENV.items():
    os.environ[key] = value
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none"
)
