"use server";

import {
  districtDaily, recipientDaily, type DistrictDay, type RecipientDay,
} from "@/lib/admin-queries";

import { spanFrom } from "../span";

// This page's cards are sorted and paged in the browser, so its refreshes hand
// back rows rather than rendered output — the view needs the numbers to sort
// them. Every other page's actions return finished markup; see ../panel.tsx.

export async function refreshDays(span: string): Promise<DistrictDay[]> {
  return districtDaily(spanFrom(span));
}

export async function refreshRecipients(span: string): Promise<RecipientDay[]> {
  return recipientDaily(spanFrom(span));
}
