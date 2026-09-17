from nephthys.transcripts.transcript import Transcript


class JameGam(Transcript):
    """Transcript for jame gam"""

    program_name: str = "Jame Gam"
    program_owner: str = "U07FCRNHS1J"  # @augie

    help_channel: str = "C0BBDUEMF0E"
    ticket_channel: str = "C0C2EHLQ96X"
    team_channel: str = "C0BV87R7YBE"

    faq_link: str = "https://hackclub.enterprise.slack.com/docs/T0266FRGM/F0BBT4JD9P0"

    first_ticket_create: str = f"""
hi (user) :jamegam-yay:
welcome to <#{help_channel}>. someone will be along soon, but check the <{faq_link}|faq> first, it answers a lot.
if your question gets answered, hit the button below to mark it resolved
"""
    ticket_create: str = f"someone will be along soon! in the meantime check the <{faq_link}|faq>. if your question gets answered, hit the button below to mark it resolved"
    resolve_ticket_button: str = "all good now"
    ticket_resolve: str = f":jamegam-yay: this one was marked resolved by <@{{user_id}}>! more questions? make a new post in <#{help_channel}>"
