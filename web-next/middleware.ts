import NextAuth from 'next-auth';
import { authConfig } from './auth.config';

const { auth } = NextAuth(authConfig);

export default auth((req) => {
  const { pathname } = req.nextUrl;
  const isAuthPage = pathname.startsWith('/auth/');
  const isApi = pathname.startsWith('/api/');
  if (isAuthPage || isApi) return;
  if (!req.auth) {
    const url = req.nextUrl.clone();
    url.pathname = '/auth/signin';
    url.searchParams.set('next', pathname);
    return Response.redirect(url);
  }
});

export const config = {
  matcher: ['/((?!_next|favicon.ico).*)'],
};
