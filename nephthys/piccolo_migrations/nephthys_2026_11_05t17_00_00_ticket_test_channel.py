from nephthys.database.raw_migration import raw_migration

ID = "2026-11-05T17:00:00:000000"
VERSION = "1.33.0"
DESCRIPTION = "Add Ticket.testChannel, set on tickets from the test help channel"


async def forwards():
    return raw_migration(
        migration_id=ID,
        app_name="nephthys",
        description=DESCRIPTION,
        forwards="""
ALTER TABLE "Ticket" ADD COLUMN "testChannel" TEXT;
""",
        backwards="""
ALTER TABLE "Ticket" DROP COLUMN IF EXISTS "testChannel";
""",
    )
