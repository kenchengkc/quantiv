import type { Metadata } from 'next';
import MlStatusPageClient from './MlStatusPageClient';

export const metadata: Metadata = {
  title: 'ML Backend Status',
  description: 'Operational status for Quantiv ML serving, model inventory, and feature coverage.',
};

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export default function MlStatusPage() {
  return <MlStatusPageClient />;
}
