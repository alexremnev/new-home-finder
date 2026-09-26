"use server";

import type { ReactNode } from "react";

import {
  AccountHistory, AccountPayments, AccountSent, AlertBuckets,
} from "./bodies";

export async function refreshBuckets(userId: number, span: string): Promise<ReactNode> {
  return AlertBuckets({ userId, span });
}

export async function refreshPayments(userId: number): Promise<ReactNode> {
  return AccountPayments({ userId });
}

export async function refreshSent(userId: number): Promise<ReactNode> {
  return AccountSent({ userId });
}

export async function refreshHistory(userId: number): Promise<ReactNode> {
  return AccountHistory({ userId });
}
