import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server';

// Only protect the /watchlist page — if unauthenticated, Clerk redirects to
// sign-in. API routes (/api/watchlist/*) must NOT be `auth.protect()`ed here:
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
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    '/(api|trpc)(.*)',
  ],
};
