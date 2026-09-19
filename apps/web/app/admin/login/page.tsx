export const dynamic = "force-dynamic";

const REASONS: Record<string, string> = {
  wrong: "That is not the password.",
  locked: "Too many attempts. Wait a few minutes and try again.",
};

export default async function AdminLogin({
  searchParams,
}: {
  searchParams: Promise<{ e?: string; mins?: string }>;
}) {
  const { e, mins } = await searchParams;
  const reason = e ? REASONS[e] : undefined;

  return (
    <div className="signin">
      <div className="signin-card">
        <div className="signin-mark" aria-hidden="true">
          🔑
        </div>
        <h1>Dashboard</h1>
        <p className="hint signin-warn">
          This page shows every subscriber&apos;s filter. Do not open it on a machine
          you do not control.
        </p>

        <form method="post" action="/api/admin/login" className="signin-form">
          <label htmlFor="username" className="field-label">
            Login
          </label>
          <input
            id="username"
            name="username"
            type="text"
            autoComplete="username"
            spellCheck={false}
            required
            autoFocus
          />

          <label htmlFor="password" className="field-label">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            placeholder="••••••••••••"
            required
          />
          {reason && (
            <p className="error">
              {reason}
              {e === "locked" && mins ? ` (${mins} minutes)` : ""}
            </p>
          )}
          <button type="submit">Sign in</button>
        </form>

        <p className="signin-foot">
          Five wrong answers and this address waits fifteen minutes.
        </p>
      </div>
    </div>
  );
}
