// Reading a place out of what somebody typed.
//
// Its own module because two different surfaces need the same answer: the web form
// and (until it was removed) the bot. "Camden Town" and "E11 4EG" have to mean the
// same thing wherever they are typed, and two copies of that rule would drift —
// which is the whole reason this is not a private helper inside a page.
//
// Tolerant about input, strict about the result: anything recognisable is accepted,
// and everything else is reported rather than silently dropped. A district nobody
// covers is a filter that matches nothing, and finding that out weeks later is
// worse than being told now.

const OUTWARD = /^[A-Z]{1,2}\d{1,2}[A-Z]?$/;


export function readDistricts(
  text: string,
  allowed: string[],
  names: Record<string, string> = {},
): { codes: string[]; unknown: string[]; notCovered: string[] } {
  const codes: string[] = [];
  const unknown: string[] = [];
  const notCovered: string[] = [];
  const permitted = new Set(allowed.map((code) => code.toUpperCase()));

  const add = (code: string, source: string) => {
    if (!permitted.has(code)) notCovered.push(source);
    else if (!codes.includes(code)) codes.push(code);
  };

  // Commas first, and names before codes. "Camden Town" contains a space, so
  // splitting the whole input on whitespace — which the code-only version did —
  // would have torn every two-word name in half.
  for (const part of text.split(/[,;\n]+/).map((p) => p.trim()).filter(Boolean)) {
    const named = names[part.toLowerCase().replace(/\s+/g, " ")];
    if (named) {
      add(named.toUpperCase(), part);
      continue;
    }

    // Not a name, so read it as one or more codes: "SE16 E14" is two, and
    // "E11 4EG" is one with its inward half attached.
    let recognised = false;
    for (const token of part.toUpperCase().split(/\s+/).filter(Boolean)) {
      const cleaned = token.replace(/[^A-Z0-9]/g, "");
      if (!cleaned) continue;
      // An inward code — "4EG" — is the second half of a postcode whose first half
      // has already been taken. Ignored rather than reported: complaining would
      // make pasting a full postcode feel like a mistake.
      if (/^\d[A-Z]{2}$/.test(cleaned)) { recognised = true; continue; }
      if (OUTWARD.test(cleaned)) { add(cleaned, token); recognised = true; }
    }
    if (!recognised) unknown.push(part);
  }
  return { codes, unknown, notCovered };
}
