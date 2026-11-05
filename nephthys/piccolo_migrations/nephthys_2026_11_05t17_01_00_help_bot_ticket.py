from nephthys.database.raw_migration import raw_migration

ID = "2026-11-05T17:01:00:000000"
VERSION = "1.33.0"
DESCRIPTION = "Add the help_bot_ticket table"


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards="""
CREATE TABLE "help_bot_ticket" (
    "id" SERIAL PRIMARY KEY,
    "ticketId" INTEGER NOT NULL UNIQUE REFERENCES "Ticket" ("id") ON DELETE CASCADE,
    "route" TEXT NOT NULL,
    "faqQuestion" TEXT,
    "submissionSummary" TEXT,
    "actionSummary" TEXT,
    "answerTs" TEXT,
    "autoResolved" BOOLEAN NOT NULL DEFAULT FALSE,
    "stillNeedHelp" BOOLEAN NOT NULL DEFAULT FALSE,
    "reopened" BOOLEAN NOT NULL DEFAULT FALSE,
    "deleteAfter" TIMESTAMPTZ,
    "deletedAt" TIMESTAMPTZ,
    "createdAt" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX "help_bot_ticket_deleteAfter" ON "help_bot_ticket" ("deleteAfter")
    WHERE "deleteAfter" IS NOT NULL;
""",
        backwards="""
DROP TABLE IF EXISTS "help_bot_ticket";
""",
    )
