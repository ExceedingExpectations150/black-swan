/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output is for the Docker image (a self-contained server
  // bundle). On Vercel it is unnecessary and interferes with Vercel's own
  // build output — a known cause of "404: NOT_FOUND" — so disable it there.
  // Vercel sets VERCEL=1 during builds.
  output: process.env.VERCEL ? undefined : "standalone",
};

export default nextConfig;
