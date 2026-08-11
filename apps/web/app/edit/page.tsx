// The edit page. A thin shell: the form loads the current filter from
// /api/subscription with the token in the URL, so the token never has to be
// embedded in server-rendered HTML that might be cached.

import { EditForm } from "./form";

export const dynamic = "force-dynamic";

export default function Page() {
  return (
    <main>
      <h1 style={{ fontSize: "1.4rem" }}>Change your filter</h1>
      <EditForm />
    </main>
  );
}
