"use server";

import type { MorePage } from "../paged";
import { compsPage, paymentsPage } from "./bodies";

// The next page of each journal on this tab. Neither is a window: they are the
// latest rows, so a page number is the only argument either needs.

export async function moreComps(page: number): Promise<MorePage> {
  return compsPage(page);
}

export async function morePayments(page: number): Promise<MorePage> {
  return paymentsPage(page);
}
