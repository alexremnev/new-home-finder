// London neighbourhoods, by the name people use for them.
//
// ── why this file exists ─────────────────────────────────────────────────────
//
// `districtNames()` derives names from listings already seen, which is the right
// default: it cannot go stale against the data and it costs nothing to maintain.
// What it cannot do is name a place before a listing from it has arrived. Canada
// Water is in SE16 whether or not the feed has mentioned it this week, and a
// dropdown that omits it looks like the service does not cover Canada Water.
//
// So: this is the floor, and observed names are laid on top of it. A name the feed
// actually uses wins, because that is the word attached to real listings.
//
// ── what this list is and is not ─────────────────────────────────────────────
//
// It is the neighbourhoods somebody looking for a flat would type. It is NOT
// exhaustive and is not trying to be — London has several hundred named areas, many
// of them contested at the edges, and a list of every hamlet would be mostly
// entries nobody searches for and cannot be checked by anybody reading it.
//
// Each name maps to the postcode district its centre sits in. Several are honest
// simplifications: a neighbourhood can straddle two districts, and the one chosen
// here is where most of its rental stock is. Somebody who wants the other half can
// switch to postcode mode and add it, which is the reason both modes exist.
//
// Adding one is a line. Keep it alphabetical within its district block so a
// duplicate is visible rather than discovered.

