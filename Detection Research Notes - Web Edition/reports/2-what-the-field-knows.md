# What the Field Knows

*ParaKit — Detection Research Notes · June 2026 (point-in-time snapshot)*
*All findings here are drawn from published literature, attributed to their original authors and listed at the bottom. **[CITED]** throughout.*

A plain-language survey of published drum-transcription research — the background that motivates ParaKit's design.

## In brief
Automatic drum transcription is well studied. The standard pipeline is **detect onsets → classify the instrument → clean up the result**, and the cleanup lives in the last two stages (peak-picking and post-filtering) — so it can be improved without retraining the detector. Two themes recur across the literature: telling cymbals apart is the dominant source of error, and the headline accuracy score (F-measure) doesn't fully capture how good the result *sounds*.

> **The field's hardest-won lesson: the accuracy score is a leaky goal.** In a published listening test, adding note **velocity** (how hard each drum is hit) roughly **doubled** human preference (919 vs. 456 wins) while the classification accuracy score barely moved. Optimize for the score, but don't trust it alone — carry secondary signals (spurious-hit count, per-instrument error, timing distribution).

## 1. The cleanup stage — turning raw output into a clean chart

| Technique | What it does | When it helps / fails |
|---|---|---|
| Adaptive peak-picking | marks a hit only where the signal is a local maximum, exceeds a local average + threshold, and is far enough from the last hit | universal final step; too-low threshold catches false hits on decay tails, too-high misses ghost notes |
| Static low threshold | a fixed low cutoff on neural-network outputs (which are "spiky") | the common default for modern detectors; one global cutoff leaves accuracy on the table |
| Minimum-gap de-duplication | suppress a second hit within a short window of the first, per instrument | removes double-counts; set too wide it merges legitimate fast double-strokes |
| Per-instrument peak-picking | independent threshold + gap per instrument | cymbals and kick/snare have different statistics, so this reliably beats one global setting |
| Beat / grid quantization | snap hits to the nearest beat-grid position | cleans jitter — but depends on beat-tracker accuracy, and a hard snap can *hurt* |
| Musical-pattern model | a model of typical drum patterns nudges output toward plausible rhythms | measurably helps (one study: +10.8 points) but needs a drum-score corpus to train on |
| Segment-and-classify re-classifier | a small classifier re-labels a hit's instrument once an onset already exists | the highest-value cleanup lever for cymbals — and the approach ParaKit uses |

> **The caution the literature keeps repeating:** naively hard-snapping every hit to a grid frequently makes the result *worse* (unnatural). The systems that win fold the musical/structural prior in **softly** — using the grid as evidence to keep/drop and gently nudge timing, not as a hammer.

## 2. The public datasets — size, license & role
If you're building on this, dataset licensing matters: some are usable in a shipped app, some are research-only.

| Dataset | Size | Type | License | Role |
|---|---|---|---|---|
| **E-GMD** | 444 h, 43 kits | real e-kit → MIDI (first with velocity) | CC BY 4.0 | best for tuning at scale |
| **ADTOF** | 359 h, ~2,915 tracks | real polyphonic, auto-corrected charts | non-commercial | best realism at scale (research only) |
| **MDB-Drums** | ~0.4 h, 23 tracks | real polyphonic incl. vocals | open (CC) | small high-quality test set, rich cymbal labels |
| **ENST-Drums** | ~1.0 h, 3 drummers | recorded acoustic + minus-one | non-commercial | real cymbal variety + stems (research only) |
| **IDMT-SMT-Drums** | ~2.2 h, 104 loops | drum-only (kick/snare/hi-hat) | open | clean baseline + isolated stems |
| **Slakh2100** | ~145 h | synthesized from MIDI | CC BY 4.0 | huge synthetic set (domain gap vs real) |
| **STAR Drums** | 124.5 h | synthetic drums + recorded instruments | journal | recent, explicit 3/5/8/18-class maps |

> **License watch:** E-GMD, Slakh2100, MDB-Drums and IDMT-SMT-Drums are open/CC and usable in a shipped app. **ADTOF and ENST-Drums are non-commercial / research-only** — fine for internal evaluation, not for redistribution or commercial training claims.

## 3. Why cymbals are the hard part
Hi-hat, crash and ride share overlapping, noisy high-frequency spectra, often sound at the same instant as other hits, and ring out with long decays that smear into later frames. Across the literature, the cymbal classes score far below kick and snare — it's the dominant error source.

- kick / snare / hi-hat on clean drum-only audio: **0.80–0.95** (the easy cases)
- crash, ride and toms: **much lower**
- in one published full-system study: **crash 0.08**, ride 0.45, hi-hat 0.55–0.74 — crash is the hardest cymbal of all

