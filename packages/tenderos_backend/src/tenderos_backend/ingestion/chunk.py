import re
from dataclasses import dataclass, field

from tenderos_backend.core.hashing import sha256_hex
from tenderos_backend.ingestion.embed import count_tokens

# all-MiniLM-L6-v2's max_seq_length is 256 word-pieces (verified against the
# loaded model, not assumed) - it silently TRUNCATES beyond that, so any text
# past the limit contributes nothing to its own chunk's embedding and is
# effectively invisible to B6's vector retrieval. This is the real
# constraint, enforced with the actual tokenizer in _split_to_token_limit.
MAX_CHUNK_TOKENS = 256

# Character budget used for the cheap first pass. Deliberately calibrated to
# the WORST realistic density, not the prose average: measured on real tender
# content, plain prose runs ~4.3-6.1 chars/token, but dense passages (clause
# codes, part numbers, currency amounts, unspaced table cells - exactly what
# compliance matrices and pricing schedules are made of) measured as low as
# ~1.8-2.0 chars/token. At the prose average, 900 chars looked safely under
# the 256-token ceiling; at the dense ratio it is ~450-500 tokens, so the
# packer's own char-based pass would routinely hand the tokenizer pass
# oversized pieces to bisect - and a bisection split has no sentence
# boundary and no overlap, i.e. exactly the "requirement cut in half" case
# this whole module exists to avoid. 450 = 256 tokens * ~1.8 chars/token
# (the measured dense floor), so even dense content usually clears the token
# ceiling unaided and _split_to_token_limit stays a true last resort rather
# than the common path on real documents. Do not raise this back toward 900
# without re-measuring chars/token on dense fixtures first - see
# scripts/make_synthetic_fixtures.py's dense_compliance_matrix.pdf and
# tests/unit/test_chunking.py's density-based tests.
MAX_CHUNK_CHARS = 450

# Carried from the end of one chunk into the start of the next, within a
# provenance group. Roughly one or two sentences - enough that a requirement
# split across a boundary ("The bidder shall provide... / ...within 30 days")
# appears whole in at least one chunk, which is what B5's mandatory-recall
# gate ultimately measures. This is sized in absolute characters, not as a
# fraction of MAX_CHUNK_CHARS: what it has to protect (one to two sentences)
# doesn't shrink just because the chunk budget did, so it stays 150
# regardless of where MAX_CHUNK_CHARS lands. Larger overlap buys little and
# costs an embedding per duplicated span.
OVERLAP_CHARS = 150

# Split after ., ! or ? when followed by whitespace. Deliberately naive: a
# smarter splitter would need abbreviation/decimal handling, and a wrong
# split here only shifts a boundary, it never loses text.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class ExtractedBlock:
    """One natively-structured unit of extracted text, at whatever
    granularity the source format itself provides - one per PDF page, one
    per DOCX paragraph, one per XLSX sheet. Produced by parse_pdf.py/
    parse_docx.py/parse_xlsx.py; consumed by build_chunks() below.
    """

    text: str
    page_num: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None
    heading_path: list[str] = field(default_factory=list)


@dataclass
class ChunkDraft:
    """What build_chunks() produces - everything DocumentRepository.create_chunk
    needs except document_id/chunk_index, which the caller (jobs/ingestion.py)
    assigns when persisting, since that's a document-wide sequence, not
    something a single chunk knows about itself.
    """

    chunk_text: str
    normalized_text: str
    content_hash: str
    page_num: int | None
    sheet_name: str | None
    cell_range: str | None
    heading_path: list[str]
    token_count: int


def normalize_text(text: str) -> str:
    """Collapses all whitespace runs to single spaces and strips the ends.

    normalized_text feeds both the FTS index (to_tsvector already handles
    case-folding/stemming on its own) and content_hash - what it needs to be
    robust to is whitespace jitter across re-parses of the same source file
    (a line-wrap landing one character earlier, a trailing newline), not
    case. Without this, two parses of an unchanged PDF could hash
    differently and defeat "stable chunking".
    """
    return " ".join(text.split())


def content_hash_for(
    normalized_text: str,
    page_num: int | None = None,
    sheet_name: str | None = None,
    cell_range: str | None = None,
    heading_path: list[str] | None = None,
) -> str:
    """SHA-256 over normalized text AND its provenance (I2: "SHA-256 over
    normalized stable inputs", whose own examples are composite keys, not
    bare text).

    Provenance has to be in the hash because document_chunks has
    UNIQUE(document_id, content_hash): real tender documents repeat text
    verbatim (an "N/A" table row, boilerplate headers on every annexure
    page), so hashing text alone would make the same phrase on page 3 and
    page 7 collide and raise IntegrityError on insert. Including provenance
    keeps them distinct while still being stable across re-parses, since
    every component is derived from the source content, never from a
    timestamp or row id.
    """
    provenance = f"{page_num}|{sheet_name}|{cell_range}|{'>'.join(heading_path or [])}"
    return sha256_hex(f"{provenance}|{normalized_text}".encode())


def _draft_from_merged_text(
    raw_text: str,
    page_num: int | None,
    sheet_name: str | None,
    cell_range: str | None,
    heading_path: list[str],
) -> ChunkDraft:
    normalized = normalize_text(raw_text)
    return ChunkDraft(
        chunk_text=raw_text.strip(),
        normalized_text=normalized,
        content_hash=content_hash_for(normalized, page_num, sheet_name, cell_range, heading_path),
        page_num=page_num,
        sheet_name=sheet_name,
        cell_range=cell_range,
        heading_path=heading_path,
        token_count=count_tokens(normalized),
    )


