// Manual validation (no zod dep). Reject malformed input with 400 — never coerce.

export type ConfigPatch = {
  niche_queries: string[];
  broad_categories: string[];
  open_problems: string[];
  seed_vocab: Record<string, number>;
  seed_papers?: string[];
};

export function validatePatch(body: any): { ok: true; value: ConfigPatch } | { ok: false; error: string } {
  if (typeof body !== "object" || body == null) return { ok: false, error: "body must be an object" };

  const strArr = (v: any, name: string) => {
    if (!Array.isArray(v) || v.some((x) => typeof x !== "string")) throw new Error(`${name} must be a string array`);
    return v.map((s: string) => s.trim()).filter(Boolean);
  };

  try {
    const niche_queries = strArr(body.niche_queries, "niche_queries");
    const broad_categories = strArr(body.broad_categories, "broad_categories");
    const open_problems = strArr(body.open_problems, "open_problems");
    const seed_papers = body.seed_papers === undefined ? [] : strArr(body.seed_papers, "seed_papers");

    const sv = body.seed_vocab;
    if (typeof sv !== "object" || sv == null || Array.isArray(sv)) throw new Error("seed_vocab must be a {term: weight} object");
    const seed_vocab: Record<string, number> = {};
    for (const [k, v] of Object.entries(sv)) {
      const key = String(k).trim().toLowerCase();
      if (!key) throw new Error("seed_vocab has an empty term");
      if (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > 5) {
        throw new Error(`seed_vocab["${key}"] must be a number in [0, 5]`);
      }
      seed_vocab[key] = v;
    }
    if (broad_categories.length === 0) throw new Error("broad_categories cannot be empty");

    return { ok: true, value: { niche_queries, broad_categories, open_problems, seed_vocab, seed_papers } };
  } catch (e: any) {
    return { ok: false, error: e?.message ?? "invalid payload" };
  }
}

// quoted phrases inside arXiv-style queries — used to know which new queries
// warrant an on-demand backfill
export function phrasesOf(queries: string[]): string[] {
  const out = new Set<string>();
  for (const query of queries) {
    for (const m of query.matchAll(/"([^"]+)"/g)) {
      const p = m[1].trim();
      if (p.length >= 4) out.add(p);
    }
  }
  return [...out];
}
