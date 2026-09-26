"use server";

import type { ReactNode } from "react";

import {
  Duplicates, Faults, Feeds, Health, Jobs, LastRuns, ListingsCreated, LogLines,
  MessagesRead, QueueTiles, RunsChart,
} from "./bodies";

// One card each on the System tab. Every argument is a plain string or number,
// so nothing about the page's state has to be smuggled into a button.

export async function refreshHealth(span: string): Promise<ReactNode> {
  return Health({ span });
}

export async function refreshFaults(): Promise<ReactNode> {
  return Faults();
}

export async function refreshFeeds(span: string): Promise<ReactNode> {
  return Feeds({ span });
}

export async function refreshDuplicates(span: string): Promise<ReactNode> {
  return Duplicates({ span });
}

export async function refreshJobs(span: string): Promise<ReactNode> {
  return Jobs({ span });
}

export async function refreshRuns(span: string): Promise<ReactNode> {
  return RunsChart({ span });
}

export async function refreshMessages(span: string): Promise<ReactNode> {
  return MessagesRead({ span });
}

export async function refreshListings(span: string): Promise<ReactNode> {
  return ListingsCreated({ span });
}

export async function refreshQueue(span: string): Promise<ReactNode> {
  return QueueTiles({ span });
}

export async function refreshLastRuns(): Promise<ReactNode> {
  return LastRuns();
}

export async function refreshLog(
  span: string,
  job: string | undefined,
  level: string | undefined,
  page: number,
): Promise<ReactNode> {
  return LogLines({ span, job, level, page });
}
