import type { MetadataRoute } from "next";

const SITE = (process.env.SITE_URL ?? "https://londonhomefinder.co.uk").replace(/\/+$/, "");

// Only pages a stranger can open and would want. /upgrade needs a token and
// /admin needs a password, so neither belongs here.
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${SITE}/`, lastModified: new Date(), changeFrequency: "daily", priority: 1 },
  ];
}
