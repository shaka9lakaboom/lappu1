import type { Metadata } from 'next';
import { PRODUCT_NAME } from '@skillmirror/config';

import './globals.css';

export const metadata: Metadata = {
  title: PRODUCT_NAME,
  description: 'Use AI. Keep the skill.',
};

export default function RootLayout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en">
      <body className="min-h-screen">{children}</body>
    </html>
  );
}
