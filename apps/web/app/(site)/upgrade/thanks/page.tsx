export const metadata = { title: "Payment received" };

export default function Page() {
  return (
    <div className="panel">
      <div className="done-tick">✓</div>
      <h1>Payment received</h1>
      <p className="lede">
        Your plan is extended. Send <strong>/show</strong> to the bot to see the new
        limits — if it still shows the old plan, give it a minute and ask again.
      </p>
    </div>
  );
}
