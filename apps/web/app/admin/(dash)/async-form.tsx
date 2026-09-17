"use client";

import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

// A form that posts without leaving the page, and still works when it cannot.
//
// The markup stays a real <form> with a real action, so a browser with no
// JavaScript submits it the old way and the route's redirect carries it back.
// With JavaScript the same fields go out through fetch, the route answers JSON
// because of the header, and router.refresh() re-renders the server components
// in place — no white flash, no lost scroll position.
export function AsyncForm({
  action,
  children,
  confirm,
  className,
}: {
  action: string;
  children: ReactNode;
  confirm?: string;
  className?: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    if (confirm && !window.confirm(confirm)) return;

    setBusy(true);
    setFailed(null);
    try {
      const response = await fetch(action, {
        method: "POST",
        headers: { "x-async": "1" },
        body: new FormData(form),
      });
      const body = (await response.json().catch(() => ({}))) as {
        error?: string;
        goto?: string | null;
      };
      if (!response.ok) {
        setFailed(body.error ?? `failed with ${response.status}`);
        return;
      }
      form.reset();
      if (body.goto) router.push(body.goto);
      else router.refresh();
    } catch (error) {
      setFailed(String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form method="post" action={action} onSubmit={submit} className={className}>
      <fieldset disabled={busy} style={{ border: "none", margin: 0, padding: 0 }}>
        {children}
      </fieldset>
      {failed && <span className="bad" style={{ fontSize: 11 }}>{failed}</span>}
    </form>
  );
}
