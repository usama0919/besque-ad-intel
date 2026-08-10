"""Parse docs/angle_language.md and load it into the angle_language table via
dedupe.upsert_angle_language() - one call per angle, for the six existing slugs.

Only content under the "## Angles" heading is parsed. The "## Claude Reviews
Language for Image Gen" preamble (including its Step 3 fictional-stat and Step 4
mechanism-as-subtext sections) and the override note above it are never read by
this script at all - not filtered out, simply out of the parsed range - so
docs/angle_language.md's own override note ("The loader ignores both sections")
holds structurally, not just by convention.

best_verbatims is not parsed in this pass - every row gets [] regardless of the
doc's "The best verbatims" section content.

--dry-run parses and prints diagnostics only - it never opens a DB connection,
so it cannot write, or even read, anything in prod.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "angle_language.md"

ANGLES_HEADING = "## Angles"

ANGLE_SLUGS = {
    "Loose Skin": "loose_skin",
    "GLP-1": "glp1",
    "Crepey Skin": "crepey_skin",
    "Bruising": "bruising",
    "Sun Damage": "sun_damage",
    "Menopause": "menopause",
}

# Matched by startswith against the #### heading text, so headings with an
# em-dash suffix ("What causes it — the mechanism") still match their label.
FIELD_LABELS = [
    ("core_angle", "The core angle"),
    ("causes", "What causes it"),
    ("main_pain_point", "Main pain point"),
    ("main_benefit", "Main benefit"),
    ("common_phrases", "Most common phrases customers use"),
    ("result_phrases", "Result phrases customers use"),
    ("best_verbatims", "The best verbatims"),
    ("image_direction", "How customers describe their skin problem"),
]

# Only ever applied to common_phrases / result_phrases / image_direction (never
# core_angle / causes / main_pain_point / main_benefit) - those three are the
# only fields with an optional throwaway lead-in sentence before the real
# content. main_benefit in particular has a genuine mid-paragraph ':' ("the
# real proof: women buying shorts...") that must never be touched, which is
# exactly why this isn't called on every field.
_LEADIN_FIELDS = {"common_phrases", "result_phrases", "image_direction"}


def strip_leadin(text):
    """Remove a leading sentence ending in ':' (e.g. 'These are the specific
    visual descriptions customers use... Use these to brief image gen:') from
    raw section text, before any further processing. No-op if there's no ':'
    at all. Cuts at the FIRST ':' only, so any ':' further into real content
    (none exist in today's doc) is left untouched."""
    if ":" not in text:
        return text
    _, _, rest = text.partition(":")
    return rest.strip()


def _field_key(heading_text):
    for key, label in FIELD_LABELS:
        if heading_text.startswith(label):
            return key
    return None


def _join_paragraph(lines):
    """Word-wrapped markdown lines -> one flat paragraph, single-spaced."""
    text = " ".join(line.strip() for line in lines if line.strip())
    return re.sub(r"\s+", " ", text).strip()


def _split_phrases(lines):
    """'a · b · c' (possibly with a leading non-phrase preface sentence, already
    removed by strip_leadin before this runs) -> ["a", "b", "c"]."""
    joined = strip_leadin(_join_paragraph(lines))
    parts = [p.strip() for p in joined.split("·")]
    return [p for p in parts if p]


def find_angles_anchor(lines):
    """Locate the exact line that scopes parsing to the Angles section. Returns
    (0-based index, line text). Raises if it's missing, so a heading rename in
    the doc fails loudly instead of silently parsing zero or everything."""
    for i, line in enumerate(lines):
        if line.strip() == ANGLES_HEADING:
            return i, line
    raise ValueError(f"docs/angle_language.md has no {ANGLES_HEADING!r} heading")


def parse_doc(text):
    all_lines = text.splitlines()
    anchor_index, anchor_line = find_angles_anchor(all_lines)
    lines = all_lines[anchor_index + 1:]

    angles = {}
    current_slug = None
    current_field = None
    buffer = []

    def flush():
        if current_slug is not None and current_field is not None:
            angles[current_slug][current_field] = buffer[:]

    for line in lines:
        h3 = re.match(r"^###\s+(.+?)\s*$", line)
        h4 = re.match(r"^####\s+(.+?)\s*$", line)
        if h3:
            flush()
            name = h3.group(1).strip()
            slug = ANGLE_SLUGS.get(name)
            if slug is None:
                raise ValueError(f"unrecognised angle heading in doc: {name!r}")
            current_slug = slug
            angles[current_slug] = {}
            current_field = None
            buffer = []
        elif h4:
            flush()
            current_field = _field_key(h4.group(1).strip())
            buffer = []
        else:
            buffer.append(line)
    flush()

    rows = {}
    for slug, fields in angles.items():
        rows[slug] = {
            "core_angle": _join_paragraph(fields.get("core_angle", [])),
            "causes": _join_paragraph(fields.get("causes", [])),
            "main_pain_point": _join_paragraph(fields.get("main_pain_point", [])),
            "main_benefit": _join_paragraph(fields.get("main_benefit", [])),
            "common_phrases": _split_phrases(fields.get("common_phrases", [])),
            "result_phrases": _split_phrases(fields.get("result_phrases", [])),
            "best_verbatims": [],
            "image_direction": strip_leadin(_join_paragraph(fields.get("image_direction", []))),
        }
    return rows, (anchor_index, anchor_line)


def check_no_unstripped_leadin(rows):
    """strip_leadin cuts at the first ':' in the whole text, which is wider
    than 'remove exactly the lead-in sentence' - a mid-paragraph ':' in an
    angle nobody has eyeballed would silently truncate image_direction instead
    of raising. This is the narrower, explicit check for the one lead-in
    phrase actually observed in the doc ('These are the specific visual
    descriptions...'): if it's still there, strip_leadin did nothing, meaning
    either the doc's wording changed or the ':' it needed isn't where expected."""
    offenders = [slug for slug, row in rows.items()
                 if row["image_direction"].startswith("These are")]
    if offenders:
        raise ValueError(
            f"image_direction still starts with the lead-in sentence for: {offenders} "
            "- strip_leadin did not fire as expected, refusing to proceed"
        )


def _print_dry_run_report(rows, anchor):
    anchor_index, anchor_line = anchor
    print(f"Angles anchor matched at line {anchor_index + 1}: {anchor_line!r}")
    print()
    for slug in ANGLE_SLUGS.values():
        row = rows[slug]
        cp, rp = row["common_phrases"], row["result_phrases"]
        first_12_words = " ".join(row["image_direction"].split()[:12])
        print(f"--- {slug} ---")
        print(f"common_phrases: count={len(cp)}")
        print(f"result_phrases: count={len(rp)}")
        print(f"image_direction (first 12 words): {first_12_words!r}")
        print()
    for slug in ("loose_skin", "glp1"):
        row = rows[slug]
        cp, rp = row["common_phrases"], row["result_phrases"]
        print(f"--- {slug} (detail) ---")
        print(f"common_phrases: count={len(cp)} first_3={cp[:3]} last_3={cp[-3:]}")
        print(f"result_phrases: count={len(rp)} first_3={rp[:3]} last_3={rp[-3:]}")
        print(f"image_direction: {row['image_direction']!r}")
        print(f"best_verbatims == []: {row['best_verbatims'] == []}")
        print()
    print("check_no_unstripped_leadin: all six clear (no ValueError raised above this line)")


def main():
    # Windows defaults sys.stdout to the console codepage (cp1252 etc.), not
    # UTF-8, even when redirected to a file - silently mangling em-dashes and
    # accented characters (é in "décolletage") into U+FFFD on write, with no
    # error raised. Force UTF-8 so printed/redirected output matches the
    # actual in-memory string contents.
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                         help="Parse and print diagnostics only. No DB connection opened.")
    args = parser.parse_args()

    text = DOC_PATH.read_text(encoding="utf-8")
    rows, anchor = parse_doc(text)

    missing = [slug for slug in ANGLE_SLUGS.values() if slug not in rows]
    if missing:
        raise ValueError(f"doc is missing section(s) for: {missing}")

    check_no_unstripped_leadin(rows)

    if args.dry_run:
        _print_dry_run_report(rows, anchor)
        return

    anchor_index, anchor_line = anchor
    print(f"Angles anchor matched at line {anchor_index + 1}: {anchor_line!r}")

    from src import dedupe

    # angle_language had zero init_angle_language() call sites anywhere in the
    # repo as of the start of this session - Task 2 wiring it into the 9
    # existing init_angles() call sites lands it for next time those run, but
    # nothing has actually EXECUTED any of those sites yet in this DB. Check
    # explicitly, rather than assume the table is there, before the first upsert.
    with dedupe.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
            ("angle_language",),
        )
        existed_before = cur.fetchone()[0]

    dedupe.init_angles()
    dedupe.init_angle_language()
    print(f"angle_language table: {'already existed' if existed_before else 'created by this run'}")

    existing_slugs = {a["slug"] for a in dedupe.get_angles()}
    unknown = [slug for slug in rows if slug not in existing_slugs]
    if unknown:
        raise ValueError(
            f"angle_slug(s) not present in angles table, aborting before any write: {unknown}"
        )

    for slug in ANGLE_SLUGS.values():
        row = rows[slug]
        dedupe.upsert_angle_language(
            angle_slug=slug,
            core_angle=row["core_angle"],
            causes=row["causes"],
            main_pain_point=row["main_pain_point"],
            main_benefit=row["main_benefit"],
            common_phrases=row["common_phrases"],
            result_phrases=row["result_phrases"],
            best_verbatims=row["best_verbatims"],
            image_direction=row["image_direction"],
        )
        print(
            f"{slug}: {len(row['common_phrases'])} common phrases, "
            f"{len(row['result_phrases'])} result phrases loaded"
        )

    print()
    print("--- read-back via dedupe.get_angle_language() ---")
    decolletage = "décolletage"
    for slug in ANGLE_SLUGS.values():
        readback = dedupe.get_angle_language(slug)
        if readback is None:
            print(f"{slug}: NO ROW FOUND on read-back")
            continue
        in_source = decolletage in rows[slug]["image_direction"]
        if in_source:
            accent_check = "intact" if decolletage in readback["image_direction"] else "LOST"
        else:
            accent_check = "n/a (not present in source for this angle)"
        print(
            f"{slug}: common_phrases={len(readback['common_phrases'])} "
            f"result_phrases={len(readback['result_phrases'])} "
            f"décolletage accent: {accent_check}"
        )


if __name__ == "__main__":
    main()
