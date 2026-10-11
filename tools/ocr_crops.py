"""OCR the routes: crop every `ocr` route router-cli gives and read it with Tesseract, caching each result.

For each route, the page is rendered with PyMuPDF over the route's box and 6 pt round it, in grey, turned so
its own text runs left to right (a page with /Rotate can read sideways as displayed): an image route at
the image's own resolution (its dpi from the router, kept between 300 and 400), outlined and garbled text
at 300 dpi. Tesseract reads the crop (TSV output, one word per line with its box and confidence), and the
words' boxes are mapped back to points on the page as displayed (after /Rotate, like the router's).
Tesseract runs with OMP_THREAD_LIMIT=1, so the same crop always gives the same words, and its TSV is
cached under data/ocr-cache by a hash of the crop's PNG and the Tesseract settings: a rerun that renders
the same crops calls Tesseract for none of them.

Tesseract refuses an image more than 32,767 px on a side. A crop that would be longer (a well log can be
14,000 pt tall, 70,000 px at 360 dpi) is read in strips across its long side, at the same resolution, each
at most STRIP_PX long and overlapping the next by STRIP_OVERLAP points. Each strip is a crop of its own in
the output (with "strip": [k, n]), and a word is kept only by the strip whose own share of the box holds
the word's centre, so a word in an overlap is counted once. A crop under the limit is read as before.

With --pages, each whole page is one crop instead (source "page", 300 dpi), whatever the router says:
the baseline of OCR'ing every page, read the same way. Each crop's Tesseract time, in seconds, is kept
next to its cached TSV; a crop cached before times were kept is read again to time it, and the new TSV
must match the cached one.

With --engine=ppocrv6, PP-OCRv6 (Apache 2.0, ONNX on CPU) reads the crops instead of Tesseract. It runs in
its own Python environment (paddleocr with onnxruntime; no Paddle framework), named by the PPOCR_PYTHON
environment variable, as one long-lived tools/ppocr_worker.py per worker thread, each on one CPU thread.
--ppocr-tier=tiny|small|medium (default small) and --ppocr-side (the detector's long side in pixels,
default 960) choose the model. PP-OCRv6 gives text lines, so each line's box is shared among its words by
their lengths in characters; its score (0 to 1) is written as 0 to 100, Tesseract's scale. Its results are
cached apart from Tesseract's, under a key that includes the model and settings.

Output, one JSON line per file:
  {"file": ..., "pages": [{"n": 1, "crops": [{"route": 0, "source": "image", "box": [x0, y0, x1, y1],
    "dpi": 200, "px": [w, h], "cache": "<hash>", "secs": 0.41, "words": [[x0, y0, x1, y1, "text", conf], ...]}]}]}

With --routes=<file.jsonl>, the routes come from that file, one router-cli line per file ({"file", "status",
"pages": [{"n", "width", "height", "routes": [...]}]}), instead of from running router-cli: a caller that
recorded its routes (doc-router's plan) gets exactly those crops read, whatever router is built now.

usage: python tools/ocr_crops.py <list.txt> --out=<file.jsonl> [--jobs=4] [--pages] [--engine=ppocrv6 [--ppocr-tier=small] [--ppocr-side=960]]
       python tools/ocr_crops.py --routes=<routes.jsonl> --out=<file.jsonl> [--jobs=4]   routes given, not routed here
       python tools/ocr_crops.py --split=tune --out=<file.jsonl> [--jobs=4] [--pages]    the constructed set's split
       python tools/ocr_crops.py --real=005 --root=<govdocs1 dir> --out=<file.jsonl> [--pages]    the real set's picked pages only
Held-out data is refused unless tools/check_frozen.py passes.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fitz
from rich.progress import MofNCompleteColumn, Progress, TimeElapsedColumn

from check_frozen import require_frozen

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "router" / "target" / "release" / "router-cli.exe"
CACHE = ROOT / "data" / "ocr-cache"
SET = ROOT / "data" / "constructed"
ARGS = ["-l", "eng", "--psm", "3"]
TEXT_DPI = 300
# white space added round each crop, in points: Tesseract finds little on a crop cut tight to its letters
PAD = 6
# an image is read at its own resolution, but at 300 dpi at least: Tesseract reads small type better scaled
# up (on the constructed set, full scans read at their own 150 dpi lost to the same pages read at 300)
DPI_RANGE = (300, 400)
# Tesseract's limit on an image side, in pixels; longer crops are read in strips (see the docstring)
MAX_PX = 32767
STRIP_PX = 8000
STRIP_OVERLAP = 36
# crops rendered ahead of the workers, per worker: enough to keep them busy, few enough that memory stays
# flat however many files are read
IN_FLIGHT = 2


def strips(box, dpi):
    """[(box, core, part)] for a route's box: itself alone when its crop fits Tesseract's limit (core and
    part None), else strips across its long side. core is the (axis, lo, hi) share of the box whose word
    centres the strip keeps; part is [k, n]."""
    w, h = box[2] - box[0], box[3] - box[1]
    if max(w, h) + 2 * PAD <= MAX_PX * 72 / dpi:
        return [(box, None, None)]
    axis = 1 if h >= w else 0  # the long side: y (1) or x (0)
    lo, hi = box[axis], box[axis + 2]
    n = -(-int((hi - lo) * dpi / 72) // STRIP_PX)
    step = (hi - lo) / n
    out = []
    for k in range(n):
        a, b = lo + k * step, lo + (k + 1) * step
        sb = list(box)
        sb[axis], sb[axis + 2] = max(lo, a - STRIP_OVERLAP), min(hi, b + STRIP_OVERLAP)
        core = (axis, -float("inf") if k == 0 else a, float("inf") if k == n - 1 else b)
        out.append(([round(v, 2) for v in sb], core, [k, n]))
    return out


def kept(word, core):
    """A strip keeps a word whose centre lies in its own share of the box."""
    if core is None:
        return True
    axis, lo, hi = core
    centre = (word[axis] + word[axis + 2]) / 2
    return lo <= centre < hi


def tesseract_version():
    out = subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout
    return out.split("\n", 1)[0].strip()


def routes_of(files):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("\n".join(str(Path(p).resolve()).replace("\\", "/") for p in files))
        lst = f.name
    out = subprocess.run([str(CLI), "--list", lst], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    Path(lst).unlink()
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def turned(page):
    """The page turned so its own text runs left to right, for reading: (the page to render, the matrix
    from the displayed page to it, the matrix back, a document to keep alive). The text's direction is its
    lines' most common one in the text layer, by characters; the page keeps its own /Rotate when that
    already reads (or when it has no text), else the first of 0, 90, 180 and 270 that does. Checked on
    govdocs1 005: 005530 (/Rotate 270) reads with /Rotate taken off, 005523 and 005944 (/Rotate 90) as
    displayed. A page that reads as displayed is rendered as it is, unchanged."""
    count = Counter()
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            count[(round(line["dir"][0]), round(line["dir"][1]))] += sum(len(s["text"]) for s in line["spans"])
    d = fitz.Point(count.most_common(1)[0][0]) if count else None
    reads = lambda m: d is None or ((v := d * m - fitz.Point(0, 0) * m).x > 0.9 and abs(v.y) < 0.1)
    if reads(page.rotation_matrix):
        return page, fitz.Identity, fitz.Identity, None
    one = fitz.open()
    one.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
    cp = one[0]
    for r in (0, 90, 180, 270):
        cp.set_rotation(r)
        if reads(cp.rotation_matrix):
            break
    else:
        cp.set_rotation(page.rotation)
    return cp, page.derotation_matrix * cp.rotation_matrix, cp.derotation_matrix * page.rotation_matrix, one


def crop(turn, box, dpi):
    """The route's box, from the page as displayed, rendered in grey at dpi from the turned page; where the
    crop's top-left pixel sits, in pixels at that dpi on the turned page; and the matrix from the turned
    page's points to the displayed page (None when they're the same). The clip is in the coordinates of
    the page as rendered."""
    page, to_turned, back, _ = turn
    clip = (fitz.Rect(box[0] - PAD, box[1] - PAD, box[2] + PAD, box[3] + PAD) * to_turned) & page.rect
    pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csGRAY, alpha=False)
    return pix.tobytes("png"), pix.width, pix.height, pix.x, pix.y, None if back == fitz.Identity else back


def read(png, dpi, key):
    """Tesseract's TSV for the crop and the seconds it took, from the cache when both are there. The TSV is
    written last, and each file through a temporary name and a rename, so a cached crop is always whole."""
    path, secs = CACHE / f"{key}.tsv", CACHE / f"{key}.secs"
    if path.exists() and secs.exists() and secs.read_text().strip():
        return path.read_text(encoding="utf-8"), float(secs.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "c.png"
        img.write_bytes(png)
        env = dict(os.environ, OMP_THREAD_LIMIT="1")
        t0 = time.perf_counter()
        tsv = subprocess.run(["tesseract", str(img), "stdout", "--dpi", str(dpi), *ARGS, "tsv"], capture_output=True,
                             text=True, encoding="utf-8", env=env, check=True).stdout
        took = round(time.perf_counter() - t0, 3)
    if path.exists() and path.read_text(encoding="utf-8") != tsv:
        raise SystemExit(f"Tesseract read crop {key} differently on a second run")
    for target, text in ((secs, str(took)), (path, tsv)):
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    return tsv, took


PPOCR_WORKER = Path(__file__).resolve().parent / "ppocr_worker.py"
# memory one PP-OCRv6 reader reached on dense Sodir pages (small tier, detector side 960): 840 MB
PPOCR_GB = 0.9
_ppocr = threading.local()
_ppocr_all = []


def ppocr_settings(tier, side):
    """The engine's identity for the cache key: model, detector size and package versions."""
    py = os.environ.get("PPOCR_PYTHON")
    if not py:
        sys.exit("--engine=ppocrv6 needs PPOCR_PYTHON: the Python of an environment with paddleocr and onnxruntime")
    v = subprocess.run([py, "-c", "import paddleocr, onnxruntime; print(paddleocr.__version__, onnxruntime.__version__)"],
                       capture_output=True, text=True, check=True).stdout.split()
    return f"PP-OCRv6_{tier} side {side}, 1 thread, paddleocr {v[0]}, onnxruntime {v[1]}"


