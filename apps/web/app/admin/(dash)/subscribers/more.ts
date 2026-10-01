"use server";

import type { MorePage } from "../paged";
import { districtsPage, everyonePage } from "./bodies";

// The next page of the two lists on this tab. The range travels as its key, a
// plain string, so nothing about the page's state has to be serialised into a
// button.

export async function moreEveryone(span: string, page: number): Promise<MorePage> {
  return everyonePage(span, page);
}

export async function moreDistricts(span: string, page: number) {
  return districtsPage(span, page);
}
