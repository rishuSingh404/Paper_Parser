import { q, q1 } from "./db";

export type State = Awaited<ReturnType<typeof getState>>;

export async function getState() {
  const digest = await q1<any>(
    `SELECT run_date, mode, papers_scanned, broad_ranked, cross_domain, niche_papers,
            rising_terms, bursts, clusters_summary, created_at
     FROM digests ORDER BY run_date DESC LIMIT 1`,
  );

  const lastRun = await q1<any>(
    `SELECT max(run_date) AS d FROM digests`,
  );
  const daysOld =
    lastRun?.d != null
      ? Math.floor((Date.now() - new Date(lastRun.d).getTime()) / 86_400_000)
      : null;

  const vocab = await q<any>(
    `SELECT term, weight, is_seed, last_seen_iso_week
     FROM vocab ORDER BY is_seed DESC, weight DESC`,
  );

  const config = await q1<any>(`SELECT * FROM config WHERE id = 1`);

  const suggestions = await q<any>(
    `SELECT id, kind, payload, rationale, created_at
     FROM config_suggestions WHERE status = 'pending' ORDER BY created_at DESC LIMIT 20`,
  );

  const runs = await q<any>(
    `SELECT id, run_date, kind, status, started_at, finished_at, stats, errors, config_version
     FROM run_log ORDER BY id DESC LIMIT 8`,
  );

  const discovery = await q<any>(
    `SELECT term, tier, current_count, baseline_count, delta, distinct_groups
     FROM discovery_bursts
     WHERE run_date = (SELECT max(run_date) FROM discovery_bursts)
     ORDER BY (tier = 'first_appearance') DESC, delta DESC LIMIT 30`,
  );

  const working = await q<any>(
    `WITH cards AS (
       SELECT run_date, outcome,
              CASE WHEN outcome IS NULL AND CURRENT_DATE - run_date >= 5 THEN 'ignored' ELSE outcome END AS eff
       FROM digest_cards
       WHERE section = 'broad' AND rank <= 10
         AND run_date > CURRENT_DATE - INTERVAL '8 weeks'
     )
     SELECT to_char(run_date, 'IYYY-"W"IW') AS week,
            count(*) FILTER (WHERE eff = 'liked') AS liked,
            count(*) FILTER (WHERE eff IN ('disliked','muted')) AS disliked,
            count(*) FILTER (WHERE eff = 'ignored') AS ignored,
            count(*) AS n
     FROM cards GROUP BY 1 ORDER BY 1`,
  );

  return {
    generated_at: new Date().toISOString(),
    staleness: {
      last_run_date: lastRun?.d ?? null,
      days_old: daysOld,
      stale: daysOld != null && daysOld > 2,
    },
    digest,
    vocab,
    config,
    suggestions,
    discovery,
    runs,
    working: working.map((w) => {
      const judged = Number(w.liked) + Number(w.disliked) + Number(w.ignored);
      return {
        ...w,
        precision: judged ? Number(w.liked) / judged : null,
      };
    }),
  };
}
