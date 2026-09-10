import { NextRequest, NextResponse } from "next/server";
import { q } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const paperId = req.nextUrl.searchParams.get("paper_id");
  if (paperId) {
    const signals = await q(
      `SELECT run_date, name, value, detail FROM signals
       WHERE paper_id = $1 ORDER BY run_date DESC, name`,
      [paperId],
    );
    return NextResponse.json({ paper_id: paperId, signals }, { headers: { "cache-control": "no-store" } });
  }
  const runs = await q(
    `SELECT id, run_date, kind, status, started_at, finished_at, stats, errors, config_version
     FROM run_log ORDER BY id DESC LIMIT 25`,
  );
  return NextResponse.json({ runs }, { headers: { "cache-control": "no-store" } });
}
