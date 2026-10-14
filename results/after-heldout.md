# Changes after the held-out runs

The held-out results in results/heldout.md are the frozen pipeline's and stay as they are. Each change below was made later, chosen on tune data only, relocked (results/frozen.sha256, tools/check_frozen.py), and measured apart.

## An OCR-confidence cutoff in the merge (4 October 2026)

On govdocs1 006 the pipeline's word error was higher than the file's text alone (2.09% against 1.68%), and most of the OCR words that matched nothing in the reference had a Tesseract confidence under 85 (2,087 of 2,699, 1,554 under 60). The merge now leaves out OCR words under a confidence of 0.5 (router/src/merge.rs MIN_CONF; router-cli --merge ... --min-conf 0 keeps them all).

The cutoff was chosen by a rule written before any test: the lowest summed word error over the constructed tune split (exact truth) and govdocs1 005 (agreement with its reference), keeping the constructed region recall at 98% or more.

| Cutoff | Constructed tune: word error | Region recall | govdocs1 005: word error | Pixel-only words found |
|---|---|---|---|---|
| none | 0.47% | 99.18% | 1.44% | 77.42% |
| 0.3 | 0.42% | 99.03% | 1.33% | 77.08% |
| 0.5 | 0.45% | 98.80% | 1.25% | 76.84% |
| 0.6 | 0.49% | 98.60% | 1.22% | 76.55% |
| 0.7 | 0.56% | 98.27% | 1.19% | 76.11% |
| 0.8 | 0.67% | 97.75% | 1.15% | 75.39% |
| 0.85 | 0.78% | 97.26% | 1.14% | 74.85% |
| 0.9 | 1.03% | 96.16% | 1.13% | 72.34% |

On the constructed set a low cutoff drops junk and a high one starts dropping true words that Tesseract was unsure of; on 005 the word error keeps falling as the cutoff rises, partly because the reference itself keeps only words at 85 or more. 0.5 has the lowest sum (1.70).

006 has been used, so the cutoff is tested once on fresh pages, govdocs1 thread 007, built and scored the same way (below).

### The test on govdocs1 007 (4 October 2026)

613 pages from 231 files of thread 007, each with a usable text layer, 404 with images or drawings (data/real/007.json, built the same way as 005 and 006 and committed and pinned before any run). As before, the numbers are agreement with the reference, not accuracy.

| Method | Recall | Precision | Word error | Recall of pixel-only words | Page area OCR'd | Megapixels | Tesseract calls | Tesseract seconds |
|---|---|---|---|---|---|---|---|---|
| routed, cutoff 0.5 | 98.97% | 99.56% | 1.42% | 83.60% | 2.35% | 166.6 | 315 | 92 |
| routed, no cutoff | 98.98% | 99.31% | 1.65% | 83.87% | 2.35% | 166.6 | 315 | 92 |
| every page | 96.12% | 96.77% | 6.90% | 91.91% | 98.25% | 5,150.0 | 613 | 1,738 |
| file text | 98.32% | 99.82% | 1.85% | 9.56% | 0.00% | 0.0 | 0 | 0 |

The cutoff lowers the word error from 1.65% to 1.42% for 0.01 points of recall, and with it the pipeline's word error is lower than the file's text alone (1.42% against 1.85%), where on 006 without it it was higher. Against OCR'ing every page it OCRs 2.35% of the area against 98.25% and its word error is 5.48 points lower, the plan's real-page target met again on pages no choice was made on.

## Images judged as they show on the page (5 October 2026)

where-are-the-regions' kind layer judged each image as stored, not as shown, so a scan stored on its side and turned by the page's /Rotate looked like a graphic with no text, and the router skipped it. In the first 10 files of a sample of scanned well reports from the Norwegian Offshore Directorate (Sodir), it skipped 323 of 1,105 image regions. The fix is in where-are-the-regions (8019e6d, its results/kinds-turn.md), and on the same 10 files the router now skips 17.

Relocked at that commit, routing on the constructed held-out split (tools/score_route.py --split=heldout) gives the numbers in results/heldout.md: 210 of 210 regions routed, none of the 45 photos, logos and blank sheets sent to OCR, 22.0% of the page area sent against 23.6% that needs it, and 316 of 324 routes right at 0.9 and up. I didn't repeat the OCR runs.

## A page floor (5 October 2026)

After that fix I ran the router over all 478 files of the Sodir sample (43,045 pages, 83% of them scans). 695 scanned pages still had every image skipped. Of 24 picked at random and checked by eye, 14 held printed text: 10 sparse section headings ("SECTION A GEOLOGY", "4.7 Bit record"), a contents page, two dense tables and a table scanned sideways. One was handwriting, 3 were charts with axis labels and 6 were log strips or nearly blank. Skipping a page like that loses all of it, while reading a blank scan costs one OCR call that finds nothing.

So a page with no text of its own (no word the file draws, an invisible OCR layer included) and nothing routed now gets its largest image read, if that image covers at least half the page (`FLOOR`, `page_floor` in router/src/route.rs). The route keeps its own confidence and gains the reason `page_floor`. I set the rule from the Sodir sample; it wasn't tuned on any test set.

