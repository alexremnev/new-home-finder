# Putting WhatsApp on a real number

What has to happen so alerts arrive from **London Home Finder** rather than from
a phone number, and from a number that can write to anybody rather than to five
test recipients.

No company registration is needed for the name. Three things get confused with
each other, and only the first is wanted here:

| | What it is | Needed |
| --- | --- | --- |
| **Display name** | "London Home Finder" in place of the number | **yes** |
| **Business verification** | Meta checking company documents | no — it raises limits |
| **Official Business Account** | the green tick | no |

The display name is reviewed against what you have declared about yourself — the
portfolio name, the Page, the website — not against a companies register.

## The shortest path that works

**The name recipients see belongs to the phone number, not to the WABA.**
`verified_name` is a property of a phone number; a WABA's name is an internal
label nobody outside the panel ever sees. So "alerts from London Home Finder"
needs a display name on a number — not a WABA called that.

That collapses the whole job into: **add the real number to the WhatsApp
Business Account that already works, and ask for the display name on it.**

A new WABA costs a new app, a new system user and token, both templates
submitted again, the webhook configured again, and a fresh trip through Meta's
policy and review gates. None of it buys anything a subscriber can see.

**Check nothing is subscribed before changing the number.** "No subscribers" is
easy to believe and easy to be wrong about — test numbers linger:

```sql
SELECT uc.address, uc.verified_at, uc.last_inbound_at,
       u.status, s.active AS has_filter
  FROM user_channels uc
  JOIN users u ON u.id = uc.user_id
  LEFT JOIN subscriptions s ON s.user_id = u.id AND s.active
 WHERE uc.channel = 'whatsapp';
```

Any row is a number that thinks it has an open conversation with the old
number. See step 8.

## 1. Free the phone number

A number registered on WhatsApp cannot be verified for the Cloud API. Which app
it is in decides what to do:

- **WhatsApp Business app** — turn off two-step verification (Settings →
  Account → Two-step verification → **Disable**) and the number can be migrated,
  keeping its quality rating and messaging limit.
- **Regular WhatsApp** — Settings → Account → **Delete my account**. There is no
  migration.

Either way: export any chats worth keeping first. The Cloud API has no inbox, so
history does not follow the number.

Deleting the Business app's account also removes the WABA that account created,
if one was made for it. That is not a loss — see above.

After this the number must never be opened in the WhatsApp app again.

## 2. Add the number to the WABA that already works

WhatsApp Manager → that WABA → **Phone numbers → Add phone number**:

- Display name: `London Home Finder`
- Category: something honest, e.g. Real Estate
- Verify by **voice** first if SMS has been refused before — the routes are
  separate and SMS is the one that gets throttled

Rename the WABA itself to `London Home Finder` if the old name annoys you. It is
a label; it changes nothing.

## 3. Take the right phone number id

The WABA now holds two numbers: Meta's test one and yours. Every setting must
point at yours.

```sh
curl -s "https://graph.facebook.com/v21.0/$WABA_ID/phone_numbers?fields=display_phone_number,verified_name,name_status,code_verification_status,platform_type,quality_rating" -H "Authorization: Bearer $WA_ACCESS_TOKEN"
```

Your row needs `code_verification_status: VERIFIED` and
`platform_type: CLOUD_API`. `verified_name` is literally what recipients see;
`name_status` says whether the review is finished.

## 4. Templates

The working WABA already has the listing template approved — its name is what
`WA_TEMPLATE_NAME` holds. List what is there:

```sh
curl -s "https://graph.facebook.com/v21.0/$WABA_ID/message_templates?fields=name,status,category,language&limit=50" -H "Authorization: Bearer $WA_ACCESS_TOKEN"
```

Only the notice template is new. Create it once:

