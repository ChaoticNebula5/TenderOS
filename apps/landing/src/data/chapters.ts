/**
 * The nine chapters, in scroll order. Shared between the DOM layout (each
 * chapter is a <section>) and the world's scroll-progress tracking, so the
 * list exists exactly once rather than being re-typed in both places.
 * Ported from the `data-side`/`data-form` attributes on each
 * `<section class="ch">` in reference-landing.html:430-543.
 *
 * Only "sphere" and "pack" have a real formation in `world/formations.ts` -
 * P1's scope. The rest are named here because the DOM structure, header nav
 * and scroll-progress tracking are P0/P1 groundwork that all nine chapters
 * need to exist for; `world/formations.ts`'s `resolveFormation` holds the
 * world on "pack" for any chapter beyond it until P2 onward implement them.
 */

export type FormationName =
  | "sphere"
  | "pack"
  | "helix"
  | "retrieve"
  | "draft"
  | "redteam"
  | "recede"
  | "crossdoc"
  | "bid";

export interface ChapterDef {
  id: string;
  side: "left" | "right" | "center";
  form: FormationName;
  wide?: boolean;
}

// SCOPE NOTE (deferred, not P1/P2 - raised in review, not yet actioned):
// every chapter here sits on "left" - alternate left/right per chapter
// (01 left, 02 right, 03 left, ...) so the page doesn't read as monotonous.
// Do this when building each chapter's real content in P2-P5, not as a
// standalone pass, since `side` also drives world/cameraFraming.ts's
// measureRegion - changing it changes where the world frames itself, not
// just which side the text column renders on.
export const CHAPTERS: ChapterDef[] = [
  { id: "hero", side: "left", form: "sphere" },
  { id: "pack", side: "left", form: "pack" },
  { id: "read", side: "left", form: "helix" },
  { id: "comply", side: "left", form: "retrieve" },
  { id: "draft", side: "left", form: "draft" },
  { id: "red", side: "left", form: "redteam" },
  { id: "impact", side: "center", form: "recede", wide: true },
  { id: "price", side: "left", form: "crossdoc" },
  { id: "close", side: "left", form: "bid" },
];