All 24 sampled pages get a floor route, and Tesseract reads them: "SECTION A GEOLOGY", the contents page, the depth table, "4.7 Bit record". Every constructed and govdocs1 page has text of its own, so the floor can't fire there. Routing on the constructed tune and held-out splits is unchanged, and the real-page results stand.

## Faster thumbnails (5 October 2026)

where-are-the-regions now indexes a file once for all its thumbnails and averages one-component images through a table (b31da19, its results/speed.md), and `route_page` takes that indexed file (`pixels::Source`). The routes are the same: on 15 Sodir files, 1,469 of 1,493 pages give identical routes and the other 24 differ only by the page floor, which those runs came before. The router took 39 s on those files, against 188 s. Held-out routing is unchanged.

## Has-text on scanned reports (5 October 2026)

where-are-the-regions' kind layer changed for scanned reports (eed4e72, its results/kinds-sparse.md): the ink side picked by glyph count on images about half dark, wider thumbnails for long thin images, sideways text counted, and has-text on nearly empty pages with a heading. Its own held-out run found 149 of 157 Sodir pages with text, against 40, and held 9 more images without text on govdocs1 004. The router uses the new has-text as it is. Routing on the constructed tune and held-out splits is unchanged; I didn't repeat the real-page OCR runs (005 to 007).

The browser build after these changes (ac5a028) gives the same routes and merges as router-cli on all 1,304 files of the recorded OCR sets, the constructed tune and held-out splits and govdocs1 005 to 007 (tools/wasm_check.mjs, no differences).

## The shared PDF reader (6 October 2026)

where-are-the-regions now reads PDFs through [pdf-core](https://github.com/sharad-bapat/pdf-core), the parser it used to share by copy with scan-or-text and wordbox; its page map and image kinds came out identical on all 1,113 of its test files. The frozen record here now holds the pdf-core commit as well as where-are-the-regions', and routing on the tune split is unchanged.

## Long crops read in strips (6 October 2026)

Tesseract refuses an image more than 32,767 px on a side, and one refusal stopped tools/ocr_crops.py for the whole file. Scanned well logs reach that: in the Sodir sample, a completion log has pages 14,000 pt tall, 70,000 px at 360 dpi. In the first 20 pages of 324 Sodir reports, 10 files have such a page (17 pages), so all 10 lost every crop.

A crop that would pass the limit is now read in strips across its long side, at the same resolution: each at most 8,000 px long (`STRIP_PX`), overlapping the next by 36 pt (`STRIP_OVERLAP`). Each strip is a crop of its own in the output, marked `"strip": [k, n]`, and keeps only the words whose centres fall in its own share of the box, so a word in an overlap is counted once. The merge takes the strips as it takes any crop.

A crop under the limit is cut and read as before. On a Sodir file with 186 crops, all under it, the output is byte for byte the same. The constructed set's longest page side is 843 pt, against 5,886 pt before a crop needs strips at 400 dpi, so no constructed result can change. On the 6-page log above, its two long pages now give 18 strips and 23,714 words, among them the lithology descriptions down the log. That file took 1,625 s of Tesseract time.

## Crops streamed to Tesseract (6 October 2026)

tools/ocr_crops.py rendered every crop of its whole file list before reading any, and kept every PNG in memory until the last was read. One file at a time that's harmless, but memory then grows with the length of the list, and the workers sit idle while rendering runs.

Crops are now handed to the workers as they're rendered, across all the files in the list, with at most two per worker waiting (`IN_FLIGHT`); a PNG is dropped once Tesseract has read it. MuPDF's store of decoded images is emptied after each file. A crop that comes up twice is still read once, and its first occurrence still carries the Tesseract time.

The output is byte for byte the same as before: on the constructed tune split (441 crops, 434 different, and again with --pages, 300 crops), and on the first 20 pages of 50 Sodir reports (1,000 crops, 184,660 words). On those 50 files, all read from the cache so that only rendering was timed, the peak memory went from 949 MB to 639 MB and the time from 295 s to 267 s. Sampled every half second, the new run's median was 157 MB; its peak was a short spike while one large scan was decoded, which the old run had too.

## PP-OCRv6 as a second reader (6 October 2026)

tools/ocr_crops.py can now read the crops with PP-OCRv6 (`--engine=ppocrv6`), PaddleOCR's detector and recognizer released in June 2026 under Apache 2.0, instead of Tesseract. It runs on ONNX Runtime in its own Python environment (paddleocr 3.7.0 with onnxruntime; the Paddle framework isn't needed), named by the PPOCR_PYTHON variable, as one long-lived tools/ppocr_worker.py per worker that loads the models once. The default is the small tier with the detector's long side at 960 px (`--ppocr-tier`, `--ppocr-side`). PP-OCRv6 reads text lines, so each line's box is shared among its words by their length in characters, and its score from 0 to 1 is written as 0 to 100, as Tesseract's is. Its results are cached apart from Tesseract's.

