import type { Metadata } from 'next';
import AboutPageClient from './AboutPageClient';

export const metadata: Metadata = {
  title: 'About',
  description:
    'How Quantiv reads option chains: straddle EM, IV term structure, twelve-quarter realized history, and a LightGBM quantile forecast — with the math.',
};

export default function AboutPage() {
  return <AboutPageClient />;
}
