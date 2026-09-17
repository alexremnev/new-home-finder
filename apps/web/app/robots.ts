import type { MetadataRoute } from "next";

const SITE = (process.env.SITE_URL ?? "https://londonhomefinder.co.uk").replace(/\/+$/, "");

// The admin and the api are not for crawlers: one is private and the other is
// machinery. Everything else is the point of the site.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: ["/admin", "/api/", "/l/"] }],
    sitemap: `${SITE}/sitemap.xml`,
    host: SITE,
  };
}
