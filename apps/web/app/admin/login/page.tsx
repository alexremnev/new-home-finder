// The login form.
//
// A plain HTML form posting to a route handler: no client component, no state, no
// JavaScript. A password field is the last place to want a hydration boundary, and
// the browser's own form is better at this than anything written here would be.

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
    <div className="panel" style={{ maxWidth: "22rem", margin: "3rem auto" }}>
      <h1 style={{ fontSize: "1.35rem" }}>Admin</h1>
      <p className="hint" style={{ marginBottom: "1.25rem" }}>
        This console shows every subscriber&apos;s filter. Do not open it on a
        machine you do not control.
      </p>

      <form method="post" action="/api/admin/login">
        <label htmlFor="password" className="field-label">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          autoFocus
        />
        {reason && (
          <p className="error">
            {reason}
            {e === "locked" && mins ? ` (${mins} minutes)` : ""}
          </p>
        )}
        <button type="submit">Sign in</button>
      </form>
    </div>
  );
}
