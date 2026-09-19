import type { MetadataRoute } from "next";

const SITE = (process.env.SITE_URL ?? "https://londonhomefinder.co.uk").replace(/\/+$/, "");

// The api and the redirector are not for crawlers; everything else is the point
// of the site.
//
// /admin is deliberately absent. Naming it here would publish the path to
// anybody who reads robots.txt, and it is not indexable anyway: the middleware
// sends every request without a session to the login page.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: ["/api/", "/l/"] }],
    sitemap: `${SITE}/sitemap.xml`,
    host: SITE,
  };
}
