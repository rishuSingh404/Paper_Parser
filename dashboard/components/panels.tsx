"use client";
import { useState } from "react";

const fmtDate = (d: string | null) => (d ? String(d).slice(0, 10) : "?");

export function StalenessBanner({ s }: { s: any }) {
  if (!s?.stale) return null;
  return (
    <div className="banner">
      ⚠ Latest digest is <b>{s.days_old} days</b> old ({fmtDate(s.last_run_date)}). The daily
      run may be failing — check the Run log below. Signals below are not current.
    </div>
  );
}

export function RisingTerms({ terms }: { terms: any[] }) {
  if (!terms?.length) return <p className="mut">No rising terms yet — needs ~6 weeks of history.</p>;
  const max = Math.max(1, ...terms.map((t) => Math.abs(t.momentum)));
  return (
    <div className="card">
      {terms.map((t) => (
        <div key={t.term} className="row" style={{ margin: "7px 0" }}>
          <span style={{ width: 190, fontSize: 12.5 }}>{t.term}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(Math.abs(t.momentum) / max) * 100}%` }} />
          </div>
          <span className="mut" style={{ width: 100, textAlign: "right" }}>
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
      <div className="row" style={{ marginTop: 8 }}>
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
    return <p className="mut">Nothing outside your tracked vocab qualified this week.</p>;
  }
  return (
    <div className="card">
      <div style={{ marginBottom: 14 }}>
        <b style={{ fontSize: 13.5 }}>First appearance</b>
        <div className="mut" style={{ margin: "3px 0 8px" }}>
          New this week, used by 2+ independent groups.
        </div>
        {firstAppearance.length === 0 ? (
          <span className="mut">none this week</span>
        ) : (
          <div className="row">
            {firstAppearance.map((d) => (
              <span key={d.term} className="pill ok tag"
                    title={`first seen this week, ${d.distinct_groups} independent groups`}>
                {d.term} · {d.current_count} groups
              </span>
            ))}
          </div>
        )}
      </div>
      <div>
        <b style={{ fontSize: 13.5 }}>Still bursting</b>
        <div className="mut" style={{ margin: "3px 0 8px" }}>
          Rising above its own recent baseline; already had some presence.
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
    </div>
  );
}

function FeedbackButtons({ paperId, onDone }: { paperId: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const send = async (verdict: string) => {
    setBusy(true);
    setFailed(false);
    try {
      const res = await fetch("/api/feedback", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ paper_id: paperId, verdict }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setPicked(verdict);
      onDone();
    } catch {
      setFailed(true);
    }
    setBusy(false);
  };
  const colorFor = (v: string) =>
    v === "liked" ? "var(--ok)" : v === "disliked" ? "var(--hot)" : v === "saved" ? "var(--warm)" : "var(--muted)";
  return (
    <div className="row" style={{ marginTop: 10, alignItems: "center" }}>
      {["liked", "disliked", "saved", "muted"].map((v) => (
        <button
          key={v}
          disabled={busy}
          onClick={() => send(v)}
          style={picked === v ? { borderColor: colorFor(v), color: colorFor(v), background: "var(--card-soft)" } : undefined}
        >
          {v === "liked" ? "Like" : v === "disliked" ? "Dislike" : v === "saved" ? "Save" : "Mute"}
        </button>
      ))}
      {picked && <span className="mut" style={{ color: colorFor(picked) }}>recorded</span>}
      {failed && <span className="mut" style={{ color: "var(--hot)" }}>failed — try again</span>}
    </div>
  );
}

function SignalRow({ c }: { c: any }) {
  const parts = [
    c.signals.embedding_sim != null ? `sim ${c.signals.embedding_sim}` : null,
    c.signals.lexical ? `lexical ${c.signals.lexical}` : null,
    c.signals.cocitation_velocity ? `co-cite ${c.signals.cocitation_velocity}` : null,
    c.signals.concept_overlap ? `concepts ${c.signals.concept_overlap}` : null,
    c.signals.hf_upvotes ? `HF ${c.signals.hf_upvotes}` : null,
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return (
    <div className="faint" style={{ marginTop: 6 }}>
      {parts.join(" · ")}
    </div>
  );
}

function AbstractToggle({ abstract }: { abstract: string | null }) {
  const [open, setOpen] = useState(false);
  if (!abstract) return null;
  return (
    <>
      <button style={{ marginTop: 8 }} onClick={() => setOpen((v) => !v)}>
        {open ? "hide abstract" : "abstract"}
      </button>
      {open && <div className="abx">{abstract}</div>}
    </>
  );
}

export function BroadCard({ c, onFeedback }: { c: any; onFeedback: () => void }) {
  return (
    <div className="card accent-field">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <b style={{ fontSize: 14.5, lineHeight: 1.4 }}>#{c.rank} {c.title}</b>
        <div className="row" style={{ flexShrink: 0 }}>
          {c.citation_tag.delta != null && <span className="pill">{c.citation_tag.badge} +{c.citation_tag.delta}/30d</span>}
          <span className="pill">score {c.composite}</span>
        </div>
      </div>
      <div className="mut" style={{ marginTop: 4 }}>
        {(c.authors || []).slice(0, 5).join(", ")}{(c.authors || []).length > 5 ? " et al." : ""} · {fmtDate(c.announce_date)} ·{" "}
        {(c.sources || []).join(", ")} · {c.link ? <a href={c.link} target="_blank" rel="noreferrer">link</a> : "no link"}
        {c.abstract_missing ? " · ⚠ title-only (no abstract)" : ""}
      </div>
      <div className="why">{c.why}</div>
      <SignalRow c={c} />
      <AbstractToggle abstract={c.abstract} />
      <FeedbackButtons paperId={c.paper_id} onDone={onFeedback} />
    </div>
  );
}

// The "field name" — a mechanism term this paper carries (e.g. "latent steering"), or,
// failing that, the open-problem it's closest to. This is the thing Rishu wants to catch
// by name, the way "jepa" would have shown up here 5 months early.
function fieldTagOf(c: any): string | null {
  return (c.matched && c.matched[0]) || c.anchor_label || null;
}

// One header per tag instead of repeating the same 🏷 pill on every card — a
// generic term (e.g. "decorrelation" hitting unrelated physics/archaeology
// papers) reads as noisy repetition otherwise, capped to 4/tag server-side
// but still visually loud without grouping. "other" bucket last.
export function groupCrossDomain(cards: any[]): { tag: string; cards: any[] }[] {
  const order: string[] = [];
  const groups = new Map<string, any[]>();
  for (const c of cards) {
    const tag = fieldTagOf(c) ?? "other";
    if (!groups.has(tag)) { groups.set(tag, []); order.push(tag); }
    groups.get(tag)!.push(c);
  }
  order.sort((a, b) => (a === "other" ? 1 : b === "other" ? -1 : 0));
  return order.map((tag) => ({ tag, cards: groups.get(tag)! }));
}

export function CrossDomainCard({ c, onFeedback, showTag = true }: { c: any; onFeedback: () => void; showTag?: boolean }) {
  const fieldTag = fieldTagOf(c);
  const sim = c.signals?.embedding_sim;
  return (
    <div className="card accent-ok">
      {showTag && fieldTag && <div className="pill ok tag" style={{ marginBottom: 8 }}>{fieldTag}</div>}
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <b style={{ fontSize: 14.5, lineHeight: 1.4 }}>{c.title}</b>
        <div className="row" style={{ flexShrink: 0 }}>
          {sim != null && <span className="pill ok">sim {sim}</span>}
          {c.citation_tag.delta != null && <span className="pill">{c.citation_tag.badge} +{c.citation_tag.delta}/30d</span>}
        </div>
      </div>
      <div className="mut" style={{ marginTop: 4 }}>
        {(c.authors || []).slice(0, 5).join(", ")}{(c.authors || []).length > 5 ? " et al." : ""} · {fmtDate(c.announce_date)} ·{" "}
        {(c.sources || []).join(", ")} · {c.link ? <a href={c.link} target="_blank" rel="noreferrer">link</a> : "no link"}
        {c.abstract_missing ? " · ⚠ title-only (no abstract)" : ""}
      </div>
      <div className="why">{c.why}</div>
      <AbstractToggle abstract={c.abstract} />
      <FeedbackButtons paperId={c.paper_id} onDone={onFeedback} />
    </div>
  );
}

export function NicheCard({ c, onFeedback }: { c: any; onFeedback: () => void }) {
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <b style={{ fontSize: 14.5, lineHeight: 1.4 }}>{c.title}</b>
        {c.new_since_last_digest && <span className="pill ok" style={{ flexShrink: 0 }}>new</span>}
      </div>
      <div className="mut" style={{ marginTop: 4 }}>
        {(c.authors || []).slice(0, 5).join(", ")} · {fmtDate(c.announce_date)} ·{" "}
        {c.link ? <a href={c.link} target="_blank" rel="noreferrer">link</a> : "no link"} ·{" "}
        matches {(c.matched || []).map((m: string) => `"${m}"`).join(", ")}
        {c.abstract_missing ? " · ⚠ title-only" : ""}
      </div>
      <AbstractToggle abstract={c.abstract} />
      <FeedbackButtons paperId={c.paper_id} onDone={onFeedback} />
    </div>
  );
}

export function ClusterRollup({ clusters }: { clusters: any[] }) {
  if (!clusters?.length) return <p className="mut">No cluster rollup yet (needs embeddings + a month of broad-track papers).</p>;
  return (
    <div className="card">
      {clusters.map((cl) => (
        <div key={cl.cluster_key} style={{ margin: "10px 0" }}>
          <div className="row">
            <b style={{ fontSize: 13.5 }}>{(cl.label_terms || []).slice(0, 5).join(" · ")}</b>
            <span className="pill">
              {cl.size} papers{cl.prev_size != null ? ` (was ${cl.prev_size})` : ""}{cl.is_new ? " · new" : ""}
            </span>
          </div>
          <div className="mut" style={{ marginTop: 2 }}>reps: {(cl.representative_paper_ids || []).join(", ")}</div>
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
      <div className="mut" style={{ marginTop: 10 }}>
        <span className="pill seed">purple</span> = seed (permanent, still decays) · plain = learned (decays + prunes)
      </div>
    </div>
  );
}

export function WorkingPanel({ working }: { working: any[] }) {
  return (
    <div className="card">
      {!working?.length ? (
        <p className="mut">No rated broad-track cards yet. precision@10 appears once you rate a few digests.</p>
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
              <td style={{ color: r.status === "ok" ? "var(--ok)" : r.status === "error" ? "var(--hot)" : "var(--muted)", fontWeight: 600 }}>{r.status}</td>
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