A practical note for anyone comparing systems: most papers publish per-cymbal accuracy only as bar charts, not numeric tables — so a clean crash-vs-ride comparison is genuinely scarce in the literature (a reporting-convention gap; see the *Literature Review & Findings* report).

## 4. How results are measured — and where the metrics mislead
The standard metric is onset **F-measure** (a hit counts as correct if it lands within a tolerance window of a reference hit). Common pitfalls:

1. **Double-counting** — use one-to-one matching, or extra hits inflate the score.
2. **Tolerance hides timing** — a wide window (±50 ms) lets sloppy timing pass; also report a tight one (±25 ms).
3. **Out-precising the labels** — if reference timing is ~50 ms noisy, a stricter window measures noise, not skill.
4. **Class imbalance** — hi-hats dominate; a plain average hides cymbal failure — report per-instrument + macro average.
5. **Score ≠ perceptual quality** — the headline number can stay flat while the result audibly improves (e.g. dynamics).

**The three difficulty tiers:** drum-only audio (the easy lab case) → + other percussion (harder) → **full musical mix** (drums + melodic instruments — the realistic, hardest case, and the one a song-to-chart app actually faces).

## 5. Practical do / don't
**Do:** de-duplicate per instrument with a gap drawn from real fastest double-strokes · use per-instrument thresholds, not one global setting · use the beat grid to *adjudicate* keep-vs-drop and softly nudge timing · keep note velocity / dynamics (a cheap, large perceptual win) · evaluate at both ±25 ms and ±50 ms, per-instrument + macro, ideally across datasets.

**Don't:** hard-snap every hit to a grid as a naive last step · tune against the F-measure alone · merge below the real double-stroke speed · trust synthetic-tuned thresholds on real, mic'd drums · report a single averaged number and call it done.

## Sources & references
Every finding above is drawn from the following published work and datasets, attributed to their original authors.

1. Wu, Dittmar, Southall, Vogl, Widmer, Hockman, Müller & Lerch — "A Review of Automatic Drum Transcription", IEEE/ACM TASLP, 2018. [doi:10.1109/TASLP.2018.2830113](https://doi.org/10.1109/TASLP.2018.2830113)
2. Vogl, Widmer & Knees — "Towards Multi-Instrument Drum Transcription", DAFx 2018. [arXiv:1806.06676](https://arxiv.org/abs/1806.06676)
3. Vogl, Dorfer & Knees — "Recurrent Neural Networks for Drum Transcription", ISMIR 2016.
4. Cartwright & Bello — "Increasing Drum Transcription Vocabulary Using Data Synthesis", DAFx 2018 (source of the per-cymbal accuracy figures).
5. Callender, Hawthorne & Engel — "Improving Perceptual Quality of Drum Transcription with the Expanded Groove MIDI Dataset (E-GMD)", 2020 (the velocity listening-test result). [arXiv:2004.00188](https://arxiv.org/abs/2004.00188)
6. Ishizuka, Nishikimi, Nakamura & Yoshii — "Tatum-Level Drum Transcription … with Language Model-Based Regularized Training", 2020 (the +10.8-point pattern-model result). [arXiv:2010.03749](https://arxiv.org/abs/2010.03749)
7. Ishizuka, Nishikimi & Yoshii — "Global Structure-Aware Drum Transcription Based on Self-Attention", 2021. [arXiv:2105.05791](https://arxiv.org/abs/2105.05791)
8. Zehren, Alunno & Bientinesi — "ADTOF: A Large Dataset of Non-Synthetic Music for Automatic Drum Transcription", ISMIR 2021. [arXiv:2111.11737](https://arxiv.org/abs/2111.11737)
9. Zehren et al. — "Analyzing and Reducing the Synthetic-to-Real Transfer Gap … Automatic Drum Transcription", 2024. [arXiv:2407.19823](https://arxiv.org/abs/2407.19823)
10. "Noise-to-Notes: Diffusion-based Generation and Refinement for Automatic Drum Transcription", 2025. [arXiv:2509.21739](https://arxiv.org/abs/2509.21739)
11. **MIREX 2018 — Drum Transcription** task definition (evaluation tolerances).
12. **madmom** audio-processing library (peak-picking) — [github.com/CPJKU/madmom](https://github.com/CPJKU/madmom) · **mir_eval** (onset scoring) — [github.com/mir-evaluation/mir_eval](https://github.com/mir-evaluation/mir_eval)
13. **Datasets:** [E-GMD](https://magenta.tensorflow.org/datasets/e-gmd) · [MDB-Drums](https://github.com/CarlSouthall/MDBDrums) · [ENST-Drums](https://perso.telecom-paristech.fr/grichard/ENST-drums/) · [IDMT-SMT-Drums](https://www.idmt.fraunhofer.de/en/publications/datasets/drums.html) · [Slakh2100](http://www.slakh.com/) · [STAR Drums](https://doi.org/10.5334/tismir.244)
