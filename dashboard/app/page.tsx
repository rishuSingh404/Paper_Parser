"use client";
import { useCallback, useEffect, useState } from "react";
import {
  BroadCard, BurstList, ClusterRollup, CrossDomainCard, DiscoveryPanel, groupCrossDomain, NicheCard,
  RisingTerms, RunLogViewer, StalenessBanner, VocabPanel, WorkingPanel,
} from "@/components/panels";
import { ConfigPanel } from "@/components/ConfigPanel";
import { SearchPanel } from "@/components/SearchPanel";

type TabId = "emerging" | "field" | "niche" | "search" | "system";

export default function Page() {
  const [state, setState] = useState<any>(null);
  const [err, setErr] = useState<string>("");
  const [tab, setTab] = useState<TabId>("emerging");

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

  if (err) return <div className="wrap page-body"><h1>Paper Radar</h1><div className="banner" style={{ marginTop: 16 }}>Could not load: {err}</div></div>;
  if (!state) return <div className="wrap page-body"><h1>Paper Radar</h1><p className="mut" style={{ marginTop: 16 }}>loading…</p></div>;

  const d = state.digest;
  const broad = d?.broad_ranked ?? [];
  const crossDomain = d?.cross_domain ?? [];
  const niche = d?.niche_papers ?? [];
  const discovery = state.discovery ?? [];

  const tabs: { id: TabId; label: string; count: number | null }[] = [
    { id: "emerging", label: "Emerging", count: discovery.length + crossDomain.length },
    { id: "field", label: "Your field", count: broad.length },
    { id: "niche", label: "Niche", count: niche.length },
    { id: "search", label: "Search", count: null },
    { id: "system", label: "System", count: null },
  ];

  return (
    <>
      <header className="site-header">
        <div className="wrap">
          <div className="topbar">
            <div className="brand">
              <span className={`logo-dot ${state.staleness?.stale ? "stale" : ""}`} />
              <h1>Paper Radar</h1>
            </div>
            <nav className="tabs">
              {tabs.map((t) => (
                <button key={t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
                  {t.label}
                  {t.count != null && <span className="tab-count">{t.count}</span>}
                </button>
              ))}
            </nav>
          </div>
        </div>
      </header>

      <div className="wrap page-body">
        <div className="meta-bar">
          <span>
            {d ? <>digest {String(d.run_date).slice(0, 10)} · {d.mode} · {d.papers_scanned} papers scanned</> : "no digest yet"}
            {" · updated "}{new Date(state.generated_at).toLocaleTimeString()}
          </span>
          <a onClick={load}>Refresh</a>
        </div>

        <div style={{ marginTop: 10 }}>
          <StalenessBanner s={state.staleness} />
        </div>

        {tab === "emerging" && (
        <div className="tab-panel">
          <h3>Rising terms — outside your tracked vocabulary</h3>
          <DiscoveryPanel discovery={discovery} />

          <h3>Structurally close papers — not lexically in your field</h3>
          {crossDomain.length === 0 && (
            <p className="mut">Nothing cleared the cross-domain bar this week.</p>
          )}
          {groupCrossDomain(crossDomain).map((g) => (
            <div key={g.tag} style={{ marginTop: 14 }}>
              {g.tag !== "other" && (
                <div className="pill ok tag" style={{ marginBottom: 8 }}>{g.tag} <span className="mut">({g.cards.length})</span></div>
              )}
              {g.cards.map((c: any) => <CrossDomainCard key={c.paper_id} c={c} onFeedback={load} showTag={g.tag === "other"} />)}
            </div>
          ))}
        </div>
      )}

      {tab === "field" && (
        <div className="tab-panel">
          <p className="tab-intro">
            {broad.length} paper{broad.length === 1 ? "" : "s"}, ranked by composite signal
            {d?.broad_ranked_truncated_at ? ` · truncated at ${d.broad_ranked_truncated_at}` : ""}.
          </p>
          {broad.length === 0 && <p className="mut">Nothing cleared the recall gate this week.</p>}
          {broad.map((c: any) => <BroadCard key={c.paper_id} c={c} onFeedback={load} />)}
        </div>
      )}

      {tab === "niche" && (
        <div className="tab-panel">
          <p className="tab-intro">
            {niche.length} paper{niche.length === 1 ? "" : "s"} · {niche.filter((n: any) => n.new_since_last_digest).length} new since last digest.
          </p>
          {niche.length === 0 && <p className="mut">Nothing new in your exact area.</p>}
          {niche.map((c: any) => <NicheCard key={c.paper_id} c={c} onFeedback={load} />)}
        </div>
      )}

      {tab === "search" && (
        <div className="tab-panel">
          <SearchPanel />
        </div>
      )}

      {tab === "system" && (
        <div className="tab-panel">
          <details className="section" open>
            <summary>Clusters <span className="count">this month</span></summary>
            <ClusterRollup clusters={d?.clusters_summary ?? []} />
          </details>

          <details className="section">
            <summary>Vocabulary <span className="count">{state.vocab.length}</span></summary>
            <VocabPanel vocab={state.vocab} />
          </details>

          <details className="section">
            <summary>Performance <span className="count">precision@10</span></summary>
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
                <div className="mut" style={{ marginTop: 6 }}>Accept by adding the term in Config below.</div>
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
      )}
      </div>
    </>
  );
}
