# Adding a plan, or changing a price

Everything about a plan except its Stripe Price id lives in the `plans` table, so
adding one is an `INSERT` and no deploy. The Price ids are the only part that has
to be created by hand, twice — once in the test Stripe account and once in the
live one — because a Price id belongs to one account and means nothing in the
other.

The columns involved:

| Column                   | Meaning                                                     |
| ------------------------ | ----------------------------------------------------------- |
| `key`                    | Internal name. Referenced by `users.plan` and `payments`, so it never changes once anybody is on it. |
| `display_name`           | What the subscriber reads. `'1 month'`.                     |
| `price_pence`            | `2000` is £20. `0` means the plan is not sold.              |
| `duration_days`          | How long it lasts. `NULL` means it never expires.           |
| `duration_days_whatsapp` | Same, when it was started from WhatsApp. `NULL` means use `duration_days`. |
| `channel`                | `'telegram'`, `'whatsapp'`, or `NULL` for either.           |
| `max_districts`          | How many areas the filter may name.                         |
| `delivery_share`         | Percentage of matches actually sent. `100` for anything paid. |
| `enabled`                | `false` retires it without breaking the history pointing at it. |
| `stripe_price_id`        | Price id in the **test** account. Used unless `STRIPE_MODE=live`. |
| `stripe_price_id_live`   | Price id in the **live** account. Used when `STRIPE_MODE=live`. |

## 1. Insert the row first, with no Price ids

A plan with no Price id cannot be bought: `/api/checkout` returns 503 and says
which column is empty. That is the right order — the row can be reviewed on the
upgrade page before any money can move.

```sql
INSERT INTO plans
       (key, display_name, max_districts, duration_days,
        price_pence, is_signup_default, enabled, channel)
VALUES ('wa_month', '1 month', 5, 30, 2000, false, true, 'whatsapp')
ON CONFLICT (key) DO UPDATE
   SET display_name  = EXCLUDED.display_name,
       max_districts = EXCLUDED.max_districts,
       duration_days = EXCLUDED.duration_days,
       price_pence   = EXCLUDED.price_pence,
       enabled       = EXCLUDED.enabled,
       channel       = EXCLUDED.channel;
```

`channel` matters. A plan scoped to `'whatsapp'` is the only kind a WhatsApp
subscriber is shown and the only kind checkout will sell them — the scoping is
enforced in `/api/checkout`, not only on the page, because the plan key arrives
in the query string and can be edited.

## 2. Make the Price in the **test** account

1. Stripe dashboard → make sure the **Test mode** toggle is on.
2. **Product catalogue → Add product**.
   - Name: what the subscriber should see on the Stripe page, e.g.
     `London Home Finder — 1 month (WhatsApp)`.
   - Price: `20.00`, currency **GBP**.
   - Billing: **One-off**, not recurring. Nothing here takes a repeating
     payment; a plan is a period that is bought and then ends.
3. Save, then open the product and find its **Pricing** table.
4. Copy the id from that row. It starts with **`price_`**.

> The id on the product heading starts with `prod_` and is the wrong one.
> Checkout rejects a `prod_` id with a message saying exactly this, because it
> is the mistake that is easiest to make and hardest to see.

```sql
UPDATE plans SET stripe_price_id = 'price_...' WHERE key = 'wa_month';
```

## 3. Make the same Price in the **live** account

Repeat step 2 with the **Test mode** toggle **off**. It is a different account,
so it needs its own product and its own Price — the ids are unrelated.

```sql
UPDATE plans SET stripe_price_id_live = 'price_...' WHERE key = 'wa_month';
```

Doing both now, rather than when going live, is the point of having two columns:
switching accounts later is then `STRIPE_MODE` alone, and so is switching back.

## 4. Check it

```sql
SELECT key, display_name, price_pence, duration_days, duration_days_whatsapp,
       channel, enabled,
       stripe_price_id      IS NOT NULL AS has_test_price,
       stripe_price_id_live IS NOT NULL AS has_live_price
  FROM plans
 ORDER BY price_pence, key;
```

Both flags should be true, and every id should start with `price_`:

```sql
SELECT key, stripe_price_id, stripe_price_id_live
  FROM plans
 WHERE coalesce(stripe_price_id, 'price_')      NOT LIKE 'price_%'
    OR coalesce(stripe_price_id_live, 'price_') NOT LIKE 'price_%';
```

Then, in whichever mode the environment is in, send `/pay` to the bot and open
the link. The plan should appear with the right price, and only for the messenger
it is scoped to. Pay it in test mode with `4242 4242 4242 4242` and confirm
`plan_until` moves by `duration_days`.

## Changing a price

Stripe Prices are immutable. Changing what somebody pays means making a **new**
Price and pointing the row at it:

```sql
UPDATE plans
   SET price_pence = 2500,
       stripe_price_id      = 'price_new_test',
       stripe_price_id_live = 'price_new_live'
 WHERE key = 'wa_month';
```

Update `price_pence` in the same statement. It is what every page and message
quotes, and a row saying £20 while Stripe charges £25 is the worst of the
possible failures — it is not an error anywhere, it is just untrue.

Archive the old Price in Stripe afterwards, not before: a checkout session
opened a minute earlier still refers to it.

## Retiring a plan

```sql
UPDATE plans SET enabled = false WHERE key = 'week';
```

Never delete the row. People are on it and `payments` references it.
`active_subscriptions` resolves a plan without consulting `enabled`, so
disabling stops it being offered while everyone currently on it keeps it until
it expires.

## Changing a trial

The trial is the plan with `is_signup_default`, currently `trial`. Its length is
per messenger, because a WhatsApp alert is billed per message and a Telegram one
is not:

```sql
UPDATE plans
   SET duration_days = 2,            -- Telegram
       duration_days_whatsapp = 1    -- WhatsApp
 WHERE key = 'trial';
```

The clock starts when a messenger is connected, not when the form is submitted
— see `beginSubscription` — so an abandoned form costs nobody a trial day. The
landing page reads both numbers out of this row, so the cards cannot promise a
length the bot will not grant.
