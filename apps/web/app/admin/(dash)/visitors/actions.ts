"use server";

import type { ReactNode } from "react";

import { VisitorChart, VisitorFacet, VisitorTiles, type Facet } from "./bodies";

// One card's worth of refresh. Each takes only the range key and, where it
// needs it, which breakdown — both plain strings, so nothing about the page's
// state has to be serialised into the button.
//
// They re-run one query and return one card's contents. Nothing else on the
// page is touched, and the page is not re-rendered.

export async function refreshTiles(span: string): Promise<ReactNode> {
  return VisitorTiles({ span });
}

export async function refreshChart(span: string): Promise<ReactNode> {
  return VisitorChart({ span });
}

export async function refreshFacet(span: string, facet: string): Promise<ReactNode> {
  return VisitorFacet({ span, facet: facet as Facet });
}