```sh
curl -s -X POST "https://graph.facebook.com/v21.0/$WABA_ID/message_templates" \
  -H "Authorization: Bearer $WA_ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"plan_notice_paid","language":"en","category":"MARKETING",
   "components":[
     {"type":"BODY",
      "text":"Your alerts have stopped — your plan has run out.\n\nYour filter is kept exactly as it is. Tap below and everything turns back on, with nothing to set up again."},
     {"type":"BUTTONS","buttons":[{"type":"URL","text":"Get full access",
      "url":"https://londonhomefinder.co.uk/upgrade?t={{1}}",
      "example":["https://londonhomefinder.co.uk/upgrade?t=AbC123xyz"]}]}]}'
```

No variable in the body, so no `body_text` example — one wording covers both
ways a period ends, out of days and out of its message allowance, and there was
nothing left for a variable to say. The button's variable is the upgrade token,
without which the page cannot tell whose plan is being bought; a declared
button variable is not optional, so a send with no token is refused with 132000
and the notifier does not attempt one.

`language` must be plain `en`. Picking "English (UK)" in the editor gives
`en_GB`, the code sends `en`, and every send fails with 132001.

**Who gets it.** Only a plan somebody paid for, and only when their window is
shut. An ended trial is told nothing on this path: the template is a marketing
message at three times the price of one inside the window, and the warning
before the end has already gone out while the window was open — see
`claim_plan_notices`, which treats the window shutting as a deadline of its
own, and `worth_a_template` in the outbox, which is where the line is drawn.

**Category is not yours to decide, in either direction.** This one was
submitted as `UTILITY`, came back `MARKETING`, and has since been reviewed as
`UTILITY` again. Meta classifies templates itself and re-classifies them after
approval, so neither the submission nor last quarter's answer settles it.
`utility` is what the notifier bills it at by default now — $0.022 rather than
$0.0635, see 0070 — and `WA_NOTICE_CATEGORY` is there to follow Meta if it
moves again. It prices the ledger row and nothing else. Read the current answer
off the template:

```sh
curl -s "https://graph.facebook.com/v21.0/$WABA_ID/message_templates?fields=name,status,category" \
  -H "Authorization: Bearer $WA_ACCESS_TOKEN"
```

Marketing costs something other than money, which is why the move is worth
more than the three cents: Meta pauses marketing templates first when a
number's quality rating drops, and they count against the business-initiated
ceiling that business verification raises. A utility template is subject to
neither.

**The button shape matters more than the category.** With a url button the
notice is a dead end for delivery: tapping one sends us nothing, so the
24-hour window — shut by definition, because a plan ends after a day of silence
— is still shut when the payment clears. Meta refuses the receipt with 131047
and every alert the person has just paid for waits in the queue until they
write in of their own accord.

A **quick reply** fixes that, because a tap on one is an inbound message. The
window reopens before the payment rather than after it, and the site answers
the tap with the same checkout link the url button carried:

**Do not edit `plan_notice_paid`. Make a second template beside it.** Editing
the live one puts it back through review, and review is where the category gets
decided again — but the worse problem is the middle. `WA_NOTICE_BUTTON` has to
match whatever Meta is actually serving, and during a review of an edited
template which version that is, is not something this document can promise. Get
it wrong in either direction and every notice is refused with 132000.

A second template removes the question. Both are approved, the old one stays
live and working, and one deploy switches from one to the other — which is also
the rollback, because the old name is still there:

```sh
curl -s -X POST "https://graph.facebook.com/v21.0/$WABA_ID/message_templates" \
  -H "Authorization: Bearer $WA_ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"plan_notice_reply","language":"en","category":"UTILITY",
   "components":[
     {"type":"BODY",
      "text":"Your alerts have stopped — your plan has run out.\n\nYour filter is kept exactly as it is. Tap below and everything turns back on, with nothing to set up again."},
     {"type":"BUTTONS","buttons":[{"type":"QUICK_REPLY","text":"Get full access"}]}]}'
```

Then, in one go: `WA_NOTICE_TEMPLATE=plan_notice_reply` **and**
`WA_NOTICE_BUTTON=reply`, then restart. Either variable alone is 132000.

