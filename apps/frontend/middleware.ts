import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server';

// Protect browser pages that require a signed-in Clerk session. API routes
// must NOT be `auth.protect()`ed here:
// Clerk's dev-mode handshake rewrites the request to a `/clerk_<id>` URL
// which then falls through to [symbol] and returns HTML instead of JSON.
// Each API route calls `auth()` itself and returns a proper JSON 401.
const isProtected = createRouteMatcher(['/watchlist(.*)']);

export default clerkMiddleware(async (auth, req) => {
  if (isProtected(req)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // Skip Next.js internals and static files, always run on API routes.
    // `json` MUST be listed — Clerk's dev-mode handshake will rewrite static
    // .json files in /public to an HTML page, breaking every screener / week
    // / symbol fetch with "Unexpected token '<', '<!DOCTYPE'... is not valid
    // JSON". Same family of bug as the original /api/watchlist regression.
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|json|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    '/(api|trpc)(.*)',
  ],
};
