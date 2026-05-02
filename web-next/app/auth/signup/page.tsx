import { createUser } from './actions';
import { redirect } from 'next/navigation';

export default async function SignUpPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const params = await searchParams;
  const errorMessage = params.error ? decodeURIComponent(params.error) : null;

  return (
    <div className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-[380px] py-8">
        <div className="text-[10px] font-extrabold tracking-[0.18em] mb-6">
          AGENT-OSINT
        </div>

        <div className="label-uppercase">SIGN UP</div>
        <h1 className="text-[24px] font-extrabold leading-[1.05] heavy-rule pb-2">
          Create an account.
        </h1>

        <div className="mt-3.5 px-2.5 py-2 bg-amber border-l-[3px] border-amber2 text-[11px] leading-[1.4] text-muted">
          <strong className="text-amber2">Invite-only.</strong> Your email must be on the allowed list.
        </div>

        {errorMessage && (
          <div className="mt-3.5 border-2 border-[#7f1d1d] bg-white px-3 py-2 text-[12px] text-[#7f1d1d]">
            {errorMessage}
          </div>
        )}

        <form
          className="mt-3.5 space-y-2.5"
          action={async (data) => {
            'use server';
            const result = await createUser(data);
            if (result?.error) {
              redirect(`/auth/signup?error=${encodeURIComponent(result.error)}`);
            }
            redirect('/auth/signin');
          }}
        >
          <input
            name="email" type="email" required placeholder="Email"
            className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[13px]"
          />
          <input
            name="password" type="password" required minLength={12}
            placeholder="Password (min 12 chars)"
            className="block w-full border-2 border-ink bg-white px-2.5 py-2 text-[13px]"
          />
          <button
            type="submit"
            className="block w-full bg-ink text-white py-2 text-[11px] font-bold tracking-[0.12em] uppercase mt-3"
          >
            CREATE ACCOUNT →
          </button>
        </form>

        <div className="mt-4 text-[11px] text-muted">
          Have an account?{' '}
          <a href="/auth/signin" className="text-ink font-semibold underline">
            Sign in
          </a>
        </div>
      </div>
    </div>
  );
}
