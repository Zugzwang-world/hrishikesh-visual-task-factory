import type { NextConfig } from "next";

// A static site: `next build` writes out/, which Vercel serves as is. The sample data lives in
// public/data (written by `vtf export <run> --format explorer --out web/public/data`).
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  reactStrictMode: true,
};

export default nextConfig;
