import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server';

// Routes that require a signed-in user. Everything else (earnings calendar,
// ticker detail, about, auth pages, etc.) stays public.
const isProtected = createRouteMatcher([
  '/watchlist(.*)',
  '/api/watchlist(.*)',
]);

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
