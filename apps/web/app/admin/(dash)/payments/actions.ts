"use server";

import type { ReactNode } from "react";

import {
  ByPlan, ByProvider, FreeExtensions, LatestPayments, PaymentTiles, TakenPerDay,
} from "./bodies";

// One card each. The range key is the only argument any of them needs, and the
// two tables do not even need that — they are the latest rows, not a window.

export async function refreshTiles(span: string): Promise<ReactNode> {
  return PaymentTiles({ span });
}

export async function refreshSeries(span: string): Promise<ReactNode> {
  return TakenPerDay({ span });
}

export async function refreshByPlan(span: string): Promise<ReactNode> {
  return ByPlan({ span });
}

export async function refreshByProvider(span: string): Promise<ReactNode> {
  return ByProvider({ span });
}

export async function refreshComps(): Promise<ReactNode> {
  return FreeExtensions();
}

export async function refreshLatest(): Promise<ReactNode> {
  return LatestPayments();
}
