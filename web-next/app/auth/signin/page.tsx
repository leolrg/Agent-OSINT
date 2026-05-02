import { signIn } from '../../../auth';
import { AuthError } from 'next-auth';
import { redirect } from 'next/navigation';

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const params = await searchParams;
  const errorMessage =
    params.error === 'CredentialsSignin'
      ? 'Invalid email or password.'
      : params.error
      ? 'Could not sign in. Please try again.'
      : null;

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-[380px] py-8">
        <div className="text-[10px] font-extrabold tracking-[0.18em] mb-6">
          AGENT-OSINT
        </div>

        <div className="label-uppercase">SIGN IN</div>
        <h1 className="text-[24px] font-extrabold leading-[1.05] heavy-rule pb-2">
          Welcome back.
        </h1>

        {errorMessage && (
          <div className="mt-4 border-2 border-[#7f1d1d] bg-white px-3 py-2 text-[12px] text-[#7f1d1d]">
            {errorMessage}
          </div>
        )}

        <form
          className="mt-5 space-y-2.5"
          action={async (data) => {
            'use server';
            try {
              await signIn('credentials', {
                email: data.get('email'),
                password: data.get('password'),
                redirectTo: params.next ?? '/scans',
              });
            } catch (e) {
              if (e instanceof AuthError) {
                const next = params.next
                  ? `&next=${encodeURIComponent(params.next)}`
                  : '';
                redirect(`/auth/signin?error=${e.type}${next}`);
              }
              throw e;
            }
          }}
        >
          <input
            name="email" type="email" required placeholder="Email"
            className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[13px]"
          />
          <input
            name="password" type="password" required placeholder="Password"
            className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[13px]"
          />
          <button
            type="submit"
            className="block w-full bg-ink text-white py-2 text-[11px] font-bold tracking-[0.12em] uppercase mt-3"
          >
            SIGN IN →
          </button>
        </form>

        <div className="mt-4 text-[11px] text-muted">
          No account?{' '}
          <a href="/auth/signup" className="text-ink font-semibold underline">
            Sign up
          </a>
        </div>
      </div>
    </div>
  );
}