def build_chunks(blocks: list[ExtractedBlock]) -> list[ChunkDraft]:
    """Turns parser output into the chunk sequence persisted to
    document_chunks. Deterministic: the same blocks always produce the same
    chunks in the same order ("stable chunking"), so chunk_index and
    content_hash survive a re-parse unchanged and re-ingestion never
    duplicates rows.

    The load-bearing rule is that chunks never span a provenance boundary
    (see _provenance_key). B4's own do-not-proceed list includes "chunks
    cannot return to source page/sheet", and B5 gates on source citation
    validity >= 0.95 - if one chunk merged page 3 and page 4 text but could
    only record a single page_num, every requirement extracted from the
    page-4 half would carry a wrong citation. Grouping first makes every
    chunk's provenance exactly right rather than approximately right; the
    cost is some chunks smaller than target at page boundaries, which is
    the cheaper error.
    """
    drafts: list[ChunkDraft] = []
    seen_hashes: set[str] = set()

    for group in _group_by_provenance(blocks):
        anchor = group[0]
        merged_text = "\n".join(block.text.strip() for block in group if block.text.strip())
        if not normalize_text(merged_text):
            continue

        packed = _pack_units(_atomic_units(merged_text))
        for piece in [p for packed_piece in packed for p in _split_to_token_limit(packed_piece)]:
            draft = _draft_from_merged_text(
                piece,
                anchor.page_num,
                anchor.sheet_name,
                anchor.cell_range,
                anchor.heading_path,
            )
            # Identical text under identical provenance is genuinely the
            # same chunk, and UNIQUE(document_id, content_hash) would reject
            # the second copy anyway - skip rather than let the insert fail.
            if draft.content_hash in seen_hashes:
                continue
            seen_hashes.add(draft.content_hash)
            drafts.append(draft)

    return drafts


def _provenance_key(block: ExtractedBlock) -> tuple:
    return (
        block.page_num,
        block.sheet_name,
        block.cell_range,
        tuple(block.heading_path),
    )


def _group_by_provenance(blocks: list[ExtractedBlock]) -> list[list[ExtractedBlock]]:
    """Consecutive runs of blocks sharing identical provenance. Consecutive,
    not global: two separate sections under the same heading stay separate
    groups, preserving document order.
    """
    groups: list[list[ExtractedBlock]] = []
    for block in blocks:
        if groups and _provenance_key(groups[-1][0]) == _provenance_key(block):
            groups[-1].append(block)
        else:
            groups.append([block])
    return groups


def _atomic_units(text: str) -> list[str]:
    """Smallest pieces a chunk boundary may fall between: lines first (table
    rows and list items are already meaningful units), then sentences within
    an over-long line. Anything still larger than the cap is hard-split, so
    the packer below never receives a unit it cannot place.
    """
    units: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(line) <= MAX_CHUNK_CHARS:
            units.append(line)
            continue
        for sentence in _SENTENCE_END.split(line):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= MAX_CHUNK_CHARS:
                units.append(sentence)
            else:
                units.extend(
                    sentence[i : i + MAX_CHUNK_CHARS]
                    for i in range(0, len(sentence), MAX_CHUNK_CHARS)
                )
    return units


def _pack_units(units: list[str]) -> list[str]:
    """Greedily fills chunks up to MAX_CHUNK_CHARS, carrying OVERLAP_CHARS of
    trailing context into the next chunk so a requirement sentence sitting on
    a boundary appears whole in at least one chunk. Overlap only ever happens
    here - inside one provenance group - so carried text can never end up
    attributed to a page or sheet it did not come from.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for unit in units:
        if current and current_len + len(unit) + 1 > MAX_CHUNK_CHARS:
            chunks.append("\n".join(current))
            current = _overlap_tail(current)
            current_len = sum(len(u) + 1 for u in current)
        current.append(unit)
        current_len += len(unit) + 1

    if current:
        chunks.append("\n".join(current))
    return chunks


def _split_to_token_limit(text: str) -> list[str]:
    """Authoritative enforcement of the model's 256-word-piece window, using
    the real tokenizer rather than the character proxy.

    _pack_units keeps chunks under MAX_CHUNK_CHARS, which is right for
    ordinary prose but not for dense content - an unspaced table row can be
    438 tokens at 900 characters, and everything past 256 would be silently
    dropped at embed time. Splitting at the nearest whitespace to the
    midpoint keeps the halves close to even and terminates because each
    recursion is strictly shorter; text with no split point left is returned
    as-is rather than looping.
    """
    if count_tokens(text) <= MAX_CHUNK_TOKENS:
        return [text]

    midpoint = len(text) // 2
    if midpoint == 0:
        return [text]

    split_at = text.rfind(" ", 0, midpoint)
    if split_at <= 0:
        split_at = midpoint

    left, right = text[:split_at].strip(), text[split_at:].strip()
    if not left or not right:
        return [text]

    return _split_to_token_limit(left) + _split_to_token_limit(right)


def _overlap_tail(units: list[str]) -> list[str]:
    """The trailing units totalling at most OVERLAP_CHARS. Never returns the
    whole list - carrying everything forward would mean the next chunk starts
    exactly where the last one did and the packer would not advance.
    """
    tail: list[str] = []
    total = 0
    for unit in reversed(units[1:]):
        if total + len(unit) > OVERLAP_CHARS:
            break
        tail.insert(0, unit)
        total += len(unit) + 1
    return tail
