/** @type {import('next').NextConfig} */
const nextConfig = {
  // Emit a self-contained server bundle (.next/standalone) so the Docker
  // runtime image ships only the pruned deps it actually needs.
  output: "standalone",
};

export default nextConfig;