Three things came out of trying it on a 300 dpi Sodir summary page. The medium tier at full resolution took 23.5 s and 6.6 GB on that one page; with the detector's long side capped at 960 px the small tier took 2.6 s and 626 MB and still read the page's 53 lines, "Water depth" and "334 m" among them. Each library starts its own thread pool, so four readers side by side took 17 s a page against 5.3 s with OpenMP, OpenCV and ONNX Runtime held to one thread each; the worker now does that. And on dense pages a reader reaches about 840 MB, so ocr_crops lowers --jobs to what free memory holds (0.9 GB a reader).

On the first 20 pages of 3 Sodir reports (60 crops), PP-OCRv6 found 5,583 words against Tesseract's 5,809, in 290 s of reading against Tesseract's 57 s, with four readers. Two runs with the cache cleared between them gave the same words, and the same words as runs before the thread limit. Tesseract's output is unchanged: byte for byte on those files and on the constructed tune split. How the two compare on accuracy is for a benchmark, not these pages.

## Newer where-are-the-regions and pdf-core (7 October 2026)

Relocked at where-are-the-regions 814d2ac and pdf-core a9328cd. where-are-the-regions added output (each page's rules and each glyph's font) and changed none (its results/rules.md). pdf-core now gives text to glyphs from TeX's maths fonts that had none (its README, "Maths glyph names"); on where-are-the-regions' 1,113 test files that changed 1,610 words in 19 files, all of them unmapped before. Words that decode no longer count as undecodable, so on files typeset with TeX maths the router sends fewer word regions to OCR: on the first pages of 115 arXiv papers, the maths-font words with an unmapped glyph went from 1,219 to 16. Routing on the constructed tune and held-out splits wasn't rerun: none of their 600 files uses a TeX maths font.

## Routes given to ocr_crops (11 October 2026)

tools/ocr_crops.py takes `--routes=<file.jsonl>`: router-cli's lines, one per file, read from a file instead of from running router-cli. doc-router's reader passes the regions its plan recorded, so the crops it reads are the planned ones, whatever router is built when it reads. Without the option nothing changes: on the constructed tune split (300 files, 441 crops, 35,396 words) the output is the same as before, byte for byte apart from timings. On olmOCR-Bench's dev slice (284 files), doc-router's reads with the plan's routes have the same words and maths as reads routed by router-cli.

## Newer where-are-the-regions: tight type (11 October 2026)

Relocked at where-are-the-regions 6bd5b72, which splits words on tight type (its results/tight-type.md): letters set overlapping and spaces of about 0.14 of the font size had joined whole lines into one word. Routing is unchanged on the constructed tune and held-out splits (600 files, router-cli output identical) and on olmOCR-Bench's dev slice (284 files, the same routes; one image's under_text reason moved from 0.79 to 0.80).

## Crops rendered with PDFium (14 October 2026)

tools/ocr_crops.py renders its crops with PDFium (pypdfium2, Apache-2.0 / BSD-3-Clause) instead of PyMuPDF, which is AGPL-3.0. The rules are the same. The text's direction comes from each character's baseline as PDFium gives it, and a page that doesn't read as displayed is drawn turned rather than copied with a new /Rotate. A crop covers the pixels of the whole page at its dpi that the padded box covers, rounded outwards as MuPDF rounded them. A file PDFium can't open gets no crops and says so; MuPDF had opened one such file (a DST report) with no pages and stopped the run.

Crops are the same, their pixels not quite:

- On 23 files (rotated, sideways, scanned and born-digital pages; three boxes a page at 300 and 360 dpi), all 174 crops have the same size and position and take the same turn, and the 48 turned ones map word boxes back to the same points.
- Anti-aliasing differs, by a median of 2.6 grey levels a pixel. Tesseract reads some crops differently: on 56 crops of 27 scanned files, 75% of the words are the same text, and 14,183 words came back against 14,136.

So the check is accuracy, not identity:

| | PyMuPDF | PDFium |
|---|---|---|
| constructed tune split, routed: recall, precision, word error, recall in regions that need OCR | 99.68%, 99.86%, 0.45%, 98.80% | 99.69%, 99.87%, 0.44%, 98.81% |
| the same, Tesseract seconds | 317 | 259 |
| doc-router alone on 183 OmniParseBench pages it OCRs (761 tests), text dev / test | 17.5 / 15.8 | 20.7 / 17.0 |
| the same, tables | 22.2 / 22.1 | 22.2 / 19.1 |
| the same, layout | 47.8 / 40.0 | 50.7 / 46.7 |

On the constructed split the measures move by 0.01 points. On the OmniParseBench pages, 15 more of the 761 tests pass: 17 more text and layout tests, 2 fewer table tests. results/ocr-tune.jsonl is left as it was. Still on PyMuPDF, none of them on the OCR path: the scripts that build and check the sets (build_ocr_set.py, build_real.py, check_real.py, check_truth.py, samples.py) and score_route.py.
