/** @type {import('next').NextConfig} */
export default {
  // The webhook and the form both talk to Postgres, so nothing here may be
  // statically prerendered against a build-time database.
  experimental: { serverActions: { bodySizeLimit: "64kb" } },

  // The link-preview image reads the logo and the flat out of `public/` to
  // inline them, and file tracing cannot see through
  // `readFileSync(join(process.cwd(), …))` — so both files are named here. It
  // is only needed if that route is ever rendered on demand rather than
  // prerendered, which is exactly the case that is hard to notice: a missing
  // preview image is invisible until somebody pastes a link.
  outputFileTracingIncludes: {
    "/opengraph-image": ["./public/logo-mark.png", "./public/sample-flat.png"],
  },
};