def read_ppocr(png, key, tier, side):
    """PP-OCRv6's lines for the crop (JSON) and the seconds it took, cached like Tesseract's TSV. Each worker
    thread keeps its own reader process, so the models load once per thread."""
    path, secs = CACHE / f"{key}.ppocr.json", CACHE / f"{key}.secs"
    if path.exists() and secs.exists() and secs.read_text().strip():
        return path.read_text(encoding="utf-8"), float(secs.read_text())
    w = getattr(_ppocr, "proc", None)
    if w is None:
        w = subprocess.Popen([os.environ["PPOCR_PYTHON"], str(PPOCR_WORKER), tier, str(side)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        if '"ready"' not in w.stdout.readline():
            raise RuntimeError("the PP-OCRv6 worker didn't start")
        _ppocr.proc, _ppocr.dir = w, tempfile.mkdtemp()
        _ppocr_all.append(w)
    img = Path(_ppocr.dir) / "c.png"
    img.write_bytes(png)
    t0 = time.perf_counter()
    w.stdin.write(json.dumps({"png": str(img)}) + "\n")
    w.stdin.flush()
    answer = json.loads(w.stdout.readline())
    took = round(time.perf_counter() - t0, 3)
    if "error" in answer:
        raise RuntimeError(f"PP-OCRv6 on crop {key}: {answer['error']}")
    text = json.dumps(answer["lines"], ensure_ascii=False)
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise SystemExit(f"PP-OCRv6 read crop {key} differently on a second run")
    for target, t in ((secs, str(took)), (path, text)):
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(t, encoding="utf-8")
        os.replace(tmp, target)
    return text, took


def words_of_ppocr(lines_json, ox, oy, dpi):
    """PP-OCRv6's lines as words in points on the displayed page: each line's box shared among its words
    by length in characters (a space counts as one), its score as 0 to 100."""
    s = 72 / dpi
    out = []
    for x0, y0, x1, y1, text, score in json.loads(lines_json):
        per = (x1 - x0) / max(len(text), 1)
        at = 0
        for word in text.split(" "):
            if word:
                a, b = x0 + at * per, x0 + (at + len(word)) * per
                out.append([round((ox + a) * s, 2), round((oy + y0) * s, 2), round((ox + b) * s, 2), round((oy + y1) * s, 2),
                            word, round(score * 100, 1)])
            at += len(word) + 1
    return out


def words_of(tsv, ox, oy, dpi):
    """Word lines of Tesseract's TSV, their pixel boxes moved to points on the displayed page."""
    s = 72 / dpi
    out = []
    for line in tsv.splitlines()[1:]:
        f = line.split("\t")
        if len(f) == 12 and f[0] == "5" and f[11].strip():
            x, y, w, h = (int(v) for v in f[6:10])
            out.append([round((ox + x) * s, 2), round((oy + y) * s, 2), round((ox + x + w) * s, 2), round((oy + y + h) * s, 2),
                        f[11], round(float(f[10]), 1)])
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    flags = {a[2:] for a in sys.argv[1:] if a.startswith("--") and "=" not in a}
    if "out" not in opt or (not args and "split" not in opt and "real" not in opt and "routes" not in opt):
        sys.exit(__doc__)
    picked = None
    if "real" in opt:
        man = json.loads((ROOT / "data" / "real" / f"{opt['real']}.json").read_text(encoding="utf-8"))
        if man["items"] and man["items"][0]["split"] != "tune":
            require_frozen()
        files = sorted({Path(opt["root"]) / i["file"] for i in man["items"]})
        picked = {(Path(i["file"]).name, i["page"]) for i in man["items"]}
    elif "split" in opt:
        if opt["split"] != "tune":
            require_frozen()
        man = json.loads((SET / "manifest.json").read_text(encoding="utf-8"))
        files = [SET / i["file"] for i in man["items"] if i["split"] == opt["split"]]
    elif "routes" in opt:
        files = None
    else:
        files = [Path(l.strip()) for l in open(args[0], encoding="utf-8") if l.strip()]
    CACHE.mkdir(parents=True, exist_ok=True)
    ppocr = opt.get("engine") == "ppocrv6"
    tier, side = opt.get("ppocr-tier", "small"), int(opt.get("ppocr-side", 960))
    version = ppocr_settings(tier, side) if ppocr else tesseract_version()
    settings = version.encode() if ppocr else "|".join([version, *ARGS]).encode()

    # Crops are rendered one at a time and handed to the workers as they come, across all the files, so
    # the workers never wait on a file's rendering and a long file doesn't hold up the rest. At most
    # IN_FLIGHT crops wait as PNGs in memory; each is dropped once Tesseract has read it. The same crop
    # can come up twice in a run (an image drawn twice): it's read once, and the first occurrence carries
    # the Tesseract time (two threads on one cache key once read a half-written file).
    workers = int(opt.get("jobs", 4))
    if ppocr:
        # a PP-OCRv6 reader holds about PPOCR_GB of memory on a dense page: no more readers than fit
        try:
            import psutil
            fit = max(1, int(psutil.virtual_memory().available / 2**30 / PPOCR_GB))
            if fit < workers:
                print(f"--jobs={workers} lowered to {fit}: PP-OCRv6 readers need about {PPOCR_GB} GB each", file=sys.stderr)
                workers = fit
        except ImportError:
            pass
    room = threading.BoundedSemaphore(IN_FLIGHT * workers)
    jobs, rows, futures, cached = [], [], {}, 0
    with Progress(*Progress.get_default_columns(), TimeElapsedColumn(), MofNCompleteColumn(), transient=True) as bar, \
            ThreadPoolExecutor(max_workers=workers) as pool:
        task = bar.add_task("tesseract", total=0)

        def one(png, dpi, key):
            try:
                return read_ppocr(png, key, tier, side) if ppocr else read(png, dpi, key)
            finally:
                room.release()
                bar.advance(task)

        given = None
        if "routes" in opt:
            given = [json.loads(l) for l in open(opt["routes"], encoding="utf-8") if l.strip()]
        for d in given if given is not None else routes_of(files):
            row = {"file": d["file"], "pages": []}
            rows.append(row)
            if d.get("status") != "ok":
                row["status"] = d.get("status")
                continue
            with fitz.open(d["file"]) as doc:
                for p in d["pages"]:
                    if picked is not None and (Path(d["file"]).name, p["n"]) not in picked:
                        continue
                    crops = []
                    turn = turned(doc[p["n"] - 1])
                    routes = [{"source": "page", "decision": "ocr", "dpi": 0, "x0": 0, "y0": 0, "x1": p["width"], "y1": p["height"]}] if "pages" in flags else p["routes"]
                    for i, r in enumerate(routes):
                        if r["decision"] != "ocr":
                            continue
                        dpi = round(min(max(r["dpi"], DPI_RANGE[0]), DPI_RANGE[1])) if r["source"] == "image" else TEXT_DPI
                        for box, core, part in strips([r["x0"], r["y0"], r["x1"], r["y1"]], dpi):
                            png, w, h, ox, oy, rot = crop(turn, box, dpi)
                            key = hashlib.sha256(png + b"|" + settings + b"|" + str(dpi).encode()).hexdigest()[:24]
                            c = {"route": i, "source": r["source"], "box": box, "dpi": dpi, "px": [w, h], "cache": key}
                            if part:
                                c["strip"] = part
                            crops.append(c)
                            jobs.append((c, ox, oy, rot, core))
                            if key not in futures:
                                cached += (CACHE / f"{key}.tsv").exists() and (CACHE / f"{key}.secs").exists()
                                room.acquire()
                                bar.update(task, total=len(futures) + 1)
                                futures[key] = pool.submit(one, png, dpi, key)
                            del png
                    row["pages"].append({"n": p["n"], "crops": crops})
            # MuPDF keeps decoded images in its store across files; emptied after each file so memory
            # stays flat over a long list
            fitz.TOOLS.store_shrink(100)
        results = {k: f.result() for k, f in futures.items()}
    unique = futures
    timed = set()
    for c, ox, oy, rot, core in jobs:
        tsv, took = results[c["cache"]]
        c["secs"] = 0.0 if c["cache"] in timed else took
        timed.add(c["cache"])
        c["words"] = words_of_ppocr(tsv, ox, oy, c["dpi"]) if ppocr else words_of(tsv, ox, oy, c["dpi"])
        if rot is not None:
            for w in c["words"]:
                r = fitz.Rect(w[:4]) * rot
                w[:4] = [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]
        c["words"] = [w for w in c["words"] if kept(w, core)]
    Path(opt["out"]).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    words = sum(len(c["words"]) for c, *_ in jobs)
    secs = sum(c["secs"] for c, *_ in jobs)
    for w in _ppocr_all:
        w.stdin.close()
        w.wait()
    engine = "PP-OCRv6" if ppocr else "Tesseract"
    print(f"{len(rows)} files, {len(jobs)} crops, {len(unique)} different ({cached} from the cache), {words} words, {engine} {secs:.0f} s in all; {version}" + ("" if ppocr else f", {' '.join(ARGS)}"))


if __name__ == "__main__":
    main()
