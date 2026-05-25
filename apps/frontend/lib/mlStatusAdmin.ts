import { auth, currentUser } from '@clerk/nextjs/server';

type MlStatusAdminAccess =
  | { ok: true; userId: string; email: string }
  | { ok: false; status: 401 | 403 };

function normalizeEmail(value: string | null | undefined): string | null {
  const email = value?.trim().toLowerCase();
  return email || null;
}

export function mlStatusAdminEmailSet(): Set<string> {
  const raw =
    process.env.ML_STATUS_ADMIN_EMAILS ??
    process.env.ADMIN_EMAILS ??
    (process.env.NODE_ENV === 'production' ? '' : process.env.E2E_CLERK_USER_EMAIL) ??
    '';
  return new Set(
    raw
      .split(',')
      .map((item) => normalizeEmail(item))
      .filter((item): item is string => Boolean(item)),
  );
}

export async function requireMlStatusAdmin(): Promise<MlStatusAdminAccess> {
  const { userId } = await auth();
  if (!userId) return { ok: false, status: 401 };

  const allowlist = mlStatusAdminEmailSet();
  if (allowlist.size === 0) return { ok: false, status: 403 };

  const user = await currentUser();
  const primaryEmail = normalizeEmail(user?.primaryEmailAddress?.emailAddress);
  const firstVerifiedEmail = user?.emailAddresses
    ?.map((email) => normalizeEmail(email.emailAddress))
    .find((email): email is string => Boolean(email));
  const email = primaryEmail ?? firstVerifiedEmail;

  if (!email || !allowlist.has(email)) return { ok: false, status: 403 };
  return { ok: true, userId, email };
}
