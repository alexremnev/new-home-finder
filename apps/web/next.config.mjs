/** @type {import('next').NextConfig} */
export default {
  // The webhook and the form both talk to Postgres, so nothing here may be
  // statically prerendered against a build-time database.
  experimental: { serverActions: { bodySizeLimit: "64kb" } },
};
