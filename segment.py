#!/usr/bin/env python3
"""Split a cached posting body into numbered segments for Jev line selection.

Cached bodies are ONE line (the page text flattened, no newlines), so Jev's
"pick a line id" pattern needs a sentence splitter first. Every segment is an
exact slice of the body, so a segment Jev selects is already a verbatim quote
and the substring-verification contract still holds.

  python3 segment.py --coverage     # score the splitter against posting_quotes.csv
"""
import csv, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BODIES = os.path.join(HERE, "data", "cache", "bodies")

# a full stop after one of these does not end a sentence ("( e.g. SageMaker")
ABBR = re.compile(r"\b(e\.g|i\.e|etc|vs|approx|incl|esp|cf|Dr|Mr|Ms|Mrs|Prof|Inc|Ltd|Co|Corp|"
                  r"No|St|Jr|Sr|Ph\.D|U\.S|U\.K|a\.m|p\.m)\.$", re.I)
BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")
MAX_WORDS = 80          # longer runs are usually a flattened list; cut them at list punctuation
LIST_CUT = re.compile(r"\s+(?=•)|(?<=;)\s+")


def spans(body):
    """[(start, end)] covering the body, in order, whitespace between trimmed off."""
    out, start = [], 0
    for m in BOUNDARY.finditer(body):
        if ABBR.search(body[start:m.start()]):
            continue
        out.append((start, m.start()))
        start = m.end()
    out.append((start, len(body)))
    fine = []
    for s, e in out:
        if len(body[s:e].split()) <= MAX_WORDS:
            fine.append((s, e))
            continue
        cut = s
        for m in LIST_CUT.finditer(body, s, e):
            fine.append((cut, m.start()))
            cut = m.end()
        fine.append((cut, e))
    return [(s, e) for s, e in fine if body[s:e].strip()]


def seg_id(i):
    return f"S{i:03d}"


def segments(body):
    """[(seg_id, text, start, end)]"""
    return [(seg_id(i), body[s:e], s, e) for i, (s, e) in enumerate(spans(body))]


def document(segs):
    """Numbered document for a Choice over segment ids."""
    return "\n".join(f"{sid}| {text}" for sid, text, _, _ in segs)


def load_body(posting_id):
    p = os.path.join(BODIES, posting_id + ".txt")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def best_overlap(segs, qs, qe):
    """(seg_id, share of the quote span that the single best segment covers)"""
    best = (None, 0.0)
    for sid, _, s, e in segs:
        ov = max(0, min(e, qe) - max(s, qs)) / max(1, qe - qs)
        if ov > best[1]:
            best = (sid, ov)
    return best


def coverage():
    quotes = list(csv.DictReader(open(os.path.join(HERE, "data", "posting_quotes.csv"), encoding="utf-8")))
    n = inside = most = 0
    by_type, lens, nsegs = {}, [], {}
    for q in quotes:
        body = load_body(q["posting_id"])
        if body is None or q["quote_text"] not in body:
            continue
        if q["posting_id"] not in nsegs:
            segs = segments(body)
            nsegs[q["posting_id"]] = segs
            lens += [len(t.split()) for _, t, _, _ in segs]
        segs = nsegs[q["posting_id"]]
        qs = body.find(q["quote_text"])
        _, ov = best_overlap(segs, qs, qs + len(q["quote_text"]))
        n += 1
        inside += ov >= 0.999
        most += ov >= 0.6
        t = by_type.setdefault(q["claim_type"], [0, 0])
        t[0] += 1
        t[1] += ov >= 0.999
    lens.sort()
    print(f"quotes scored {n}  inside one segment {inside} ({inside/n:.1%})  >=60% in one segment {most} ({most/n:.1%})")
    print("by claim_type (inside/total):", {k: f"{v[1]}/{v[0]}" for k, v in sorted(by_type.items())})
    counts = sorted(len(s) for s in nsegs.values())
    print(f"segments per body: min {counts[0]} median {counts[len(counts)//2]} max {counts[-1]} (Choice cap 255)")
    print(f"words per segment: median {lens[len(lens)//2]} p95 {lens[int(len(lens)*.95)]} max {lens[-1]}")


if __name__ == "__main__":
    if "--coverage" in sys.argv:
        coverage()
    else:
        print(__doc__)
