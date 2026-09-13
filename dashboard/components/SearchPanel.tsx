"use client";
import { useCallback, useEffect, useState } from "react";

const fmtDate = (d: string | null) => (d ? String(d).slice(0, 10) : "?");

export function SearchPanel() {
  const [searches, setSearches] = useState<any[]>([]);
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/searches", { cache: "no-store" });
      const data = await res.json();
      setSearches(data.searches ?? []);
    } catch {
      // quiet — this panel is additive, don't block the rest of the dashboard
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const add = async () => {
    if (!phrase.trim()) return;
    setBusy(true);
    setMsg("searching arXiv + your corpus…");
    try {
      const res = await fetch("/api/searches", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ phrase: phrase.trim() }),
      });
      const data = await res.json();
      if (!res.ok) {
        setMsg(`✗ ${data.error ?? "failed"}`);
      } else {
        setMsg(`✓ found ${data.total} paper(s) so far — historical backfill running in the background, check back in a few minutes for more`);
        setPhrase("");
        await load();
      }
    } catch (e: any) {
      setMsg(`✗ ${e?.message ?? "failed"}`);
    }
    setBusy(false);
  };

  const remove = async (id: number) => {
    await fetch(`/api/searches?id=${id}`, { method: "DELETE" });
    await load();
  };

  return (
    <div>
      <div className="card">
        <p className="lede" style={{ margin: "0 0 12px" }}>
          Type a plain description of a sub-area you're working in — not arXiv
          syntax, just words (e.g. "ECG signal hallucination detection"). This
          builds the search for you, pulls the last 12 months of matching
          papers from arXiv once, and keeps matching new ones from every daily
          run after that — no need to touch the Niche queries box below.
        </p>
        <div className="row">
          <input
            placeholder="e.g. ECG signal hallucination detection"
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
            style={{ minWidth: 280, flex: 1 }}
          />
          <button className="primary" disabled={busy || !phrase.trim()} onClick={add}>
            {busy ? "searching…" : "Add & search"}
          </button>
        </div>
        {msg && <div className="mut" style={{ marginTop: 8 }}>{msg}</div>}
      </div>

      {loaded && searches.length === 0 && (
        <p className="mut" style={{ marginTop: 8 }}>No saved searches yet — add one above.</p>
      )}

      {searches.map((s) => {
        const transfers = (s.matches ?? []).filter((m: any) => m.possible_transfer);
        const inField = (s.matches ?? []).filter((m: any) => !m.possible_transfer);
        return (
          <div key={s.id} className="card accent-ok">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <b style={{ fontSize: 14.5 }}>{s.label}</b>
              <div className="row" style={{ flexShrink: 0 }}>
                <span className="pill ok">{s.total} paper{s.total === 1 ? "" : "s"}</span>
                <button onClick={() => remove(s.id)} title="remove this saved search">✕</button>
              </div>
            </div>

            {transfers.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="mut" style={{ fontWeight: 700, color: "var(--ink)" }}>
                  🌍 {transfers.length} outside your broader domain — possible transfer into this
                </div>
                {transfers.slice(0, 20).map((m: any) => (
                  <div key={m.paper_id} style={{ fontSize: 13, padding: "5px 0", lineHeight: 1.5 }}>
                    <span className="faint">{fmtDate(m.announce_date)}</span> ·{" "}
                    {m.link ? <a href={m.link} target="_blank" rel="noreferrer">{m.title}</a> : m.title}
                    {m.embedding_sim != null && <span className="mut"> · sim {m.embedding_sim}</span>}
                    {!m.matched_locally && (
                      <span className="faint" title="Found via arXiv's own relevance search on the initial add, not a literal word match — doesn't use your exact search words but arXiv judged it relevant">
                        {" "}· via arXiv search
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {inField.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="mut">📍 {inField.length} already in your broader domain (hallucination/safety) — you'd find these anyway</div>
                {inField.slice(0, 10).map((m: any) => (
                  <div key={m.paper_id} style={{ fontSize: 13, padding: "5px 0", opacity: 0.7 }}>
                    <span className="faint">{fmtDate(m.announce_date)}</span> ·{" "}
                    {m.link ? <a href={m.link} target="_blank" rel="noreferrer">{m.title}</a> : m.title}
                  </div>
                ))}
              </div>
            )}

            {s.total === 0 && (
              <div className="mut" style={{ marginTop: 8 }}>
                Nothing yet — the historical backfill may still be running, or this sub-area
                genuinely has little on arXiv yet. Check back after the next daily run.
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
