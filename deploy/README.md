# Running the worker on a VPS

Eight jobs, on systemd timers, on a machine that listens on nothing.

    ingest         every 2 min    read the feed, parse, match, queue
    rightmove      by calendar    read Rightmove search pages
    zoopla_london  by calendar    read the whole of Zoopla London, newest first
    spareroom      by calendar    read SpareRoom as a rotating window
    drain          every 5 min    send the queue, warn about plans, seed filters
    rollup         every 5 min    total the day into daily_stats
    report         hourly         check for silence, alert the ops chat
    purge          nightly        delete rows that have expired

The ones on a plain interval drift, which is what you want from them. The three
portal readers do not: they share one London-time calendar — every 5 minutes on
weekdays 08:00–18:00, every 10 minutes either side of that, hourly between
midnight and 07:00 — because how often it is worth asking a portal depends on
when letting agents publish.

They share the grid by phase rather than all landing on it: `rightmove` on the
boundary, `zoopla_london` a third of the interval later, `spareroom` two thirds.
So on the five-minute band it is :00, :01:40 and :03:20. Those are the only
three jobs that reach outward over the network and two of them share one
residential proxy pool, so starting them together would arrive at three portals
as one burst with a period a stopwatch could read off. The reasoning, band by
band, is in `systemd/london-home-finder-rightmove.timer`.

Each run's individual requests are spaced too, by `SCRAPE_JITTER` — see
`.env.example`. The timers blur the start; that blurs the shape.

## What the scrapers keep in /var/lib

`StateDirectory=london-home-finder` in the unit, so systemd creates
`/var/lib/london-home-finder` and passes it in as `$STATE_DIRECTORY`. Two things
live there, both of them about not arriving at Cloudflare as a stranger every
five minutes:

    exit-<reader>.json    which exit address this reader last used and which
                          ones refused it, so a served address is kept rather
                          than redrawn on the next run
    cookies/*.txt         a cookie jar per exit address and browser. Zoopla's
                          `__cf_bm` lives 30 minutes against a 5-minute timer,
                          so one warm session should span about six runs

The unit is `ProtectSystem=strict`, `ProtectHome=true` and `PrivateTmp=true`, so
this is the only writable path the readers have — and `/tmp` in particular is
wiped between invocations, which is why the store cannot live there.

Nothing in it is a secret and nothing in it cannot be rebuilt by running again,
so it is not backed up. Deleting it costs the next run its warm session and
nothing else. If `exit_port` in the scrape counters changes on every run while
`exit_rotations` stays at zero, this directory is not surviving the process —
that is a deployment fault and not a portal blocking us.

## What runs where, and why

The web app — form, admin console, Telegram webhook, Stripe webhook — stays on
Vercel. It is the half that has to answer an inbound request instantly, always be
up, and hold a valid TLS certificate, and Vercel does all three with no work.

The worker is the opposite: it needs no inbound port at all. Nothing connects to
this machine except you over ssh, which is why the firewall allows nothing else.

Moving the webhook here would mean owning a reverse proxy, certificate renewal and
uptime for the thing that answers people's taps. There is no gain to set against
that.

## First run

    ssh root@<ip>
    REPO=https://github.com/<you>/<repo>.git bash <(curl -fsSL <raw url>/deploy/install.sh)

Or, having cloned by hand:

    sudo -E REPO=<git url> bash deploy/install.sh

The first run creates `/etc/london-home-finder.env` from `.env.example` and stops.
Fill it in and run the script again.

## The session has to be created here

    sudo -u finder -H /opt/london-home-finder/.venv/bin/python -m worker login

Not copied from another machine. Telegram notices a user session moving between
countries and may ask for confirmation or close it; a session created at the
address it will live at never moves.

## Afterwards

    systemctl list-timers 'london-home-finder-*'
    journalctl -u london-home-finder@ingest -n 50
    journalctl -u 'london-home-finder*' -f

    systemctl start london-home-finder@drain     # run one now

## Updating

    sudo -E bash /opt/london-home-finder/deploy/install.sh

Pulls, reinstalls dependencies, replaces the units, restarts the timers. It does
not touch `/etc/london-home-finder.env`.

## When a job fails

`OnFailure=` runs `notify-failure.sh`, which curls Telegram directly — no database
involved. That is deliberate: `report.py` finds problems by reading the database, so
when the database is what is wrong it is silent for the same reason everything else
is. An alert about the store cannot live in the store.

One alert per job per hour. The timers fire every five minutes, and an outage
lasting an afternoon would otherwise send fifty identical messages.