**The name is free to be anything** — nothing in the code knows it, the worker
reads it out of `WA_NOTICE_TEMPLATE` and posts whatever it finds. Pick a name
that has never existed on this WABA, though: Meta holds a deleted template's
name for 30 days and refuses it in the meantime, which looks exactly like the
name being invalid. `plan_ended_reply` and `plan_notice_qr` are equally good if
this one is taken.

### By hand, in WhatsApp Manager

The same thing through the panel, for when curl is not to hand. **Create
template**, not Edit:

| Field | Value |
| --- | --- |
| Name | `plan_notice_reply` |
| Category | Utility |
| Language | **English** — not English (UK), which is `en_GB` and 132001 |
| Header | None |
| Body | the text above, **with no variables in it** |
| Footer | none |
| Buttons | **Quick reply** → `Get full access` |

The button is the only part that differs from what is live, and it differs
completely: where the old one is *Call to action → Visit website → Dynamic*
with a url and a `{{1}}` on the end, this is *Quick reply* with nothing but
words on it. No url, no variable, no sample value to fill in — the panel will
not ask for one, and if it does you have the wrong button type selected.

The body is unchanged, including "Tap below and everything turns back on",
which was already written for a tap rather than for a link and needs no
rewording.

**Why there is no link any more.** There was never a link in the body; it was
inside the button, as a fixed prefix plus the upgrade token in `{{1}}`. With a
quick reply the link moves to the message the site sends back the moment the
tap arrives — and it is better there: that token is minted on the tap and
lives an hour, where the template's was minted on the drain that sent the
notice and could be most of a day old before anybody pressed it.

| `WA_NOTICE_BUTTON` | The template must have | What the tap does |
| --- | --- | --- |
| `url` (default) | `URL` with `?t={{1}}` | opens the browser, window stays shut |
| `reply` | `QUICK_REPLY`, no variables | sends `pay` to the webhook, window reopens |

Wait for `APPROVED` and check the category came back `UTILITY` before switching
either variable. Until then the url template is live and the defaults are
correct.

### The payment receipt

A second template, and the one that closes the case the quick reply cannot.
The notice only reaches people whose plan had ended; a payment can clear into a
shut window for other reasons too — somebody who tapped yesterday and paid
today, somebody who came back from browser history, a trial that was never sent
a notice at all. So the receipt itself needs a route to a shut window:

```sh
curl -s -X POST "https://graph.facebook.com/v21.0/$WABA_ID/message_templates" \
  -H "Authorization: Bearer $WA_ACCESS_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"payment_received","language":"en","category":"UTILITY",
   "components":[
     {"type":"BODY",
      "text":"Payment received — thank you. Your plan is extended and your filter is exactly as you left it.\n\nOne thing left to do. WhatsApp only lets us send you alerts once you have replied to us, so tap the button below — that is what starts them."},
     {"type":"BUTTONS","buttons":[{"type":"QUICK_REPLY","text":"Start my alerts"}]}]}'
```

**The button is the message.** It says what tapping does rather than what
tapping shows: the tap is the only thing that reopens the window, so a label
like "Show my plan" offers somebody a reason to ignore it, and ignoring it is
the one outcome that leaves a paying subscriber with no alerts. The body says
why it is being asked for, because an instruction with no reason behind it
reads as a growth trick rather than as the platform's own rule — which is what
it is.

Twenty characters on the label. Meta allows twenty-five on a quick reply, but
the rest of this codebase is built to twenty (`BUTTON_CHARS`) and truncation is
silent, so there is nothing to gain by going near the real limit.

Then `WA_RECEIPT_TEMPLATE=payment_received` **on Vercel**, not on the server:
the Stripe webhook is the site's. Left unset, the receipt is simply not sent
into a shut window and the log names this variable as the reason.

A post-purchase confirmation with no selling in it is the textbook utility
case, so this one should come back `UTILITY` without argument. Check anyway —
the category is Meta's either way, and `wa-meter` prices the row at the
template rate on the assumption that it did.

