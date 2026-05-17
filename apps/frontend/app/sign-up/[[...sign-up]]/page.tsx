import type { Metadata } from 'next';
import { SignUp } from '@clerk/nextjs';

export const metadata: Metadata = {
  title: 'Sign Up',
};

export default function SignUpPage() {
  return (
    <div
      style={{
        minHeight: '70vh',
        display: 'grid',
        placeItems: 'center',
        padding: '40px 20px',
      }}
    >
      <SignUp
        appearance={{
          variables: {
            colorPrimary: 'var(--accent)',
            colorBackground: 'var(--bg)',
            colorText: 'var(--ink)',
            colorInputBackground: 'var(--bg)',
            colorInputText: 'var(--ink)',
            borderRadius: '10px',
          },
        }}
        signInUrl="/sign-in"
      />
    </div>
  );
}
