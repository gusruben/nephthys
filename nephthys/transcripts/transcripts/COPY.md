# jame gam help bot copy

Every message the help bot features send. The strings themselves live in `jame_gam.py` (the `help_bot=HelpBotCopy(...)` block), so edit them there. This file explains when each one is sent and shows what it looks like with real data filled in. The examples were rendered from the code with the real FAQ canvas and test rows.

The rules are checked by `tests/test_help_bot.py` (`CopyRulesTests`): no em or en dashes, no " - " used as a dash, no middle dots, no "prize only", at most one emoji or emoticon per message, and an emoticon only at the end of a paragraph. Words in `{braces}` are filled in by the bot.

None of this runs in #jame-gam-help until that channel's ID is in `JAMEGAM_NEW_FLOW_CHANNELS`. Until then only the test channel gets it.

The existing messages (`first_ticket_create`, `ticket_create`, `resolve_ticket_button`, `ticket_resolve`) are unchanged. The default `ticket_reopen` ("hey hey! it looks like @x has reopened this post! ...") is still what posts when someone reopens a thread.

## FAQ answers

When a new question matches a question in the FAQ canvas, the bot posts the answer itself, quoted, with a link to that question in the canvas.

| string | when | example |
|---|---|---|
| `faq_answer` | in the thread, right after the usual "someone will be along soon" message. Every line of the answer gets the `>` quote | i think the [faq](#) might answer this one!<br><br>> **[what size is the blåhaj?](#)**<br>> full sized! (1 meter)<br><br>did that help? |
| `bot_note` | small grey line under every answer the bot gives on its own (FAQ, jam facts, status, tracking). Augie's own wording. Every one of those answers has a "still need help" button, so it's true everywhere | i'm a bot! if this doesn't answer your question, click "still need help" and a helper will take a look :) |
| `faq_answered_button` | button under the answer. Resolves the ticket the usual way, and the usual `ticket_resolve` message follows | **that answered it** |
| `faq_still_need_help_button` | button under the answer. Leaves the ticket open. The same label is the button on status and tracking answers, where it reopens the ticket (the existing reopen action) | **still need help** |
| `faq_still_need_help_reply` | in the thread after "still need help". The buttons come off the answer, with nothing in their place | no worries! a helper will be along soon to take a look |
| `buttons_not_yours` | only visible to the clicker, when someone other than the asker or a helper clicks either button. Same wording as the bot's existing "Only helpers or the original poster can ..." | only helpers or the person who asked can use these buttons |

The same two buttons sit under live jam fact answers (next section). Pick-window questions ("how long do i have to pick my prize") are plain FAQ matches.

## `?faq <topic>` (helper macro)

Plain `?faq` is unchanged. With a topic (`?faq blahaj`, `?faq teams`, `?faq ai`), the bot posts that question's answer and resolves the ticket, the same way plain `?faq` does.