The listing template's shape is fixed by the code, so do not rebuild it by hand
unless you have to. In order: `{{1}}` area or address, `{{2}}` price,
`{{3}}` bedrooms, `{{4}}` bathrooms, `{{5}}` available from, `{{6}}` furnishing
— plus one url button `https://londonhomefinder.co.uk/l/{{1}}` taking the
listing id. Add an image header only if `WA_TEMPLATE_IMAGE=true`, or every send
fails with 132000.

## 5. Webhook subscription and billing

```sh
curl -s "https://graph.facebook.com/v21.0/$WABA_ID/subscribed_apps" -H "Authorization: Bearer $WA_ACCESS_TOKEN"
```

The app should be listed already; `POST` to the same path if not. Subscription
is per WABA, not per app, which is why a new WABA goes silent until this is
done.

WhatsApp Manager → **Billing** → add a card. **Every message is billed** —
free-form replies inside the 24-hour window as much as templates outside it.
That was not true when this was written, and the code was built on the old
rule: see 0063, which moved the nine-hundred allowance onto messages and
started counting all of them in `wa_messages`.

Three rates, and the prices live in `plan_settings` in **millionths of a US
dollar** — the currency Meta bills in, left unconverted so that no invented
exchange rate ends up quoted as a cost:

```sql
-- free-form inside the 24-hour window: $0.022 a message, already set by 0063
-- the plan notice, which carries a payment button and is therefore marketing:
--   $0.0635, set by 0066
-- the listing template, utility: $0.022 as well — the same as service, set by
--   0067. Its own column because they are two rates that are equal today,
--   not one rate with two names
SELECT wa_service_micros, wa_marketing_micros, wa_template_micros
  FROM plan_settings;
```

Nothing on WhatsApp is free: there is no free service tier any more, so every
message has a price and only the marketing one stands apart. Messages sent
before a rate was set carry no money — the price is stamped at send time so
that setting one now does not rewrite a month already billed — and the
dashboard tile says how many, an understated total that admits it. In practice
almost everything is the service rate: the queue holds any alert whose window
is shut, so the listing template is never actually sent, and the only template
that goes out is the plan notice.

Which rate a given row was charged at is `wa_messages.billed_as`, written by
the notifier because only it knows which payload it posted. For the notice it
comes from `WA_NOTICE_CATEGORY` — see step 4 — so if Meta re-classifies the
template, one variable and a restart puts the ledger back in step with the
invoice.

## 6. Business profile

WhatsApp Manager → **Business profile**: description, email
`support@londonhomefinder.co.uk`, website. This is what somebody sees when they
tap the name in the chat. Leave the address empty unless it is an office.

## 7. The settings

**Vercel (Production), then Redeploy** — environment changes do not reach a
deployment that is already built:

```
WA_PHONE_NUMBER_ID=<your number's id, not the test one>
WHATSAPP_NUMBER=<the number, e.g. 447721317302>
```

**The server, `/etc/london-home-finder.env`:**

```
WA_PHONE_NUMBER_ID=<the same id>
WA_NOTICE_TEMPLATE=plan_notice_paid
```

`WA_NOTICE_CATEGORY` is not needed: it defaults to `utility`, which is what the
latest review returned. Set it only if Meta moves the template again — check
with the curl in step 4 whenever the invoice looks wrong.

`WA_NOTICE_BUTTON` is not needed either until the quick-reply template is
approved. It defaults to `url`, the shape that is live; set it to `reply` in
the same deploy that the new template goes live in, and in neither case before.

`WA_RECEIPT_TEMPLATE` goes on **Vercel only** — the Stripe webhook is the
site's, and the worker never sends a receipt. Leaving it unset costs the
receipt on a shut window and says so in the log.

