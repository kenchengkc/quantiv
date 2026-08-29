'use client';

import Link from 'next/link';
import { SignedIn, SignedOut, UserButton } from '@clerk/nextjs';

export function TopbarAuthControls({ mobile = false }: { mobile?: boolean }) {
  return (
    <>
      <SignedOut>
        <Link
          href="/sign-in"
          style={{
            fontSize: mobile ? 13 : 12,
            color: 'var(--ink-2)',
            padding: mobile ? '8px 16px' : '6px 14px',
            border: '1px solid var(--line)',
            borderRadius: 999,
            transition: 'border-color 140ms ease, color 140ms ease',
          }}
        >
          Sign in
        </Link>
      </SignedOut>
      <SignedIn>
        <span style={{ display: 'inline-flex' }}>
          <UserButton
            afterSignOutUrl="/"
            appearance={{
              elements: {
                avatarBox: {
                  width: mobile ? 32 : 28,
                  height: mobile ? 32 : 28,
                },
              },
            }}
          />
        </span>
      </SignedIn>
    </>
  );
}
