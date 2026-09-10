/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // pg is a server-only dependency; keep it external to the server bundle
  serverExternalPackages: ["pg"],
};

export default nextConfig;
