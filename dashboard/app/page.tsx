"use client";
import { useCallback, useEffect, useState } from "react";
import {
  BroadCard, BurstList, ClusterRollup, CrossDomainCard, DiscoveryPanel, NicheCard,
  RisingTerms, RunLogViewer, StalenessBanner, VocabPanel, WorkingPanel,
} from "@/components/panels";
import { ConfigPanel } from "@/components/ConfigPanel";
import { SearchPanel } from "@/components/SearchPanel";

export default function Page() {
  const [state, setState] = useState<any>(null);
  const [err, setErr] = useState<string>("");

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/state", { cache: "no-store" });
      if (!res.ok) throw new Error(`state ${res.status}`);
      setState(await res.json());
      setErr("");
    } catch (e: any) {
      setErr(e?.message ?? "load failed");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (err) return <div className="wrap"><h1>📡 Paper Radar</h1><div className="banner" style={{ marginTop: 16 }}>Could not load: {err}</div></div>;
  if (!state) return <div className="wrap"><h1>📡 Paper Radar</h1><p className="mut" style={{ marginTop: 16 }}>loading…</p></div>;

  const d = state.digest;
  const broad = d?.broad_ranked ?? [];
  const crossDomain = d?.cross_domain ?? [];
  const niche = d?.niche_papers ?? [];
  const discovery = state.discovery ?? [];

  return (
    <div className="wrap">
      <div className="masthead">
        <h1><span className={`logo-dot ${state.staleness?.stale ? "stale" : ""}`} /> Paper Radar</h1>
        <div className="masthead-actions">
          {d ? <span>digest <b style={{ color: "var(--ink)" }}>{String(d.run_date).slice(0, 10)}</b></span> : "no digest yet"}
          <a onClick={load} style={{ cursor: "pointer" }}>↻ refresh</a>
        </div>
      </div>
      <p className="sub">
        {d ? <>{d.mode} · scanned {d.papers_scanned} papers</> : null}
        {" · "}fetched {new Date(state.generated_at).toLocaleTimeString()}
      </p>

      <div style={{ marginTop: 14 }}>
        <StalenessBanner s={state.staleness} />
      </div>

      <h2>🔍 Search my domain</h2>
      <SearchPanel />

      <h2>🌱 New field popping up <span className="mut" style={{ fontFamily: "var(--sans)", fontWeight: 500, fontSize: 14 }}>({discovery.length + crossDomain.length})</span></h2>
      <p className="lede">
        Not your field — the "JEPA was popping 5 months ago with only 2-3 papers, before any
        hallucination paper touched it" catch. Term names first, papers carrying them as evidence
        underneath. Read this before the field-of-yours section below — this is the one that pays off.
      </p>
      <h3>By name — terms rising outside your tracked vocab</h3>
      <DiscoveryPanel discovery={discovery} />
      <h3>By paper — structurally close to an open problem, not lexically in your field</h3>
      {crossDomain.length === 0 && (
        <p className="mut">Nothing cleared the cross-domain bar this week — quiet is a valid output.</p>
      )}
      {crossDomain.map((c: any) => <CrossDomainCard key={c.paper_id} c={c} onFeedback={load} />)}

      <h2>📍 Papers in your field <span className="mut" style={{ fontFamily: "var(--sans)", fontWeight: 500, fontSize: 14 }}>({broad.length}{d?.broad_ranked_truncated_at ? ` · truncated at ${d.broad_ranked_truncated_at}` : ""})</span></h2>
      <p className="lede">
        Hallucination detection / mitigation, safety, security — squarely your domain. You'd likely
        surface these yourself; kept here for completeness and momentum tracking, not because
        they're the point of this system.
      </p>
      {broad.length === 0 && <p className="mut">Quiet week — nothing cleared the recall gate. That is a valid output.</p>}
      {broad.map((c: any) => <BroadCard key={c.paper_id} c={c} onFeedback={load} />)}

      <h2>Niche track <span className="mut" style={{ fontFamily: "var(--sans)", fontWeight: 500, fontSize: 14 }}>({niche.length} · {niche.filter((n: any) => n.new_since_last_digest).length} new)</span></h2>
      {niche.length === 0 && <p className="mut">Nothing new in your exact area.</p>}
      {niche.map((c: any) => <NicheCard key={c.paper_id} c={c} onFeedback={load} />)}

      <details className="section">
        <summary>Emergent clusters <span className="count">this month</span></summary>
        <ClusterRollup clusters={d?.clusters_summary ?? []} />
      </details>

      <details className="section">
        <summary>Vocabulary <span className="count">{state.vocab.length}</span></summary>
        <VocabPanel vocab={state.vocab} />
      </details>

      <details className="section">
        <summary>Is it working <span className="count">precision@10</span></summary>
        <WorkingPanel working={state.working} />
      </details>

      {state.suggestions?.length > 0 && (
        <details className="section" open>
          <summary>Config suggestions <span className="count">{state.suggestions.length}</span></summary>
          <div className="card">
            {state.suggestions.map((s: any) => (
              <div key={s.id} className="mut" style={{ padding: "2px 0" }}>
                <b style={{ color: "var(--ink)" }}>{s.payload?.term}</b> — {s.rationale}
              </div>
            ))}
            <div className="mut" style={{ marginTop: 6 }}>Accept by adding the term in the config panel below.</div>
          </div>
        </details>
      )}

      <details className="section">
        <summary>Config <span className="count">editable</span></summary>
        <ConfigPanel config={state.config} onSaved={load} />
      </details>

      <details className="section">
        <summary>Run log</summary>
        <RunLogViewer runs={state.runs} />
      </details>
    </div>
  );
}
