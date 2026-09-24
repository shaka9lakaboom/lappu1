import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Workspace packages ship TypeScript source.
  transpilePackages: ['@skillmirror/contracts', '@skillmirror/config', '@skillmirror/ui'],
};

export default nextConfig;
