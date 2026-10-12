// Turns a backend identifier into readable words: underscores to spaces,
// sentence case. "requirement_extraction" -> "Requirement extraction".
//
// chosen, not in DESIGN: DESIGN.md's label map covers statuses, not task
// types, and the processing screen showed task_type raw. This is a generic
// fallback for identifiers with no hand-written label - it does not invent
// words, it only re-cases the backend's own. Empty input stays empty.

export function humanize(identifier: string): string {
  const words = identifier.replace(/_+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
