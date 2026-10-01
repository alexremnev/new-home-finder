"use server";

import type { MorePage } from "../../paged";
import { historyPage, sentPage } from "./bodies";

// The next page of this account's two journals. Neither is a window — they are
// everything this account has, newest first — so the account and a page number
// are all either needs.

export async function moreSent(userId: number, page: number): Promise<MorePage> {
  return sentPage(userId, page);
}

export async function moreHistory(userId: number, page: number): Promise<MorePage> {
  return historyPage(userId, page);
}
