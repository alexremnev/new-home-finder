/**
 * Pence as somebody reads them: "£19.99", but "£20" rather than "£20.00".
 *
 * One definition, because there were six and one of them rounded. The admin's
 * Payments page formatted with `maximumFractionDigits: 0`, so a £19.99 plan was
 * displayed as £20 — a price that was never charged, on the one page whose job
 * is to say what was.
 *
 * Trailing ".00" is dropped because a price list of whole pounds reads as a
 * rounding artefact with it; pence are never dropped, because they are the
 * price.
 */
export function pounds(pence: number): string {
  const sign = pence < 0 ? "−" : "";
  const value = Math.abs(pence) / 100;
  return (
    sign +
    "£" +
    value.toLocaleString("en-GB", {
      minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
      maximumFractionDigits: 2,
    })
  );
}
