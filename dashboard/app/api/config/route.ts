import { NextRequest, NextResponse } from "next/server";
import crypto from "node:crypto";
import { pool, q, q1 } from "@/lib/db";
import { phrasesOf, validatePatch } from "@/lib/configSchema";

export const dynamic = "force-dynamic";

// Best-effort in-memory rate limit. Vercel serverless is stateless across cold
// starts, so this only slows a sustained burst on a warm instance — proportionate
// for a single-user tool behind a shared secret.
const attempts = new Map<string, { n: number; ts: number }>();
function rateLimited(ip: string): boolean {
  const now = Date.now();
  const e = attempts.get(ip);
  if (!e || now - e.ts > 10 * 60_000) {
    attempts.set(ip, { n: 1, ts: now });
    return false;
  }
  e.n += 1;
  e.ts = now;
  return e.n > 20;
}

function tokenOk(got: string | null): boolean {
  const want = process.env.DASHBOARD_EDIT_TOKEN ?? "";
  if (!want || !got) return false;
  const a = Buffer.from(got);
  const b = Buffer.from(want);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

export async function GET() {
  const config = await q1(
    `SELECT niche_queries, broad_categories, seed_vocab, open_problems, seed_papers,
            sources, embedding_model_version, digest_mode, version
     FROM config WHERE id = 1`,
  );
  const history = await q(
    `SELECT id, edited_at, snapshot->'version' AS version FROM config_history ORDER BY id DESC LIMIT 5`,
  );
  return NextResponse.json({ config, history }, { headers: { "cache-control": "no-store" } });
}

export async function POST(req: NextRequest) {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "local";
  if (rateLimited(ip)) return NextResponse.json({ error: "rate limited" }, { status: 429 });
  if (!tokenOk(req.headers.get("x-edit-token"))) {
    return NextResponse.json({ error: "bad or missing x-edit-token" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const parsed = validatePatch(body);
  if (!parsed.ok) return NextResponse.json({ error: parsed.error }, { status: 400 });
  const patch = parsed.value;

  const client = await pool.connect();
  const enqueued: { term: string; kind: "keyword" | "query" }[] = [];
  try {
    await client.query("BEGIN");
    const cur = (await client.query("SELECT * FROM config WHERE id = 1 FOR UPDATE")).rows[0];
    await client.query("INSERT INTO config_history (snapshot) VALUES ($1::jsonb)", [JSON.stringify(cur)]);

    const beforeVocab = Object.keys(cur.seed_vocab ?? {});
    const afterVocab = patch.seed_vocab;
    const beforeQueries: string[] = cur.niche_queries ?? [];

    await client.query(
      `UPDATE config SET niche_queries = $1, broad_categories = $2, seed_vocab = $3,
              open_problems = $4, seed_papers = $5, version = version + 1, updated_at = now()
       WHERE id = 1`,
      [
        JSON.stringify(patch.niche_queries),
        JSON.stringify(patch.broad_categories),
        JSON.stringify(patch.seed_vocab),
        JSON.stringify(patch.open_problems),
        JSON.stringify(patch.seed_papers ?? []),
      ],
    );

    // vocab sync: new seed terms -> is_seed=true; removed -> is_seed=false (NOT deleted)
    for (const [term, weight] of Object.entries(afterVocab)) {
      if (!beforeVocab.includes(term)) {
        await client.query(
          `INSERT INTO vocab (term, weight, is_seed) VALUES ($1, $2, true)
           ON CONFLICT (term) DO UPDATE SET is_seed = true, weight = EXCLUDED.weight`,
          [term, weight],
        );
        enqueued.push({ term, kind: "keyword" });
      }
    }
    for (const term of beforeVocab) {
      if (!(term in afterVocab)) {
        await client.query("UPDATE vocab SET is_seed = false WHERE term = $1", [term]);
      }
    }

    // new niche queries -> backfill the raw query string
    for (const query of patch.niche_queries) {
      if (!beforeQueries.includes(query)) enqueued.push({ term: query, kind: "query" });
    }

    for (const item of enqueued) {
      await client.query(
        "INSERT INTO term_backfills (term, kind, status) VALUES ($1, $2, 'pending')",
        [item.term, item.kind],
      );
    }
    await client.query("COMMIT");
  } catch (err: any) {
    await client.query("ROLLBACK").catch(() => {});
    client.release();
    return NextResponse.json({ error: err?.message ?? "config update failed" }, { status: 500 });
  }
  client.release();

  // synchronous backfill via the worker web service (enqueue-only fallback)
  const backfills: any[] = [];
  const workerUrl = process.env.WORKER_INTERNAL_URL;
  const secret = process.env.INTERNAL_SHARED_SECRET;
  for (const item of enqueued) {
    if (!workerUrl || !secret) {
      backfills.push({ term: item.term, status: "queued", note: "WORKER_INTERNAL_URL unset — next run finishes it" });
      continue;
    }
    try {
      const res = await fetch(`${workerUrl.replace(/\/$/, "")}/internal/backfill-term`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-internal-secret": secret },
        body: JSON.stringify(item),
        signal: AbortSignal.timeout(280_000),
      });
      backfills.push({ term: item.term, ...(await res.json().catch(() => ({ status: "error" }))) });
    } catch (err: any) {
      backfills.push({ term: item.term, status: "queued", note: String(err?.message ?? err) });
    }
  }

  const justAdded = enqueued.length
    ? await q(
        `SELECT paper_id, title, link, announce_date
         FROM papers WHERE first_seen_at > now() - interval '15 minutes'
         ORDER BY announce_date DESC NULLS LAST LIMIT 120`,
      )
    : [];

  return NextResponse.json({ ok: true, enqueued: enqueued.length, backfills, just_added: justAdded });
}
