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

**Category is not yours to decide.** This one was submitted as `UTILITY` and
came back `MARKETING`: Meta classifies templates itself, and a body that sells
with a button that takes payment is a marketing template whatever the
submission said. It re-classifies after approval too. `marketing` is therefore
what the notifier bills it at by default; `WA_NOTICE_CATEGORY` is there to
follow Meta if it moves the template again, and it prices the ledger row and
nothing else. Read the current answer off the template:

```sh
curl -s "https://graph.facebook.com/v21.0/$WABA_ID/message_templates?fields=name,status,category" \
  -H "Authorization: Bearer $WA_ACCESS_TOKEN"
```

Marketing also costs something other than money: Meta pauses marketing
templates first when a number's quality rating drops, and they count against
the business-initiated ceiling that business verification raises. A utility
template is subject to neither.

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

`WA_NOTICE_CATEGORY` is not needed: it defaults to `marketing`, which is what
the review returned. Set it only if Meta moves the template to another
category.

`WA_PHONE_NUMBER_ID` is needed in both places: the worker sends the alerts and
the site answers what comes back. `WHATSAPP_NUMBER` is the site's only, because
the wa.me link on the button is built from it. Unchanged: `WA_ACCESS_TOKEN`,
`WA_APP_SECRET`, `WA_VERIFY_TOKEN`, `WA_TEMPLATE_NAME`.

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
