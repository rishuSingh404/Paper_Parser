import { NextResponse } from "next/server";
import { getState } from "@/lib/state";

// This route MUST reflect the DB's current state on every load — never static.
export const dynamic = "force-dynamic";
export const revalidate = 0;
export const fetchCache = "force-no-store";

export async function GET() {
  try {
    const state = await getState();
    return NextResponse.json(state, { headers: { "cache-control": "no-store, max-age=0" } });
  } catch (err: any) {
    return NextResponse.json(
      { error: err?.message ?? "state error" },
      { status: 500, headers: { "cache-control": "no-store" } },
    );
  }
}
