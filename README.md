# what-needs-ocr

What needs OCR? Most PDFs made on a computer carry their text, but parts of a page can exist only as pixels: a scanned page pasted into a contract, a chart exported as a picture, a logo, letters drawn as outlines, or a text layer whose characters don't decode to anything. This tool finds those parts on each page, crops them, has Tesseract read the crops and nothing else, and merges what it reads with the file's own words, each word with its box and where it came from. On held-out government PDFs it sent 5.5% of the page area to OCR, against 98.8% for OCR'ing every page, and its word error against the reference was 4 points lower.

It's the fifth in a series, after [file-checker](https://github.com/DscvryAI/file-checker) (what is this upload?), [scan-or-text](https://github.com/DscvryAI/scan-or-text) (does this PDF need OCR?), [wordbox](https://github.com/DscvryAI/wordbox) (where is the text?) and [where-are-the-regions](https://github.com/DscvryAI/where-are-the-regions) (what does each page draw?). It reads the page map where-are-the-regions makes, and scan-or-text's question gets a per-region answer here instead of one per file.

The write-up, with a demo you can try, is at https://dscvryai.com/insights/what-needs-ocr/.

## Output

router-cli prints one JSON object per file. Coordinates are PDF points from the top-left of the page as displayed, the same as where-are-the-regions, so a box can be drawn straight onto a rendered page. Each page has its routes:

```json
{"x0":78.81,"y0":696.1,"x1":452.96,"y1":830,"source":"image","index":0,"decision":"ocr","confidence":0.95,"dpi":200.13,"reasons":[["text",0.95],["has_text",0.95]]}
```

`source` says what the route came from. An `image` route is every image the page draws, with the decision `ocr`, `skip`, or `text_layer` when invisible words already cover it (an earlier OCR). Its confidence is the page map's structure score for the image times the pixel evidence that it holds text, and it's sent to OCR at 0.1 or more. A `vector` route is a block of letters drawn as outlines, or a chart or table drawing with at least 3 letter shapes in it. A `words` route is a block of the file's own words that don't decode, when at least half of that font's words don't: private-use or control characters, U+FFFD, or codes with no Unicode at all. `dpi` is an image's own resolution, so its crop can be read at that size.

With `--merge`, router-cli takes the OCR results `tools/ocr_crops.py` wrote and prints each page's merged word list instead:

```json
{"t":"AGREEMENT","x0":208.03,"y0":23.56,"x1":264.99,"y1":33.53,"source":"file","confidence":1}
{"t":"(e)","x0":191.76,"y0":740.64,"x1":203.76,"y1":750.72,"source":"ocr","confidence":0.92,"crop":0}
```

A word's `source` is `file` (a visible word the file gives as text), `file_ocr_layer` (a word of an invisible OCR layer already in the file, kept as it is) or `ocr` (read in a crop, with Tesseract's confidence and the crop's index). Words nobody sees are left out: off the page, clipped away or painted white. An OCR word that lands on a file word is dropped, since the file's text is exact, and OCR words with a confidence under 0.5 are dropped too (`--min-conf 0` keeps them all).

## Layout

- `router/`: the library and `router-cli`. `src/route.rs` decides the routes and `src/merge.rs` merges the words. It depends on where-are-the-regions as a path dependency, so the two repos have to sit side by side.
- `wasm/`: the browser build, `routes_json(bytes)` and `merge_json(bytes, crops, min_conf)`. It's a separate crate so the frozen router stays untouched, and `tools/wasm_check.mjs` confirms its output equals router-cli's.
- `demo/`: the browser demo. pdf.js 6.3.289 (Apache-2.0, bundled with its licence) draws the page, and the routes are worked out live in the browser and boxed on top. Its three made-up sample PDFs come from `tools/samples.py`, with their crops read beforehand by Tesseract, since Tesseract doesn't run in the page; `tools/demo_check.mjs` drives it in headless Chrome.
- `tools/`: building the test sets, cropping and OCR (`ocr_crops.py`, with Tesseract or PP-OCRv6 through `ppocr_worker.py`), the scorers, the freeze check, and the govdocs1 download (`fetch_govdocs.py`, resumable).
- `data/`: the constructed set's manifest and the real pages' references.
- `results/`: every number below, with how it was measured, and each run's OCR output.

## Data

The constructed set (`tools/build_ocr_set.py`, results/constructed-set.md) is 600 one-page PDFs rebuilt from ContractNLI's NDAs (CC BY 4.0), with the truth known by construction: a rasterised crop of the page pasted in, the whole page as a scan, a crop's words redrawn as outlines or with a garbage ToUnicode map, an already-OCR'd crop, and pages where OCR should find nothing (a photo, a logo, a blank sheet, the page untouched). Tesseract checked the truth, finding 99.1% of its words. The PDFs aren't committed; the manifest is, with each file's sha256.

