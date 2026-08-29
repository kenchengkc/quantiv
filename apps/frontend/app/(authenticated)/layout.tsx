import { ClerkProvider } from '@clerk/nextjs';
import { AppShell } from '@/components/AppShell';

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ClerkProvider
      appearance={{
        variables: {
          colorPrimary: '#1E90FF',
          colorBackground: '#000000',
          colorText: '#fafbfd',
          colorInputBackground: '#000000',
          colorInputText: '#fafbfd',
          borderRadius: '10px',
        },
      }}
    >
      <AppShell authenticated>{children}</AppShell>
    </ClerkProvider>
  );
}
