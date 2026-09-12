// Turns a plain-language description ("ECG signal hallucination detection")
// into a real arXiv boolean query, and into the significant-word set used to
// re-match the corpus live on every dashboard load. Mirrors, by hand, what
// Claude built manually for Rishu's first ECG/DGT search — this is that same
// move, made self-service.

const STOPWORDS = new Set([
  "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "are",
  "with", "using", "based", "via", "new", "novel", "approach", "method",
  "detection", "model", "models", "signal", "signals", "system", "systems",
]);

export function significantWords(phrase: string): string[] {
  const words = phrase
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, " ")
    .split(/\s+/)
    .filter((w) => w.length >= 3 && !STOPWORDS.has(w));
  // de-dup, keep order, cap at 5 (an overly long AND chain both narrows
  // recall pointlessly and produces an unwieldy arXiv query)
  return [...new Set(words)].slice(0, 5);
}

export function buildArxivQuery(phrase: string): string {
  const trimmed = phrase.trim();
  const words = significantWords(trimmed);
  const exact = `abs:"${trimmed.replace(/"/g, "")}"`;
  if (words.length < 2) return exact;
  const anded = words.map((w) => `abs:${w}`).join(" AND ");
  return `${exact} OR (${anded})`;
}

export function labelFor(phrase: string): string {
  return phrase.trim().slice(0, 60);
}

// word-boundary-ish regex for a single word/phrase, for Postgres `~*`
export function wordBoundaryPattern(term: string): string {
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return `\\y${escaped}\\y`;
}
