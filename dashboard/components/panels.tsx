"use client";
import { useState } from "react";

const fmtDate = (d: string | null) => (d ? String(d).slice(0, 10) : "?");

export function StalenessBanner({ s }: { s: any }) {
  if (!s?.stale) return null;
  return (
    <div className="banner">
      ⚠ Latest digest is <b>{s.days_old} days</b> old ({fmtDate(s.last_run_date)}). The daily
      Render cron may be failing — check its logs. Signals below are not current.
    </div>
  );
}

export function RisingTerms({ terms }: { terms: any[] }) {
  if (!terms?.length) return <p className="mut">No rising terms yet — needs ~6 weeks of history.</p>;
  const max = Math.max(1, ...terms.map((t) => Math.abs(t.momentum)));
  return (
    <div className="card">
      {terms.map((t) => (
        <div key={t.term} className="row" style={{ margin: "6px 0" }}>
          <span style={{ width: 190, fontSize: 12.5 }}>{t.term}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(Math.abs(t.momentum) / max) * 100}%` }} />
          </div>
          <span className="mut" style={{ width: 96, textAlign: "right" }}>
            Δ{t.momentum > 0 ? "+" : ""}{t.momentum} (now {t.current})
          </span>
        </div>
      ))}
    </div>
  );
}

export function BurstList({ bursts }: { bursts: any[] }) {
  if (!bursts?.length) return null;
  return (
    <div className="card">
      <b style={{ fontSize: 13 }}>Multi-lab bursts (tracked terms)</b>
      <div className="row" style={{ marginTop: 6 }}>
        {bursts.map((b) => (
          <span key={b.term} className="pill">
            {b.term} — {b.distinct_groups} groups (was {b.prev_distinct_groups})
          </span>
        ))}
      </div>
    </div>
  );
}

export function DiscoveryPanel({ discovery }: { discovery: any[] }) {
  const firstAppearance = (discovery ?? []).filter((d) => d.tier === "first_appearance");
  const bursting = (discovery ?? []).filter((d) => d.tier === "bursting");

  if (!discovery?.length) {
    return (
      <p className="mut">
        Nothing outside your tracked vocab qualified this week — needs a few weeks of history
        to build a baseline. This does not mean nothing new happened; it means nothing cleared
        the "2+ independent groups" bar yet.
      </p>
    );
  }
  return (
    <div className="card">
      <div style={{ marginBottom: 10 }}>
        <b style={{ fontSize: 13 }}>🆕 First appearance — genuinely new this week</b>
        <div className="mut" style={{ marginBottom: 6 }}>
          Zero mentions anywhere in the last 8 weeks, now used by 2+ independent groups. This
          is the actual "JEPA at 2-3 papers" signal — read this list every week, it will
          mostly be noise, that is expected.
        </div>
        {firstAppearance.length === 0 ? (
          <span className="mut">none this week</span>
        ) : (
          <div className="row">
            {firstAppearance.map((d) => (
              <span key={d.term} className="pill" style={{ borderColor: "var(--ok)", color: "var(--ok)" }}
                    title={`first seen this week, ${d.distinct_groups} independent groups`}>
                {d.term} · {d.current_count} groups
              </span>
            ))}
          </div>
        )}
      </div>
      <div>
        <b style={{ fontSize: 13 }}>📈 Still bursting — already had some presence</b>
        <div className="mut" style={{ marginBottom: 6 }}>
          Rising above its own recent baseline, but not brand new — a later-stage version of
          the same signal.
        </div>
        {bursting.length === 0 ? (
          <span className="mut">none this week</span>
        ) : (
          <div className="row">
            {bursting.map((d) => (
              <span key={d.term} className="pill" title={`${d.distinct_groups} groups this week, baseline ${d.baseline_count}`}>
                {d.term} · Δ+{d.delta} ({d.current_count} groups)
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="mut" style={{ marginTop: 8 }}>
        Add one to your vocab from the config panel below if it looks like a real lead.
      </div>
    </div>
  );
}

function FeedbackButtons({ paperId, onDone }: { paperId: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const send = async (verdict: string) => {
    setBusy(true);
    await fetch("/api/feedback", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ paper_id: paperId, verdict }),
    });
    setBusy(false);
    onDone();
  };
  return (
    <div className="row" style={{ marginTop: 8 }}>
      {["liked", "disliked", "saved", "muted"].map((v) => (
        <button key={v} disabled={busy} onClick={() => send(v)}>
          {v === "liked" ? "👍 like" : v === "disliked" ? "👎 dislike" : v === "saved" ? "★ save" : "🔇 mute"}
        </button>
      ))}
    </div>
  );
}

export function BroadCard({ c, onFeedback }: { c: any; onFeedback: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card">
      <div className="row">
        <b style={{ fontSize: 13.5 }}>#{c.rank} {c.title}</b>
        <span className="pill">{c.citation_tag.badge}{c.citation_tag.delta != null ? ` +${c.citation_tag.delta}/30d` : " no data"}</span>
        <span className="pill">score {c.composite}</span>
      </div>
      <div className="mut">
        {(c.authors || []).slice(0, 5).join(", ")}{(c.authors || []).length > 5 ? " et al." : ""} · {fmtDate(c.announce_date)} ·{" "}
        {(c.sources || []).join(", ")} · {c.link ? <a href={c.link} target="_blank" rel="noreferrer">link</a> : "no link"}
        {c.abstract_missing ? " · ⚠ title-only (no abstract)" : ""}
      </div>
      <div className="why">🔎 {c.why}</div>
      <div className="mut">
        sim {c.signals.embedding_sim ?? "—"} · lexical {c.signals.lexical} · co-cite {c.signals.cocitation_velocity} ·
        concepts {c.signals.concept_overlap} · HF {c.signals.hf_upvotes}
      </div>
      {c.abstract && (
        <>
          <button style={{ marginTop: 6 }} onClick={() => setOpen((v) => !v)}>
            {open ? "hide abstract" : "abstract"}
          </button>
          {open && <div className="abx">{c.abstract}</div>}
        </>
      )}
      <FeedbackButtons paperId={c.paper_id} onDone={onFeedback} />
    </div>
  );
}

export function CrossDomainCard({ c, onFeedback }: { c: any; onFeedback: () => void }) {
  const [open, setOpen] = useState(false);
  // The "field name" — a mechanism term this paper carries (e.g. "latent steering"), or,
  // failing that, the open-problem it's closest to. Shown first, big: this is the thing
  // Rishu wants to catch by name, the way "jepa" would have shown up here 5 months early.
  const fieldTag = (c.matched && c.matched[0]) || c.anchor_label || null;
  return (
    <div className="card" style={{ borderColor: "var(--ok)" }}>
      {fieldTag && (
        <div className="pill" style={{ borderColor: "var(--ok)", color: "var(--ok)", display: "inline-block", marginBottom: 6, fontWeight: 600 }}>
          🏷 {fieldTag}
        </div>
      )}
      <div className="row">
        <b style={{ fontSize: 13.5 }}>#{c.rank} {c.title}</b>
        <span className="pill" style={{ borderColor: "var(--ok)", color: "var(--ok)" }}>
          sim {c.signals.embedding_sim}
        </span>
        <span className="pill">{c.citation_tag.badge}{c.citation_tag.delta != null ? ` +${c.citation_tag.delta}/30d` : " no data"}</span>
      </div>
      <div className="mut">
        {(c.authors || []).slice(0, 5).join(", ")}{(c.authors || []).length > 5 ? " et al." : ""} · {fmtDate(c.announce_date)} ·{" "}
        {(c.sources || []).join(", ")} · {c.link ? <a href={c.link} target="_blank" rel="noreferrer">link</a> : "no link"}
        {c.abstract_missing ? " · ⚠ title-only (no abstract)" : ""}
      </div>
      <div className="why">🌍 {c.why}</div>
      {c.abstract && (
        <>
          <button style={{ marginTop: 6 }} onClick={() => setOpen((v) => !v)}>
            {open ? "hide abstract" : "abstract"}
          </button>
          {open && <div className="abx">{c.abstract}</div>}
        </>
      )}
      <FeedbackButtons paperId={c.paper_id} onDone={onFeedback} />
    </div>
  );
}

export function NicheCard({ c, onFeedback }: { c: any; onFeedback: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card">
      <div className="row">
        <b style={{ fontSize: 13.5 }}>{c.title}</b>
        {c.new_since_last_digest && <span className="pill" style={{ color: "var(--ok)", borderColor: "var(--ok)" }}>new</span>}
      </div>
      <div className="mut">
        {(c.authors || []).slice(0, 5).join(", ")} · {fmtDate(c.announce_date)} ·{" "}
        {c.link ? <a href={c.link} target="_blank" rel="noreferrer">link</a> : "no link"} ·
        matches {(c.matched || []).map((m: string) => `"${m}"`).join(", ")}
        {c.abstract_missing ? " · ⚠ title-only" : ""}
      </div>
      {c.abstract && (
        <>
          <button style={{ marginTop: 6 }} onClick={() => setOpen((v) => !v)}>
            {open ? "hide abstract" : "abstract"}
          </button>
          {open && <div className="abx">{c.abstract}</div>}
        </>
      )}
      <FeedbackButtons paperId={c.paper_id} onDone={onFeedback} />
    </div>
  );
}

export function ClusterRollup({ clusters }: { clusters: any[] }) {
  if (!clusters?.length) return <p className="mut">No cluster rollup yet (needs embeddings + a month of broad-track papers).</p>;
  return (
    <div className="card">
      {clusters.map((cl) => (
        <div key={cl.cluster_key} style={{ margin: "8px 0" }}>
          <div className="row">
            <b style={{ fontSize: 13 }}>{(cl.label_terms || []).slice(0, 5).join(" · ")}</b>
            <span className="pill">
              {cl.size} papers{cl.prev_size != null ? ` (was ${cl.prev_size})` : ""}{cl.is_new ? " · new" : ""}
            </span>
          </div>
          <div className="mut">reps: {(cl.representative_paper_ids || []).join(", ")}</div>
        </div>
      ))}
    </div>
  );
}

export function VocabPanel({ vocab }: { vocab: any[] }) {
  if (!vocab?.length) return null;
  return (
    <div className="card">
      <div className="row">
        {vocab.map((v) => (
          <span key={v.term} className={`pill ${v.is_seed ? "seed" : ""}`} title={`weight ${v.weight?.toFixed?.(3)} · last seen ${v.last_seen_iso_week ?? "never"}`}>
            {v.term} · {Number(v.weight).toFixed(2)}
          </span>
        ))}
      </div>
      <div className="mut" style={{ marginTop: 6 }}>
        <span className="pill seed">purple</span> = seed (permanent, still decays) · plain = learned (decays + prunes)
      </div>
    </div>
  );
}

export function WorkingPanel({ working }: { working: any[] }) {
  return (
    <div className="card">
      {!working?.length ? (
        <p className="mut">No rated broad-track cards yet. precision@10 appears once you 👍/👎 a few digests.</p>
      ) : (
        <table>
          <thead>
            <tr><th>week</th><th>shown</th><th>liked</th><th>disliked</th><th>ignored</th><th>precision@10</th></tr>
          </thead>
          <tbody>
            {working.map((w) => (
              <tr key={w.week}>
                <td>{w.week}</td><td>{w.n}</td><td>{w.liked}</td><td>{w.disliked}</td><td>{w.ignored}</td>
                <td>{w.precision == null ? "—" : (w.precision * 100).toFixed(0) + "%"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function RunLogViewer({ runs }: { runs: any[] }) {
  if (!runs?.length) return <p className="mut">No runs yet.</p>;
  return (
    <div className="card">
      <table>
        <thead><tr><th>#</th><th>date</th><th>kind</th><th>status</th><th>finished</th><th>key stats</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td><td>{fmtDate(r.run_date)}</td><td>{r.kind}</td>
              <td style={{ color: r.status === "ok" ? "var(--ok)" : r.status === "error" ? "var(--hot)" : "var(--muted)" }}>{r.status}</td>
              <td className="mut">{r.finished_at ? new Date(r.finished_at).toLocaleTimeString() : "—"}</td>
              <td className="mut">
                {["papers_upserted", "terms_tracked", "arxiv_niche", "arxiv_broad"]
                  .filter((k) => r.stats?.[k] != null)
                  .map((k) => `${k}=${JSON.stringify(r.stats[k])}`)
                  .join("  ")}
                {r.errors?.length ? `  · ${r.errors.length} error(s)` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
