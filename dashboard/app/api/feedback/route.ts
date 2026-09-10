import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

const VERDICTS = new Set(["liked", "disliked", "muted", "saved"]);

export async function POST(req: NextRequest) {
  let body: any;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }
  const paperId = String(body?.paper_id ?? "");
  const verdict = String(body?.verdict ?? "");
  if (!paperId || !VERDICTS.has(verdict)) {
    return NextResponse.json({ error: "paper_id and verdict (liked|disliked|muted|saved) required" }, { status: 400 });
  }

  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await client.query(
      `INSERT INTO feedback (paper_id, verdict) VALUES ($1, $2)
       ON CONFLICT (paper_id) DO UPDATE
         SET verdict = EXCLUDED.verdict, updated_at = now(), processed_for_vocab = false`,
      [paperId, verdict],
    );
    if (verdict === "muted") {
      await client.query("UPDATE papers SET muted = true WHERE paper_id = $1", [paperId]);
    }
    await client.query(
      `UPDATE digest_cards SET outcome = $2, outcome_at = now()
       WHERE paper_id = $1 AND run_date > CURRENT_DATE - INTERVAL '30 days'`,
      [paperId, verdict],
    );
    await client.query("COMMIT");
  } catch (err: any) {
    await client.query("ROLLBACK").catch(() => {});
    return NextResponse.json({ error: err?.message ?? "feedback failed" }, { status: 500 });
  } finally {
    client.release();
  }
  return NextResponse.json({ ok: true });
}
