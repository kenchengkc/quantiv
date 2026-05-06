import { SignIn } from '@clerk/nextjs';

export default function SignInPage() {
  return (
    <div
      style={{
        minHeight: '70vh',
        display: 'grid',
        placeItems: 'center',
        padding: '40px 20px',
      }}
    >
      <SignIn
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
        signUpUrl="/sign-up"
      />
    </div>
  );
}
