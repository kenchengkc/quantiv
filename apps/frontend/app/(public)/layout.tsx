import { AppShell } from '@/components/AppShell';

export default function PublicLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell authenticated={false}>{children}</AppShell>;
}
