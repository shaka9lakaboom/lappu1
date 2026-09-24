import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'SkillMirror - AI-Assisted Skill Graph & Mastery Platform',
  description: 'Mastery-based learning engine for capturing AI interactions, attributing skills, and measuring genuine mastery.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-slate-950 text-slate-100 antialiased min-h-screen">
        {children}
      </body>
    </html>
  );
}
