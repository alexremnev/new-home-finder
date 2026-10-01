"use server";

import { facetPage, type Facet } from "./bodies";

// The next twelve of one breakdown. Both arguments are plain strings, and the
// facet is matched against a fixed list inside the query, so there is nothing
// here a request can inject.

export async function moreFacet(span: string, facet: string, page: number) {
  return facetPage(span, facet as Facet, page);
}
