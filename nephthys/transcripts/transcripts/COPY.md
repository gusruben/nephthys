# jame gam help bot copy

Every message the help bot features send. The strings themselves live in `jame_gam.py` (the `help_bot=HelpBotCopy(...)` block), so edit them there. This file explains when each one is sent and shows what it looks like with real data filled in. The examples were rendered from the code with the real FAQ canvas and test rows.

The rules are checked by `tests/test_help_bot.py` (`CopyRulesTests`): no em or en dashes, no " - " used as a dash, no middle dots, no "prize only", at most one emoji or emoticon per message, and an emoticon only at the end of a paragraph. Words in `{braces}` are filled in by the bot.

None of this runs in #jame-gam-help until that channel's ID is in `JAMEGAM_NEW_FLOW_CHANNELS`. Until then only the test channel gets it.

The existing messages (`first_ticket_create`, `ticket_create`, `resolve_ticket_button`, `ticket_resolve`) are unchanged. The default `ticket_reopen` ("hey hey! it looks like @x has reopened this post! ...") is still what posts when someone reopens a thread.

## FAQ answers

When a new question matches a question in the FAQ canvas, the bot posts the answer itself, with a link to that question in the canvas.

| string | when | example |
|---|---|---|
| `faq_answer` | in the thread, right after the usual "someone will be along soon" message | i think the faq answers this one!<br><br>**[what size is the blåhaj?](#)**<br>full sized! (1 meter)<br><br>did that answer it? |
| `faq_answered_button` | button under the answer. Resolves the ticket the usual way, and the usual `ticket_resolve` message follows | **that answered it** |
| `faq_still_need_help_button` | button under the answer. Leaves the ticket open | **still need help** |
| `faq_still_need_help_reply` | in the thread after "still need help" | no worries, a helper will be along soon to take a look |
| `faq_still_need_help_context` | small grey line that replaces the buttons after "still need help" | @kid still needs help, a helper is on the way |
| `buttons_not_yours` | only visible to the clicker, when someone other than the asker or a helper clicks either button | only the person who asked (or a helper) can use these buttons |

The same two buttons sit under live jam fact answers (next section).

## `?faq <topic>` (helper macro)

Plain `?faq` is unchanged. With a topic (`?faq blahaj`, `?faq teams`, `?faq ai`), the bot posts that question's answer and resolves the ticket, the same way plain `?faq` does.

| string | when | example |
|---|---|---|
| `faq_topic_macro` | in the thread | hey kid! the faq covers this one:<br><br>**[what size is the blåhaj?](#)**<br>full sized! (1 meter) |
| `faq_topic_not_found` | only visible to the helper, when no question matches the topic | couldn't find a faq question for `zzz`. the questions are:<br>- how do i know if my project is approved?<br>- ... |

## Live jam facts

These come from the site's `/api/jam` (jam.js and shop.js), never from the static FAQ. The bot answers only the facts that were asked about, one line each. All times are in ET, written the way the site writes them.

| string | when | example |
|---|---|---|
| `jam_next` | the jam in jam.js hasn't started | the next jam is **the mfg gambler's jam**, running from saturday, december 5 at 6pm ET to sunday, december 13 at 12pm ET. here's the [itch page](#) |
| `jam_current` | it's running | the current jam is **the mfg gambler's jam**, running from saturday, december 5 at 6pm ET to sunday, december 13 at 12pm ET. here's the [itch page](#) |
| `jam_ended` | it ended and jam.js hasn't rolled over yet | **the mfg gambler's jam** wrapped up sunday, december 13 at 12pm ET, and the next jam hasn't been announced yet. keep an eye on #jame-gam-announcements for it! |
| `theme_upcoming` | theme question, before the start | the theme drops when the jam starts, saturday, december 5 at 6pm ET |
| `theme_out` | theme question, during the jam | the theme is already out! it's on the [itch page](#) |
| `form_not_open` | form question, `submitUrl` is still empty | the jame gam submission form isn't up yet. it goes up near the end of the jam, and you'll have 4 days after the jam ends to submit |
| `form_open` | `submitUrl` is set and the window is open | the jame gam submission form is open, [submit here](#). it closes thursday, december 17 at 12pm ET |
| `form_closed` | `submitUrl` is set and the window has passed | the jame gam submission form for **the mfg gambler's jam** closed thursday, december 17 at 12pm ET |
| `pick_window_yours` | pick window question from someone with a submission (uses their jam's cycle) | you get 4 days to pick your prize once your prize dm arrives |
| `pick_window_rule` | pick window question from someone without one | you get 7 days to pick your prize once your prize dm arrives (4 days for jams before the mfg gambler's jam) |

"Can I still submit, I missed it" is a late submission, so it never gets a form answer. It stays a normal ticket.

## Status of their own submission

These are for "when will mine be reviewed", "did i get approved" and "where's my dm". The bot reads the asker's rows (by Slack ID) from their newest jam. If every row is in a clear state it answers, resolves the ticket (no helper credit), adds a reopen button, and deletes the whole thread 24 hours later unless they reopen it.

It never answers, and leaves a normal ticket for a human, when a row is Pending, Needs Augie, Rejected, No Prize, Stickers Only or anything unexpected, when two rows for the same game disagree, when no row is found, when their pick window closed without an order, or when Airtable can't be read. Rejections are never mentioned in the thread. Prize Only reads as approved everywhere.

| string | when | example |
|---|---|---|
| `status_intro_one` | one game | here's where **Rooted** is at: {line} |
| `status_intro_many` + `status_line_many` | several games in the same jam | here's where your games are at:<br>- **Rooted**: approved! your prize dm goes out after a final check<br>- **Second Game**: submitted, but not reviewed yet. ... |
| `status_unreviewed` + `status_unreviewed_ahead` | not reviewed yet. The count is games submitted earlier that are still unreviewed, left out if Airtable can't count them | submitted, but not reviewed yet. 57 games submitted before yours are still waiting too. reviews can take 3 to 4 weeks, and you'll get a dm from @jame gam as soon as it's done |
| `status_approved_checking` | Approved or Prize Only, not spotchecked yet | approved! your prize dm goes out after a final check |
| `status_approved_ready` | spotchecked, DM not ticked yet | approved! your prize dm goes out soon |
| `status_approved_queued` | `action_send_approved_dm` ticked, not sent yet | approved! your prize dm is on its way |
| `status_approved_dm_open` | DM sent, pick window still open, no order | approved! your prize dm went out november 3. pick your prize at [jamegam.hackclub.com/prizes](#) by saturday, november 7 at 11:59pm ET |
| `status_approved_dm_open_no_deadline` | same, if `/api/jam` can't be read | approved! your prize dm went out november 3. pick your prize at [jamegam.hackclub.com/prizes](#) before the deadline in that dm |
| `status_approved_ordered` + an `order_*` line | they've ordered | approved, and you picked blahaj plushie. it was sent october 20 |
| `order_not_fulfilled` | | it hasn't been sent yet |
| `order_processing` | | it's being processed right now |
| `order_fulfilled` | | it was sent october 20 |
| `order_held_idv` | | it's on hold until your identity verification goes through |
| `status_footer` | small grey line under every answer the bot resolves itself | this thread deletes itself in 24 hours unless you reopen it. if anything here looks off, hit reopen and a helper will take a look |
| `reopen_button` | button under it (the existing reopen action) | **reopen** |

## Tracking

These are for "where's my prize" and "can i get tracking". The bot covers every order from their newest three jams; canceled orders are skipped. Someone with no order gets the status answer instead.

| string | when | example |
|---|---|---|
| `tracking_not_shipped` | not fulfilled, processing, or held for IDV. Resolved, 24h delete | about your blahaj plushie: it hasn't been sent yet |
| `tracking_info_public` | fulfilled, and the fulfillment note has a link, a tracking number, or is a digital prize (code, gift card, "check your dms"). Resolved, 24h delete | your blahaj plushie was sent october 20! i've put the shipping details just below, only you can see them |
| `tracking_info_private` | right after, only visible to the asker (the note can contain their email) | here's what the person who sent your blahaj plushie wrote:<br>> https://tools.usps.com/go/TrackConfirmAction?tLabels=9334... |
| `tracking_pinged_public` | fulfilled with no tracking. The ticket stays open | your blahaj plushie was sent october 20 but there's no tracking on it yet, so i've pinged the person who sent it. they'll reply here |
| `tracking_ping_team` | in #jame-gam-tickets, as a reply on that ticket's post. The fulfiller comes from the order's `fulfilled_by`, matched to Slack by email | @Gabin @kid is asking for tracking on their blahaj plushie, marked sent october 20 with no tracking. [order](#) \| [thread](#) |

## Ticket posts in #jame-gam-tickets (helpers only)

These are a few extra lines at the top of each ticket's post. For a ticket the bot resolved itself, the post stays up, so helpers can see what it said, until the thread is deleted. If the asker reopens, it's replaced by the usual reopened post with these lines on it.

| string | example |
|---|---|
| `ticket_post_submission` | **submission:** the jame gam game jam (2026-10): **Rooted** approved, waiting on the spotcheck; order: blahaj plushie, not fulfilled |
| `ticket_post_no_submission` | **submission:** none found for this slack id |
| `ticket_post_bot` + `ticket_post_actions` | **bot:** answered from the faq: what size is the blåhaj?<br>**bot:** answered with live jam info<br>**bot:** answered their status and resolved it. the thread deletes friday, november 6 at 1pm ET unless they reopen<br>**bot:** sent them their shipping details and resolved it. ...<br>**bot:** pinged @Gabin for tracking<br>**bot:** left this for a human: {reason} |
| `ticket_post_reasons` | no submission found for their slack id / their only submissions are withdrawn / their submission needs a human look (see above) / they have rows for the same game that disagree / their pick window closed and they never ordered / no order found for them / their order needs a human look / couldn't read airtable / their order has no tracking and i couldn't find who sent it |
| `ticket_post_states` | not reviewed yet / approved, waiting on the spotcheck / approved and spotchecked, prize dm not sent / approved, prize dm queued / approved, prize dm sent november 3 / pending, note: {last line of notes} / needs augie / rejected / no prize (they see it as rejected) / stickers only / withdrawn |
| `ticket_post_still_need_help` | **the asker said the bot's answer didn't help** |
| `ticket_post_reopened` | **reopened by the asker** after the bot's answer |

## Things to check in the copy

- The FAQ canvas's "when does the shop close?" says 7 days. That's right from the gambler's jam (2026-12) on, but cycles before it get 4 days. The live facts answer gets this right, but if the bot quotes that FAQ entry, it says 7.
- "Stickers Only" has no copy, so it always goes to a human. Say if it should get a line like the approved ones.
