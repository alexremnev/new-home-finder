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
  -d '{"name":"plan_notice","language":"en","category":"UTILITY",
       "components":[{"type":"BODY",
         "text":"Your {{1}} has ended. You now receive {{2}} that match your filter. Your filter is kept exactly as it is — reply here and we will send you the link to turn everything back on.",
         "example":{"body_text":[["free trial","20% of the listings"]]}}]}'
```

`example` is required whenever the text has variables.

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

WhatsApp Manager → **Billing** → add a card. Free-form replies inside the
24-hour window are not billed; templates are, and a template is exactly what
goes out when the window is shut.

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
WA_NOTICE_TEMPLATE=plan_notice
```

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
       "template":{"name":"plan_notice","language":{"code":"en"},
       "components":[{"type":"body","parameters":[
         {"type":"text","text":"free trial"},
         {"type":"text","text":"20% of the listings"}]}]}}'
```

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
many numbers. Free-form replies inside the window do not count against it, and
most alerts here are those.

Read the current ceiling in WhatsApp Manager → Phone numbers → **Messaging
limit**; Meta changes the numbers, so trust the page over any document.

If you do hit it: registering as a sole trader with HMRC is free and online, and
a UTR plus a bank statement is usually enough for verification.
