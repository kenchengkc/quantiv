import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { requireMlStatusAdmin } from '@/lib/mlStatusAdmin';
import MlStatusPageClient from './MlStatusPageClient';

export const metadata: Metadata = {
  title: 'ML Backend Status',
  description: 'Operational status for Quantiv ML serving, model inventory, and feature coverage.',
};

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export default async function MlStatusPage() {
  const access = await requireMlStatusAdmin();
  if (!access.ok) notFound();
  return <MlStatusPageClient />;
}
