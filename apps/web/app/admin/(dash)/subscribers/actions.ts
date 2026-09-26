"use server";

import type { ReactNode } from "react";

import {
  AlertDistricts, AlertPrices, AlertsDelivered, EveryoneTable, PlanMix,
  SubscriberTiles,
} from "./bodies";

export async function refreshTiles(span: string, page: number): Promise<ReactNode> {
  return SubscriberTiles({ span, page });
}

export async function refreshPlans(): Promise<ReactNode> {
  return PlanMix();
}

export async function refreshDelivered(span: string): Promise<ReactNode> {
  return AlertsDelivered({ span });
}

export async function refreshDistricts(span: string): Promise<ReactNode> {
  return AlertDistricts({ span });
}

export async function refreshPrices(span: string): Promise<ReactNode> {
  return AlertPrices({ span });
}

export async function refreshEveryone(span: string, page: number): Promise<ReactNode> {
  return EveryoneTable({ span, page });
}