Real pages come from [govdocs1](https://digitalcorpora.org/corpora/file-corpora/files/): thread 005 for tuning (668 pages), 006 held out (636 pages) and 007 for testing a change made after the held-out run (613 pages), each page with a usable text layer. Real PDFs have no truth, so `tools/build_real.py` builds a reference without the router: the file's visible words that decode, plus the words Tesseract reads at 400 dpi with a confidence of 85 or more that aren't on a file word, plus OCR in place of a layer that doesn't match what the page shows. Claude checked a sample of 20 pages by eye (results/real-reference-check.md): where the reference has a word it's right about 92% of the time, and it misses many small labels on maps and charts. So the real-page numbers are agreement with the reference, not accuracy. govdocs1 is public domain and isn't redistributed; `data/real/` holds only the references.

The rules were tuned on the tune data only, then frozen by hash (`results/frozen.sha256`, with where-are-the-regions and pdf-core pinned at their commits, checked by `tools/check_frozen.py`, which every held-out tool calls) and run once on the held-out data against targets set beforehand. Changes since then are reported apart in results/after-heldout.md: a confidence cutoff in the merge, chosen on tune data and tested on 007; a fix in where-are-the-regions so that scans turned by /Rotate aren't skipped; a page floor, so a page with no text of its own always has its largest image read; faster thumbnails; and has-text changes for scanned reports.

## Results

All runs on a Windows laptop with Tesseract 5.4.0 (`-l eng --psm 3`, one thread), 3 and 4 October 2026.

Routing alone on the constructed held-out split, 300 pages (results/heldout.md): 210 of the 210 regions that need OCR were routed, holding 99.9% of their words, and none of the 30 already-OCR'd crops or 45 photos, logos and blank sheets was sent to OCR. Two of the 15 untouched pages got a route the scorer counts as wrong. One is a real mistake: two bullets stored as a private-use character, the only two words in their font, were sent to OCR as an undecodable layer.

The whole pipeline, held out, with the rules frozen. Recall and precision count words matched one to one by text and position, and word error is missing plus extra words over the truth's words.

| Set | Method | Recall | Word error | Page area OCR'd | Tesseract seconds |
|---|---|---|---|---|---|
| Constructed, 300 pages | routed | 99.70% | 0.49% | 21.95% | 299 |
| | every page | 98.93% | 1.76% | 100.00% | 922 |
| | file text | 76.49% | 23.52% | 0% | 0 |
| govdocs1 006, 636 pages | routed | 99.19% | 2.09% | 5.50% | 118 |
| | every page | 96.90% | 6.17% | 98.78% | 1,946 |
| | file text | 98.53% | 1.68% | 0% | 0 |

Both targets were met: word error no more than a point worse than OCR'ing every page on the constructed set (it was 1.27 points better), and on real pages at least 70% less area sent to OCR with no more than a point more word error (94% less area, 4.08 points less word error). OCR'ing every page does worse than the router on word error because it reads the file's own words again and misreads some of them.

On 006 the routed word error was higher than the file's text alone. Most of the extra words were OCR words with a low confidence, so after the held-out run the merge got a cutoff, chosen on the tune data by a rule written before the test (results/after-heldout.md). Tested once on 007:

| govdocs1 007, 613 pages | Recall | Word error | Recall of pixel-only words | Page area OCR'd | Tesseract seconds |
|---|---|---|---|---|---|
| routed, cutoff 0.5 | 98.97% | 1.42% | 83.60% | 2.35% | 92 |
| routed, no cutoff | 98.98% | 1.65% | 83.87% | 2.35% | 92 |
| every page | 96.12% | 6.90% | 91.91% | 98.25% | 1,738 |
| file text | 98.32% | 1.85% | 9.56% | 0% | 0 |

Pixel-only words are the reference's words that exist only as pixels, the ones this tool is for. The router finds about 78 to 84% of them where OCR'ing every page finds 88 to 92%, for about a twentieth of the Tesseract time.

## Limits

The pixel-only words the router misses fall into a few groups (results/real-tune.md). Letters drawn as outlines up the side of a chart aren't found, as where-are-the-regions looks for letters in horizontal runs only. Logos whose pixels score at the page map's floor for text are skipped, along with the photos and blank sheets that score the same. A crop of a photo or a map reads less than the whole page at 400 dpi does. Private-use bullets in a font of their own are routed as undecodable text. Tesseract is the only engine tried, in English only. Routing speed hasn't been measured, and the results files hold the absolute paths of the machine they were run on.

## Commands

```
cargo build --release --manifest-path router/Cargo.toml
router/target/release/router-cli file.pdf
cargo test --release --manifest-path router/Cargo.toml
python tools/build_ocr_set.py <ContractNLI raw dir> <scan-or-text data/labels-real.json> data/constructed
python tools/fetch_govdocs.py <govdocs1 dir> 005 006 007
python tools/build_real.py <govdocs1 dir> 005
python tools/check_real.py <govdocs1 dir> 005 <out dir>
python tools/score_route.py --split=tune
python tools/ocr_crops.py --split=tune --out=results/ocr-tune.jsonl
python tools/score_ocr.py --split=tune
python tools/ocr_crops.py --real=005 --root=<govdocs1 dir> --out=results/ocr-real-005.jsonl
python tools/score_ocr.py --real=005 --root=<govdocs1 dir>
router/target/release/router-cli --merge results/ocr-tune.jsonl file.pdf
uv venv ppocr && uv pip install -p ppocr paddleocr==3.7.0 onnxruntime   # PP-OCRv6's own environment, no Paddle framework
PPOCR_PYTHON=ppocr/Scripts/python.exe python tools/ocr_crops.py <list.txt> --out=<file.jsonl> --engine=ppocrv6
python tools/check_frozen.py
(cd wasm && wasm-pack build --release --target web)   # then copy pkg/router_wasm.js and pkg/router_wasm_bg.wasm into demo/
node tools/wasm_check.mjs
python tools/samples.py demo/samples
node tools/demo_check.mjs <demo url> <screenshot dir>
```

## Licence

MIT. See [LICENSE](LICENSE). pdf.js in `demo/pdfjs/` is Apache-2.0. ContractNLI is CC BY 4.0 and govdocs1 is public domain; neither is redistributed here.