| string | when | example |
|---|---|---|
| `faq_topic_macro` | in the thread | hey kid! the [faq](#) covers this one:<br><br>**[what size is the blåhaj?](#)**<br>full sized! (1 meter) |
| `faq_topic_not_found` | only visible to the helper, when no question matches the topic | couldn't find a faq question for `zzz`. the questions are:<br>- how do i know if my project is approved?<br>- ... |

## Live jam facts

These come from the site's `/api/jam` (jam.js), never from the static FAQ. The bot answers only the facts that were asked about, one line each. All times are in ET, written the way the site writes them. When the answer includes the jam's dates, the itch link unfurls.

| string | when | example |
|---|---|---|
| `jam_next` | the jam in jam.js hasn't started | the next jam is **the mfg gambler's jam**! it runs from saturday, december 5 at 6pm ET to sunday, december 13 at 12pm ET, and here's the [itch page](#) |
| `jam_current` | it's running | the current jam is **the mfg gambler's jam**! it runs from saturday, december 5 at 6pm ET to sunday, december 13 at 12pm ET, and here's the [itch page](#) |
| `jam_ended` | it ended and jam.js hasn't rolled over yet | **the mfg gambler's jam** wrapped up sunday, december 13 at 12pm ET, and the next jam hasn't been announced yet. keep an eye on #jame-gam-announcements! |
| `theme_upcoming` | strict: only when the post directly asks what the theme is or when it comes out, before the start | the theme will be announced when the jam starts, saturday, december 5 at 6pm ET |
| `theme_out` | same strict rule, during the jam | the theme is already out! it's on the [itch page](#) |
| `form_not_open` | form question while jam.js has no `submitUrl` yet (that's how the bot knows the form isn't posted) | the submission form isn't up yet. it will be posted near the end of the jam, and you'll have 4 days after the jam ends to submit |
| `form_open` | jam.js has a `submitUrl` and it's before the jam's end plus `submitWindowDays` (4) | the submission form is open, [submit here](#). it closes thursday, december 17 at 12pm ET |

The theme rule is strict twice over. The classifier is told that only direct "what is the theme / when is it out" questions count. Then a pattern check drops the theme answer from any post that doesn't plainly ask that. "Does my game have to fit the theme?", "is my idea ok for the theme", "what does the theme mean": all of these are normal tickets.

After the form window closes, a form question gets no answer and stays a normal ticket. So does "can i still submit, i missed it" at any time.

## Status (only for the jam under review)

This isn't a status checker. It catches people asking or complaining about their game in the jam under review, which is the newest jam anyone has submitted to ("still no review", "where's my dm"). The bot says one of three things, resolves the ticket (no helper credit), adds a "still need help" button (which reopens it), and deletes the whole thread 24 hours later unless they click it. The game's title links to its itch page (the submission's `playable_url`) in these replies and in the ticket post, or shows in plain bold when there's no link.

Anything else is a normal ticket for a human, and the ticket post still shows what the bot found: a post about their own submission, DM or prize from an older jam (the classifier is told which jam is under review and flags these), no submission in the jam under review, Pending, Needs Augie, Rejected, No Prize, Stickers Only, anything unexpected, two rows for the same game that disagree, no row, or Airtable unreadable. Rejections are never mentioned in the thread. Prize Only reads as approved everywhere.

| string | when | example |
|---|---|---|
| `game_link` / `game_no_link` | how a title shows (`{game}` below): linked to `playable_url`, or bold when there's none | [arya in a world of unhappy people](#) / **arya in a world of unhappy people** |
| `status_intro_one` | one game in that jam | here's what i can see for [arya in a world of unhappy people](#): submitted, but not reviewed yet. reviews can take 3 to 4 weeks, and you'll get a dm from @jame gam as soon as it's done! |
| `status_intro_many` + `status_line_many` | several games in that jam | here's what i can see for your games:<br>- [Rooted](#): approved, nice work! your prize dm from @jame gam is coming soon<br>- [Second Game](#): submitted, but not reviewed yet. ... |
| `status_unreviewed` | not reviewed yet (no queue count, since reviews don't go in order) | submitted, but not reviewed yet. reviews can take 3 to 4 weeks, and you'll get a dm from @jame gam as soon as it's done! |
| `status_approved` | Approved or Prize Only, prize DM not sent yet (not spotchecked, spotchecked, or queued) | approved, nice work! your prize dm from @jame gam is coming soon |
| `status_dm_sent` | the prize DM went out | approved, and your prize dm already went out! check your dms from @jame gam |
| `status_footer` | small grey line under every answer the bot resolves itself (status and tracking), under `bot_note`. The button under it is "still need help" (`faq_still_need_help_button`), which reopens the ticket | this thread deletes itself in 24 hours unless you click "still need help" |

## Tracking

These are for "where's my prize" and "can i get tracking". If they name the prize ("my blahaj"), the bot answers about that order and leaves it for a human when there's no matching order. A prize from an older jam ("where's my gmtk blahaj") always goes to a human. Otherwise it covers their orders from the newest jam they ordered in. Canceled orders are skipped, and sticker questions are always for a human (stickers aren't prize orders). Someone with no order gets the status answer instead. `bot_note` and a "still need help" button go under each public reply.

| string | when | example |
|---|---|---|
| `order_not_fulfilled` / `order_processing` / `order_fulfilled` / `order_held_idv` | the `{order}` part of `tracking_not_shipped` | it hasn't been sent yet / it's being processed right now! / it was sent october 20 / it's on hold until your identity verification goes through |
| `tracking_not_shipped` | not fulfilled, processing, or held for IDV. Resolved, 24h delete | i checked on your blahaj plushie: it hasn't been sent yet |
| `tracking_info_public` | fulfilled, and the fulfillment note has a link, a tracking number, or is a digital prize (code, gift card, "check your dms"). Resolved, 24h delete | your blahaj plushie was sent october 20! i've put the shipping details just below, only you can see them |
| `tracking_info_private` | right after, only visible to the asker (the note can contain their email) | here's what the person who sent your blahaj plushie wrote:<br>> https://tools.usps.com/go/TrackConfirmAction?tLabels=9334... |
| `tracking_pinged_public` | fulfilled with no tracking. The ticket stays open | your blahaj plushie was sent october 20 but there's no tracking on it yet, so i've pinged the person who sent it. they'll reply here soon! |
| `tracking_ping_team` | in #jame-gam-tickets, as a reply on that ticket's post. The fulfiller comes from the order's `fulfilled_by`, matched to Slack by email | @Gabin @kid is asking for tracking on their blahaj plushie, marked sent october 20 with no tracking. [order](#) \| [thread](#) |

## Ticket posts in #jame-gam-tickets (helpers only)

These are a few extra lines at the top of each ticket's post. For a ticket the bot resolved itself, the post stays up, so helpers can see what it said, until the thread is deleted. If the asker reopens, it's replaced by the usual reopened post with these lines on it.

| string | example |
|---|---|
| `ticket_post_submission` | **submission:** the jame gam game jam (2026-10): [Rooted](#) approved, waiting on the spotcheck; order: blahaj plushie, not fulfilled |
| `ticket_post_no_submission` | **submission:** none found for this slack id |
| `ticket_post_bot` + `ticket_post_actions` | **bot:** answered from the faq: what size is the blåhaj?<br>**bot:** answered with live jam info<br>**bot:** answered their status and resolved it. the thread deletes friday, november 6 at 1pm ET unless they reopen<br>**bot:** sent them their shipping details and resolved it. ...<br>**bot:** pinged @Gabin for tracking<br>**bot:** left this for a human: {reason} |
| `ticket_post_reasons` | no submission found for their slack id / their only submissions are withdrawn / nothing from them in the jam under review (2026-10), so no status reply / it's about an older jam, so no bot reply / no order matching what they asked about (blahaj) / their submission needs a human look (see above) / they have rows for the same game that disagree / no order found for them / their order needs a human look / couldn't read airtable / their order has no tracking and i couldn't find who sent it |
| `ticket_post_states` | not reviewed yet / approved, waiting on the spotcheck / approved and spotchecked, prize dm not sent / approved, prize dm queued / approved, prize dm sent november 3 / pending, note: {last line of notes} / needs augie / rejected / no prize (they see it as rejected) / stickers only / withdrawn |
| `ticket_post_still_need_help` | **the asker said the bot's answer didn't help** |
| `ticket_post_reopened` | **reopened by the asker** after the bot's answer |

## Things to check in the copy

- The FAQ canvas's "when does the shop close?" says 7 days. That's right from the gambler's jam (2026-12) on, but cycles before it get 4. Pick-window questions now get that FAQ entry, so the answer says 7 for October and November people too, until the canvas is updated.
- "Stickers Only" has no copy, so it always goes to a human.