`WA_PHONE_NUMBER_ID` is needed in both places: the worker sends the alerts and
the site answers what comes back. `WHATSAPP_NUMBER` is the site's only, because
the wa.me link on the button is built from it — including the one on the
payment-received page, which is the other half of reopening the window. Unchanged:
`WA_ACCESS_TOKEN`, `WA_APP_SECRET`, `WA_VERIFY_TOKEN`, `WA_TEMPLATE_NAME`.

## 8. Reset the conversation windows — easy to miss

The 24-hour window is tracked as `user_channels.last_inbound_at`, and nothing in
the code knows our own number changed. A number that wrote to the old one still
looks like an open window; free-form text would be sent, Meta would answer
131047, and that code is treated as permanent — so the alert is dropped rather
than retried.

```sql
UPDATE user_channels SET last_inbound_at = NULL WHERE channel = 'whatsapp';
```

## 9. Prove it end to end

Free-form text only works after somebody writes in, so a template is the honest
first test:

```sh
curl -s -X POST "https://graph.facebook.com/v21.0/$WA_PHONE_NUMBER_ID/messages" \
  -H "Authorization: Bearer $WA_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messaging_product":"whatsapp","to":"<your number>",
       "type":"template",
       "template":{"name":"plan_notice_paid","language":{"code":"en"},
       "components":[{"type":"button","sub_type":"url","index":"0",
         "parameters":[{"type":"text","text":"AbC123xyz"}]}]}}'
```

One component, and no body among them: the body declares no variables, and a
component the template does not declare is 132000. The token is a sample here,
so the button lands on "that link has expired" — which is the page working, not
the send failing.

Reply from your phone, then read the Vercel log:

- `wa webhook { messages: 1, … }` — the delivery arrived and was accepted
- a reply sent back

What each failure means:

- **401 and `signature does not match WA_APP_SECRET`** — step 7 was not
  redeployed, or another app is subscribed to the same URL
- **`whatsapp reply not sent: WA_ACCESS_TOKEN is not set in this deployment`** —
  the token reached the server but not Vercel
- **131047 on a send** — the window is shut and the template is missing or not
  approved

Last, the real thing: fill the form on the site, press **Connect WhatsApp**, send
the prefilled message, and check the criteria card comes back.

## Blockers worth recognising

Each of these cost an evening once.

**"Your business does not yet meet WhatsApp policy requirements. Update your
business information."** The portfolio's contact fields are incomplete. Business
Settings → Business info, and fill *every* field: legal name (your own name is
fine with no company), country, street address, city, postcode, business phone,
website, email. None of it is shown in chats — the chat-visible profile is a
separate thing. Reload the page fully before retrying; the create form caches.

**No "Message templates" under Account tools, and
`This WABA is not allowed to create or update templates`.** One cause, two
symptoms: the WABA has no *verified* number. Check
`phone_numbers` for `code_verification_status`. A WABA whose number is
`NOT_VERIFIED` is incomplete, and Meta hides and refuses template tools for it.

**`136024` / `2388091`: "Request code failed… wait for 1 hour".** A cooldown.
Every retry inside it extends it, so wait. Note that Meta sends
`is_transient: false` with a message saying "temporarily" — under this one code
it hides both real throttling and "the number is still registered elsewhere", so
use the hour to check the number is actually free. Try `code_method=VOICE`
after: the SMS route is usually the throttled one.

**A number vanishing along with its WABA.** Deleting a WhatsApp Business *app*
account removes the WABA that was created for it. Expected, and harmless if the
real work lives in the WABA that already worked.

## What the missing registration actually costs

Not the name. Limits: without business verification a number has a lower ceiling
on business-initiated (template) conversations per 24 hours, and you cannot add
many numbers. Free-form replies inside the window do not count against *that
ceiling* — they are still charged for, which is a separate thing and the one
`wa_messages` tracks.

Read the current ceiling in WhatsApp Manager → Phone numbers → **Messaging
limit**; Meta changes the numbers, so trust the page over any document.

If you do hit it: registering as a sole trader with HMRC is free and online, and
a UTR plus a bank statement is usually enough for verification.
