"use client";
import { useCallback, useEffect, useState } from "react";
import {
  BroadCard, BurstList, ClusterRollup, CrossDomainCard, DiscoveryPanel, NicheCard,
  RisingTerms, RunLogViewer, StalenessBanner, VocabPanel, WorkingPanel,
} from "@/components/panels";
import { ConfigPanel } from "@/components/ConfigPanel";

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

  if (err) return <div className="wrap"><h1>Paper Radar</h1><div className="banner">Could not load: {err}</div></div>;
  if (!state) return <div className="wrap"><h1>Paper Radar</h1><p className="mut">loading…</p></div>;

  const d = state.digest;
  const broad = d?.broad_ranked ?? [];
  const crossDomain = d?.cross_domain ?? [];
  const niche = d?.niche_papers ?? [];

  return (
    <div className="wrap">
      <h1>Paper Radar</h1>
      <p className="sub">
        {d ? <>digest <b>{String(d.run_date).slice(0, 10)}</b> ({d.mode}) · scanned {d.papers_scanned} papers</> : "no digest yet"}
        {" · "}state fetched {new Date(state.generated_at).toLocaleTimeString()}
        {" · "}<a onClick={load} style={{ cursor: "pointer" }}>refresh</a>
      </p>

      <div style={{ marginTop: 12 }}>
        <StalenessBanner s={state.staleness} />
      </div>

      <h2>Rising terms {d ? `(${d.rising_terms?.length ?? 0})` : ""}</h2>
      <RisingTerms terms={d?.rising_terms ?? []} />
      <BurstList bursts={d?.bursts ?? []} />

      <h2>🌱 New field popping up ({(state.discovery ?? []).length + crossDomain.length})</h2>
      <p className="mut" style={{ marginTop: -6 }}>
        NOT your field — this is the "jepa was popping 5 months ago with only 2-3 papers, before
        any hallucination paper touched it" catch. Term names first (below), papers carrying them
        as evidence underneath. Read this before the field-of-yours section: this is the one that
        pays off, the other one you'd have found anyway.
      </p>
      <h3 style={{ marginBottom: 6, fontSize: 13.5 }}>By name — terms rising outside your tracked vocab</h3>
      <DiscoveryPanel discovery={state.discovery ?? []} />
      <h3 style={{ margin: "14px 0 6px", fontSize: 13.5 }}>By paper — structurally close to an open problem, not lexically in your field</h3>
      {crossDomain.length === 0 && (
        <p className="mut">Nothing cleared the cross-domain bar this week — quiet is a valid output.</p>
      )}
      {crossDomain.map((c: any) => <CrossDomainCard key={c.paper_id} c={c} onFeedback={load} />)}

      <h2>📍 Papers in your field ({broad.length}){d?.broad_ranked_truncated_at ? ` · truncated at ${d.broad_ranked_truncated_at}` : ""}</h2>
      <p className="mut" style={{ marginTop: -6 }}>
        Hallucination detection / mitigation, safety, security — squarely your domain. You'd
        likely surface these yourself; kept here for completeness and momentum tracking, not
        because they're the point of this system.
      </p>
      {broad.length === 0 && <p className="mut">Quiet week — nothing cleared the recall gate. That is a valid output.</p>}
      {broad.map((c: any) => <BroadCard key={c.paper_id} c={c} onFeedback={load} />)}

      <h2>Niche track ({niche.length} · {niche.filter((n: any) => n.new_since_last_digest).length} new)</h2>
      {niche.length === 0 && <p className="mut">Nothing new in your exact area.</p>}
      {niche.map((c: any) => <NicheCard key={c.paper_id} c={c} onFeedback={load} />)}

      <h2>Emergent clusters (this month)</h2>
      <ClusterRollup clusters={d?.clusters_summary ?? []} />

      <h2>Vocabulary ({state.vocab.length})</h2>
      <VocabPanel vocab={state.vocab} />

      <h2>Is it working — precision@10</h2>
      <WorkingPanel working={state.working} />

      {state.suggestions?.length > 0 && (
        <>
          <h2>Config suggestions ({state.suggestions.length})</h2>
          <div className="card">
            {state.suggestions.map((s: any) => (
              <div key={s.id} className="mut" style={{ padding: "2px 0" }}>
                <b>{s.payload?.term}</b> — {s.rationale}
              </div>
            ))}
            <div className="mut" style={{ marginTop: 6 }}>Accept by adding the term in the config panel below.</div>
          </div>
        </>
      )}

      <h2>Config (editable)</h2>
      <ConfigPanel config={state.config} onSaved={load} />

      <h2>Run log</h2>
      <RunLogViewer runs={state.runs} />
    </div>
  );
}
