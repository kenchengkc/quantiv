import type { Metadata } from 'next';
import AboutPageClient from './AboutPageClient';

export const metadata: Metadata = {
  title: 'About',
  description:
    'A visual guide to how Quantiv compares option-implied movement, realized earnings reactions, model quantiles, and publication controls.',
};

export default function AboutPage() {
  return <AboutPageClient />;
}
