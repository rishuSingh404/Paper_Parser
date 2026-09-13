"use client";
import { useEffect, useMemo, useState } from "react";

const CAT_SUGGESTIONS = ["cs.CL", "cs.LG", "cs.AI", "cs.CV", "cs.CR", "stat.ML", "eess.SP", "eess.IV", "cs.RO"];

export function ConfigPanel({ config, onSaved }: { config: any; onSaved: () => void }) {
  const [token, setToken] = useState("");
  const [history, setHistory] = useState<any[]>([]);
  const [vocab, setVocab] = useState<Record<string, number>>({});
  const [newTerm, setNewTerm] = useState("");
  const [queries, setQueries] = useState("");
  const [problems, setProblems] = useState("");
  const [cats, setCats] = useState<string[]>([]);
  const [seedPapers, setSeedPapers] = useState("");
  const [msg, setMsg] = useState<string>("");
  const [justAdded, setJustAdded] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    try {
      setToken(sessionStorage.getItem("pr_edit_token") ?? "");
    } catch {}
    fetch("/api/config", { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => setHistory(d.history ?? []))
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (!config) return;
    setVocab({ ...(config.seed_vocab ?? {}) });
    setQueries((config.niche_queries ?? []).join("\n"));
    setProblems((config.open_problems ?? []).join("\n"));
    setCats(config.broad_categories ?? []);
    setSeedPapers((config.seed_papers ?? []).join("\n"));
  }, [config]);

  const dirty = useMemo(() => JSON.stringify({ vocab, queries, problems, cats, seedPapers }), [vocab, queries, problems, cats, seedPapers]);

  const save = async () => {
    setSaving(true);
    setMsg("saving…");
    try {
      sessionStorage.setItem("pr_edit_token", token);
    } catch {}
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "content-type": "application/json", "x-edit-token": token },
      body: JSON.stringify({
        niche_queries: queries.split("\n").map((s) => s.trim()).filter(Boolean),
        broad_categories: cats,
        open_problems: problems.split("\n").map((s) => s.trim()).filter(Boolean),
        seed_papers: seedPapers.split("\n").map((s) => s.trim()).filter(Boolean),
        seed_vocab: vocab,
      }),
    });
    const data = await res.json().catch(() => ({}));
    setSaving(false);
    if (!res.ok) {
      setMsg(`✗ ${res.status}: ${data.error ?? "failed"}`);
      return;
    }
    setMsg(`✓ saved. ${data.enqueued ?? 0} backfill(s): ${(data.backfills ?? []).map((b: any) => `${b.term}→${b.status}`).join(", ")}`);
    setJustAdded(data.just_added ?? []);
    onSaved();
  };

  return (
    <div className="card">
      <div className="row">
        <input
          type="password"
          placeholder="edit token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          style={{ maxWidth: 220 }}
        />
        <span className="mut">kept in sessionStorage only</span>
      </div>

      <h3 style={{ fontSize: 13, margin: "14px 0 4px" }}>Seed keywords</h3>
      <div className="row">
        {Object.entries(vocab).map(([t, w]) => (
          <span key={t} className="chip">
            {t} · {w}
            <button onClick={() => setVocab((v) => { const n = { ...v }; delete n[t]; return n; })}>×</button>
          </span>
        ))}
      </div>
      <div className="row" style={{ marginTop: 6 }}>
        <input
          placeholder="add keyword, Enter"
          value={newTerm}
          onChange={(e) => setNewTerm(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && newTerm.trim()) {
              setVocab((v) => ({ ...v, [newTerm.trim().toLowerCase()]: 1.0 }));
              setNewTerm("");
            }
          }}
          style={{ maxWidth: 260 }}
        />
        <span className="mut">new terms default to weight 1.0</span>
      </div>

      <h3 style={{ fontSize: 13, margin: "14px 0 4px" }}>Niche queries (one per line, arXiv syntax)</h3>
      <textarea value={queries} onChange={(e) => setQueries(e.target.value)} />

      <h3 style={{ fontSize: 13, margin: "14px 0 4px" }}>Open problems (one per line — embedding anchors)</h3>
      <textarea value={problems} onChange={(e) => setProblems(e.target.value)} />

      <h3 style={{ fontSize: 13, margin: "14px 0 4px" }}>Broad categories</h3>
      <div className="row">
        {[...new Set([...CAT_SUGGESTIONS, ...cats])].map((c) => (
          <button
            key={c}
            className={cats.includes(c) ? "on" : ""}
            onClick={() => setCats((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]))}
          >
            {c}
          </button>
        ))}
      </div>

      <h3 style={{ fontSize: 13, margin: "14px 0 4px" }}>Seed papers (arXiv ids, one per line)</h3>
      <textarea value={seedPapers} onChange={(e) => setSeedPapers(e.target.value)} style={{ minHeight: 60 }} />

      <div className="row" style={{ marginTop: 14 }}>
        <button className="primary" disabled={saving || !token} onClick={save}>Save</button>
        <span className="mut">{msg}</span>
      </div>

      {justAdded.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <h3 style={{ fontSize: 13 }}>Just added — last 6 months ({justAdded.length})</h3>
          <div className="mut">bucketed by real publication week, so momentum for an established term stays flat</div>
          {justAdded.slice(0, 40).map((p) => (
            <div key={p.paper_id} style={{ fontSize: 12.5, padding: "2px 0" }}>
              {String(p.announce_date ?? "?").slice(0, 10)} · {p.link ? <a href={p.link} target="_blank" rel="noreferrer">{p.title}</a> : p.title}
            </div>
          ))}
        </div>
      )}

      {history?.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <h3 style={{ fontSize: 13 }}>Version history</h3>
          <div className="mut">
            {history.map((h) => `v${h.version} @ ${new Date(h.edited_at).toLocaleString()}`).join("  ·  ")}
          </div>
          <div className="mut">Restore: paste that snapshot back into these fields and Save (a real one-click restore lands with the history API).</div>
        </div>
      )}
    </div>
  );
}
