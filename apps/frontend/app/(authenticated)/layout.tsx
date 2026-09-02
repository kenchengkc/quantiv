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
          fontFamily:
            'var(--font-nunito-sans), ui-sans-serif, system-ui, sans-serif',
        },
      }}
    >
      <AppShell authenticated>{children}</AppShell>
    </ClerkProvider>
  );
}
