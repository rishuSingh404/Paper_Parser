import { NextRequest, NextResponse } from "next/server";
import { q, q1 } from "@/lib/db";
import { phrasesOf } from "@/lib/configSchema";
import { buildArxivQuery, labelFor, significantWords, wordBoundaryPattern } from "@/lib/searchQuery";

export const dynamic = "force-dynamic";

// Same one exclusion worker/pipeline/niche.py's domain_marker_phrases() makes
// (see its docstring) — kept in sync by hand since this is a separate runtime.
const TOO_GENERIC_FOR_DOMAIN_MARKER = new Set(["vision-language"]);

function cosine(a: number[], b: number[]): number {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

async function liveMatches(words: string[], phrase: string, extraIds: string[] = []) {
  const conds: string[] = [];
  const params: string[] = [];
  let i = 1;
  for (const w of words) {
    conds.push(`(title || ' ' || COALESCE(abstract,'')) ~* $${i++}`);
    params.push(wordBoundaryPattern(w));
  }
  const localFilter = conds.length ? `(${conds.join(" AND ")})` : "FALSE";
  // extraIds: papers the one-time historical backfill found via arXiv's own
  // relevance search but that don't pass the stricter local AND-filter (e.g.
  // "ECG-WM" and "CARE-ECG" are squarely DGT-relevant but never literally say
  // "hallucination") — shown once, tagged distinctly, so the first result set
  // isn't quietly thinner than what was actually found. Ongoing daily-sweep
  // papers still go through the stricter filter only (extraIds is a one-time
  // top-up, not a growing allowlist).
  const rows = await q<any>(
    `SELECT paper_id, title, abstract, link, announce_date, first_seen_at, categories,
            ${localFilter} AS matched_locally
     FROM papers
     WHERE NOT muted AND (
       ${localFilter}
       OR (title || ' ' || COALESCE(abstract,'')) ILIKE $${i}
       OR paper_id = ANY($${i + 1})
     )
     ORDER BY COALESCE(announce_date, first_seen_at::date) DESC
     LIMIT 150`,
    [...params, `%${phrase.trim()}%`, extraIds],
  );
  return rows;
}

async function tagCrossDomain(rows: any[], cfg: any) {
  if (rows.length === 0) return rows;
  const domainPhrases = phrasesOf(cfg?.niche_queries ?? []).filter(
    (p) => !TOO_GENERIC_FOR_DOMAIN_MARKER.has(p.toLowerCase()),
  );
  const domainRe = domainPhrases.map((p) => new RegExp(`\\b${p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i"));

  const anchorRow = await q1<any>(
    `SELECT model_version FROM anchor_embeddings ORDER BY updated_at DESC LIMIT 1`,
  );
  const mv = anchorRow?.model_version;
  let anchors: { kind: string; text: string; embedding: number[] }[] = [];
  let paperEmb = new Map<string, number[]>();
  if (mv) {
    anchors = await q<any>(
      `SELECT anchor_kind AS kind, text, embedding FROM anchor_embeddings WHERE model_version = $1`,
      [mv],
    );
    const ids = rows.map((r) => r.paper_id);
    const embRows = await q<any>(
      `SELECT paper_id, embedding FROM embeddings WHERE model_version = $1 AND paper_id = ANY($2)`,
      [mv, ids],
    );
    paperEmb = new Map(embRows.map((r: any) => [r.paper_id, r.embedding]));
  }

  return rows.map((r) => {
    const text = `${r.title} ${r.abstract ?? ""}`;
    const in_domain = domainRe.some((re) => re.test(text));
    let embedding_sim: number | null = null;
    let nearest_anchor: string | null = null;
    const vec = paperEmb.get(r.paper_id);
    if (vec) {
      let best = 0, bestKind: string | null = null;
      for (const a of anchors) {
        const s = cosine(vec, a.embedding);
        if (s > best) { best = s; bestKind = a.kind; }
      }
      embedding_sim = Math.round(best * 10000) / 10000;
      nearest_anchor = bestKind;
    }
    return {
      ...r,
      abstract: undefined, // keep the payload light; title+link is enough for this view
      in_domain,
      embedding_sim,
      nearest_anchor,
      possible_transfer: !in_domain,
    };
  });
}

export async function GET() {
  const cfg = await q1<any>(`SELECT niche_queries FROM config WHERE id = 1`);
  const searches = await q<any>(
    `SELECT id, label, phrase, words, arxiv_query, created_at FROM saved_searches ORDER BY id DESC`,
  );
  const withMatches = await Promise.all(
    searches.map(async (s) => {
      const rows = await liveMatches(s.words, s.phrase);
      const tagged = await tagCrossDomain(rows, cfg);
      return { ...s, matches: tagged, total: tagged.length };
    }),
  );
  return NextResponse.json({ searches: withMatches }, { headers: { "cache-control": "no-store" } });
}

export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const phrase = typeof body?.phrase === "string" ? body.phrase.trim() : "";
  if (!phrase || phrase.length < 3) {
    return NextResponse.json({ error: "phrase must be at least 3 characters" }, { status: 400 });
  }
  const words = significantWords(phrase);
  const arxiv_query = buildArxivQuery(phrase);
  const label = typeof body?.label === "string" && body.label.trim() ? body.label.trim().slice(0, 60) : labelFor(phrase);

  const row = await q1<any>(
    `INSERT INTO saved_searches (label, phrase, words, arxiv_query) VALUES ($1, $2, $3, $4)
     RETURNING id, label, phrase, words, arxiv_query, created_at`,
    [label, phrase, words, arxiv_query],
  );

  // one-time historical backfill (keyword_backfill_months, currently 12) so
  // there's something to show immediately, not just future daily sweeps
  const workerUrl = process.env.WORKER_INTERNAL_URL;
  const secret = process.env.INTERNAL_SHARED_SECRET;
  let backfill: any = { status: "skipped", note: "WORKER_INTERNAL_URL/INTERNAL_SHARED_SECRET not set" };
  if (workerUrl && secret) {
    try {
      const res = await fetch(`${workerUrl.replace(/\/$/, "")}/internal/backfill-term`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-internal-secret": secret },
        body: JSON.stringify({ term: arxiv_query, kind: "query" }),
        signal: AbortSignal.timeout(60_000),
      });
      backfill = await res.json().catch(() => ({ status: "error" }));
    } catch (err: any) {
      backfill = { status: "started", note: "backfill runs in the background now — refresh in a minute" };
    }
  }

  const cfg = await q1<any>(`SELECT niche_queries FROM config WHERE id = 1`);
  const backfilledIds: string[] = Array.isArray(backfill?.papers)
    ? backfill.papers.map((p: any) => p.paper_id).filter(Boolean)
    : [];
  const rows = await liveMatches(words, phrase, backfilledIds);
  const tagged = await tagCrossDomain(rows, cfg);

  return NextResponse.json({ ok: true, search: row, backfill, matches: tagged, total: tagged.length });
}

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });
  await q(`DELETE FROM saved_searches WHERE id = $1`, [id]);
  return NextResponse.json({ ok: true });
}
