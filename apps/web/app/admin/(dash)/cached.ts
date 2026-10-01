import { cache } from "react";

import { jobStates, planMix, subscriberPage } from "@/lib/admin-queries";

import { spanFrom } from "./span";

// What more than one panel on the same page asks for, asked once.
//
// Each panel fetches on its own, inside its own Suspense boundary — that is
// what lets twelve queries run at the same time instead of in a queue. The
// cost of it is that two panels built from the same numbers used to run the
// same query twice: the System tab asked for every job's state once for the
// verdict banner and again for the tiles below it, and the Subscribers tab
// read the same page of accounts once for its tiles and again for its table.
//
// `cache` is per request, so the second caller of the same query with the same
// arguments gets the first one's promise. Nothing is held between requests —
// the dashboard is `force-dynamic`, and a number a minute old is a wrong
// number.
//
// ── why the window has to be cached too ──────────────────────────────────
//
// `cache` matches arguments by identity, and a `Win` is a fresh object every
// time `spanFrom` is called, so caching a query that takes one would never
// hit. Resolving the window through here instead gives every panel on a page
// the same object — which also means they all measure "now" from the same
// instant, rather than each from the moment its own query happened to start.

/** The one window a request means by a range key. */
export const windowFor = cache(spanFrom);

/** Every job's state over the window — the verdict banner and the job tiles. */
export const jobStatesOnce = cache(jobStates);

/** The plan mix — the Plans card and the count on the tile above it. */
export const planMixOnce = cache(planMix);

/** A page of accounts — the tiles and the table are built from the same one. */
export const subscriberPageOnce = cache(subscriberPage);
