# apps/web

The landing page, the subscription endpoint, and the Telegram webhook. Next.js on
Vercel Hobby; nothing here needs a paid tier.

The worker and this app share one database and touch different parts of it. The
worker writes listings and drains the outbox; this app writes users,
subscriptions, and channels, and deletes them on `/stop`. Neither imports the
other.

## Environment

Copy `.env.example` to `.env.local` for local work, and set the same names in the
Vercel project. `DATABASE_URL` must be the **pooled** Supabase string (port 6543):
a serverless function opens a connection per instance, and the direct port runs
out of them under any real traffic.

## Local

```bash
npm install
npm run dev        # http://localhost:3000
npm test           # the form parser and the command parser
npm run typecheck
```

## Registering the webhook

Telegram delivers updates by HTTP, so the route has to be reachable over HTTPS —
which means after a deploy, not from localhost.

```bash
SECRET=$(openssl rand -hex 24)     # also set as TELEGRAM_WEBHOOK_SECRET
curl -s "https://api.telegram.org/bot$TELEGRAM_TOKEN/setWebhook" \
  -d "url=https://<your-app>.vercel.app/api/tg/webhook" \
  -d "secret_token=$SECRET" \
  -d "allowed_updates=[\"message\"]"

curl -s "https://api.telegram.org/bot$TELEGRAM_TOKEN/getWebhookInfo"
```

`secret_token` is the whole of the route's authentication. Telegram returns it in
`X-Telegram-Bot-Api-Secret-Token` on every update, and the route rejects anything
without it — the URL alone is not a secret, because URLs end up in logs.

`allowed_updates=["message"]` keeps the volume down: nothing else is read.

To develop the webhook locally, point it at a tunnel instead:

```bash
npx localtunnel --port 3000        # or ngrok http 3000
```

Remember to point it back at the deployment afterwards. A bot has exactly one
webhook, so whoever set it last owns it.

## The flow

```
form  → POST /api/subscribe → users(pending, start_token) + subscriptions(backfill_from=now())
      → t.me/<bot>?start=<token>
START → webhook → user_channels(telegram, chat_id), users.status='active', consent_at=now()
      → welcome
                    ⋮ worker: match → notify ⋮
/stop → webhook → one transaction:
          DELETE subscriptions        WHERE user_id = ?
          DELETE notifications        WHERE user_id = ? AND status = 'queued'
          UPDATE users SET status = 'stopped'
      → confirmation
```

Three details of that deletion are load-bearing:

1. **The queue is cleared in the same transaction.** Otherwise the next worker run
   sends messages to someone who has just unsubscribed. That is a PECR breach, not
   an untidiness.
2. **Send history survives.** `notifications.subscription_id` is `ON DELETE SET
   NULL` and the row is keyed by user, so someone who returns is not shown
   listings they have already seen.
3. **The user row survives** as `status='stopped'`. Erasure on request is a
   separate route with a cascade; retention of stopped users is a separate job.

## What is not here yet

`/admin` and `/api/admin/runs`. Until then the run log is read with SQL — see the
queries in `CHEATSHEET.md`.