export const NEIGHBOURHOODS: Record<string, string> = {
  // ── E, east ────────────────────────────────────────────────────────────────
  "Whitechapel": "E1",
  "Shoreditch": "E1",
  "Spitalfields": "E1",
  "Wapping": "E1W",
  "Bethnal Green": "E2",
  "Bow": "E3",
  "Mile End": "E3",
  "Chingford": "E4",
  "Clapton": "E5",
  "East Ham": "E6",
  "Walthamstow": "E17",
  "Leyton": "E10",
  "Leytonstone": "E11",
  "Manor Park": "E12",
  "Plaistow": "E13",
  "Canary Wharf": "E14",
  "Isle of Dogs": "E14",
  "Poplar": "E14",
  "Stratford": "E15",
  "Canning Town": "E16",
  "Royal Docks": "E16",
  "Hackney": "E8",
  "Dalston": "E8",
  "Homerton": "E9",
  "Forest Gate": "E7",
  "South Woodford": "E18",

  // ── EC / WC, the City and centre ───────────────────────────────────────────
  "Barbican": "EC1Y",
  "Clerkenwell": "EC1R",
  "Farringdon": "EC1M",
  "Aldgate": "EC3N",
  "Bloomsbury": "WC1B",
  "Holborn": "WC1V",
  "King's Cross": "N1C",
  "Covent Garden": "WC2E",

  // ── N, north ───────────────────────────────────────────────────────────────
  "Islington": "N1",
  "Angel": "N1",
  "Barnsbury": "N1",
  "East Finchley": "N2",
  "Finchley": "N3",
  "Finsbury Park": "N4",
  "Highbury": "N5",
  "Highgate": "N6",
  "Holloway": "N7",
  "Crouch End": "N8",
  "Hornsey": "N8",
  "Wood Green": "N22",
  "Bounds Green": "N11",
  "Muswell Hill": "N10",
  "Palmers Green": "N13",
  "Southgate": "N14",
  "Tottenham": "N17",
  "Stoke Newington": "N16",
  "Edmonton": "N18",
  "Archway": "N19",
  "Enfield": "EN1",

  // ── NW, north-west ─────────────────────────────────────────────────────────
  "Camden Town": "NW1",
  "Regent's Park": "NW1",
  "Golders Green": "NW11",
  "Cricklewood": "NW2",
  "Willesden": "NW2",
  "Hampstead": "NW3",
  "Belsize Park": "NW3",
  "Hendon": "NW4",
  "Kentish Town": "NW5",
  "Tufnell Park": "NW5",
  "Queen's Park": "NW6",
  "Kilburn": "NW6",
  "West Hampstead": "NW6",
  "Brent Cross": "NW4",
  "St John's Wood": "NW8",
  "Maida Vale": "W9",
  "Colindale": "NW9",
  "Wembley": "HA9",
  "Harrow": "HA1",
  "Neasden": "NW10",
  "Kensal Green": "NW10",

  // ── SE, south-east ─────────────────────────────────────────────────────────
  "Bermondsey": "SE1",
  "Borough": "SE1",
  "London Bridge": "SE1",
  "Southwark": "SE1",
  "Walworth": "SE17",
  "Elephant and Castle": "SE1",
  "Rotherhithe": "SE16",
  "Canada Water": "SE16",
  "Surrey Quays": "SE16",
  "Deptford": "SE8",
  "Greenwich": "SE10",
  "North Greenwich": "SE10",
  "Blackheath": "SE3",
  "Brockley": "SE4",
  "New Cross": "SE14",
  "Peckham": "SE15",
  "Nunhead": "SE15",
  "Camberwell": "SE5",
  "Denmark Hill": "SE5",
  "East Dulwich": "SE22",
  "West Dulwich": "SE21",
  "Dulwich Village": "SE21",
  "Herne Hill": "SE24",
  "Forest Hill": "SE23",
  "Sydenham": "SE26",
  "Crystal Palace": "SE19",
  "Norwood": "SE19",
  "Charlton": "SE7",
  "Woolwich": "SE18",
  "Plumstead": "SE18",
  "Eltham": "SE9",
  "Lee": "SE12",
  "Lewisham": "SE13",
  "Catford": "SE6",
  "Bellingham": "SE6",
  "Abbey Wood": "SE2",
  "Thamesmead": "SE28",
  "Kidbrooke": "SE3",
  "Penge": "SE20",
  "Bromley": "BR1",
  "Croydon": "CR0",

  // ── SW, south-west ─────────────────────────────────────────────────────────
  "Westminster": "SW1",
  "Pimlico": "SW1V",
  "Belgravia": "SW1X",
  "Victoria": "SW1E",
  "Chelsea": "SW3",
  "South Kensington": "SW7",
  "Earls Court": "SW5",
  "Fulham": "SW6",
  "Parsons Green": "SW6",
  "Battersea": "SW11",
  "Clapham Junction": "SW11",
  "Clapham": "SW4",
  "Brixton": "SW2",
  "Streatham": "SW16",
  "Balham": "SW12",
  "Tooting": "SW17",
  "Wandsworth": "SW18",
  "Southfields": "SW18",
  "Putney": "SW15",
  "Roehampton": "SW15",
  "Barnes": "SW13",
  "Mortlake": "SW14",
  "Wimbledon": "SW19",
  "Southwark Park": "SE16",
  "Stockwell": "SW9",
  "Vauxhall": "SW8",
  "Nine Elms": "SW8",
  "South Lambeth": "SW8",
  "Kennington": "SE11",
  "Colliers Wood": "SW19",
  "Merton": "SW19",
  "Morden": "SM4",
  "Raynes Park": "SW20",
  "Richmond": "TW9",
  "Kingston": "KT1",

  // ── W, west ────────────────────────────────────────────────────────────────
  "Mayfair": "W1K",
  "Marylebone": "W1U",
  "Soho": "W1D",
  "Fitzrovia": "W1T",
  "Paddington": "W2",
  "Bayswater": "W2",
  "Notting Hill": "W11",
  "Holland Park": "W11",
  "Ladbroke Grove": "W10",
  "North Kensington": "W10",
  "Kensington": "W8",
  "Chiswick": "W4",
  "Ealing": "W5",
  "Acton": "W3",
  "Hanwell": "W7",
  "Southall": "UB1",
  "West Ealing": "W13",
  "Shepherd's Bush": "W12",
  "White City": "W12",
  "Hammersmith": "W6",
  "Brentford": "TW8",
  "Hounslow": "TW3",
  "Feltham": "TW13",
  "Twickenham": "TW1",
};

/**
 * The name to show for a district, preferring what the feed actually calls it.
 *
 * Observed names win over the list above, because a name attached to real listings
 * is the word in use, and the list is a floor rather than an authority. Both are
 * folded into one map keyed by district so the dropdown holds one entry per place.
 */
export function neighbourhoodNames(
  observed: Record<string, string>,
  covered: string[],
): { code: string; name: string }[] {
  const allowed = new Set(covered.map((code) => code.toUpperCase()));
  const byCode = new Map<string, string>();

  for (const [name, code] of Object.entries(NEIGHBOURHOODS)) {
    const upper = code.toUpperCase();
    if (allowed.has(upper) && !byCode.has(upper)) byCode.set(upper, name);
  }
  for (const [name, code] of Object.entries(observed)) {
    const upper = code.toUpperCase();
    if (!allowed.has(upper)) continue;
    // Overwrites the static name deliberately — see above.
    byCode.set(upper, name.replace(/\b[a-z]/g, (c) => c.toUpperCase()));
  }

  return [...byCode]
    .map(([code, name]) => ({ code, name }))
    .sort((a, b) => a.name.localeCompare(b.name));
}
