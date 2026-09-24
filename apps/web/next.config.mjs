/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ['@skillmirror/contracts', '@skillmirror/config', '@skillmirror/ui'],
};

export default nextConfig;
