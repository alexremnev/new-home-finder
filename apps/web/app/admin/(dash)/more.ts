"use server";

import { faultsPage, lastRunsPage, logRows } from "./bodies";
import type { MorePage } from "./paged";

// The next page of each list on the System tab.
//
// They call the same functions the first page was rendered from, so there is
// one definition of what a row contains. Every argument is a plain string or
// number, so nothing about the page's state has to be smuggled into a button.

export async function moreFaults(span: string, page: number): Promise<MorePage> {
  return faultsPage(span, page);
}

export async function moreLastRuns(page: number): Promise<MorePage> {
  return lastRunsPage(page);
}

// Serves both the pager and the two filters above the list: the filters ask
// for page 1 of a different question, which is the same query with a different
// `job`. `total` comes back with it because the footer's count changes with
// the filter too.
export async function moreLog(
  span: string,
  job: string | undefined,
  level: string | undefined,
  page: number,
): Promise<MorePage & { total: number }> {
  return logRows(span, job, level, page);
}
