import type { Metadata } from 'next';
import { auth } from '@clerk/nextjs/server';
import { notFound, redirect } from 'next/navigation';
import { requireMlStatusAdmin } from '@/lib/mlStatusAdmin';
import MlStatusPageClient from './MlStatusPageClient';

export const metadata: Metadata = {
  title: 'Production Controls',
  description: 'Decision safety, data freshness, model controls, and serving diagnostics for Quantiv.',
};

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export default async function MlStatusPage() {
  const { userId } = await auth();
  if (!userId) {
    redirect(`/sign-in?redirect_url=${encodeURIComponent('/ml-status')}`);
  }

  const access = await requireMlStatusAdmin();
  if (!access.ok) notFound();
  return <MlStatusPageClient />;
}
