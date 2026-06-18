# Literature Review & Findings

*ParaKit — Detection Research Notes · June 2026 (point-in-time snapshot)*
*Findings marked **[CITED]** come from published literature; **[TESTED]** are my own measurements on real songs.*

A close read of 31 drum-transcription papers, cross-checked against my own testing on real songs.

I reviewed the full set of papers connected to a key multi-instrument drum-transcription study, then checked each idea against ParaKit's own measurements. The short version: the published field **broadly supports** the approach ParaKit already takes, it surfaced no ready-made new cleanup technique, and the one promising idea I tested most carefully **did not pan out** — which is itself a useful result.

## 1. What the literature confirms about ParaKit's approach
Positions ParaKit arrived at through its own work, independently supported by published research:

| Design choice in ParaKit | Supporting published evidence |
|---|---|
| **Ride cymbal is hard because of limited data, not a flawed method** | Across datasets, toms and crash/ride score worst — repeatedly linked to how rarely those instruments appear in training data. One widely-used dataset even merges crash and ride into a single label, so a separate "ride" class barely exists to learn from. *[CITED]* |
| **Telling cymbals apart is the dominant error** | Hi-hat / crash / ride share overlapping, noisy spectra and long decays; multiple studies report cymbal classes scoring far below kick and snare. *[CITED]* |
| **Prefer keeping a hit (recall) over dropping it; relabel rather than delete** | Aggressively cleaning output to raise precision reliably costs more recall than it gains — overall accuracy drops. Modern neural detectors are naturally high-recall and precision-limited. *[CITED]* |
| **The cleanup stage is the right lever (vs. retraining the detector)** | Beyond a point, more data doesn't beat model capacity. A documented system improved purely by removing false positives from an existing model's output — structurally the same idea as ParaKit's false-kick remover. *[CITED]* |
| **Musical "feel" matters more than the headline accuracy score** | In a listening test, adding note-velocity (dynamics) roughly **doubled** human preference while the standard accuracy score barely moved — "classifier metrics don't fully align with perceptual quality." *[CITED]* |
| **Confidence-gated, per-instrument decisions beat one-size-fits-all** | A per-instrument confirmation gate lifted precision substantially, and combining a classifier with such a gate beat either alone. *[CITED]* |

## 2. How hard is each cymbal? The actual numbers
Per-instrument accuracy (F-measure) from a full transcription system, published study (higher is better). *[CITED]*

- closed hi-hat: **0.74**
- open hi-hat: **0.55**
- ride: **0.45**
- crash: **0.08**

**Crash is the hardest cymbal everywhere.** Most studies report these only as bar charts, not numeric tables — so a clean crash-vs-ride comparison is genuinely scarce in the literature, a reporting-convention gap rather than missing knowledge.

## 3. What I tested that did *not* work
**Adding decay-shape features: no measurable improvement.** *[TESTED]*

The literature suggested cymbals separate on their decay/sustain shape — so I built and measured the features that capture it (attack-to-decay ratio, decay slope, sustain length, multi-window spectra) and re-measured on 456 songs using a strict song-separated test (train on some songs, score on songs never seen). Every variant landed **within normal run-to-run noise** — no real gain. Combining the new features was *worse* than the best single one. **I kept none of them.** The takeaway matches an earlier finding: ParaKit's ride-cymbal weakness was a *data* problem (fixed by testing on more songs), not a missing-feature problem — the existing features already capture the available decay signal.

*Documenting this dead-end on purpose: a negative result, measured carefully, saves the next person from re-running it.*

## 4. Methodology worth borrowing
- **Test on unseen songs, not unseen hits** — timbre features can look great when test hits come from songs the model trained on, then collapse on genuinely new recordings. Always split by song. *[CITED]*
- **More data helps — even imperfect data** — a large auto-aligned dataset improved accuracy as lower-quality tiers were added in; even the noisiest tier still helped. *[CITED]*
- **Score the metric honestly** — a naïve averaging scheme can hand "free credit" for not predicting a rare instrument. I checked my own scoring is free of this. *[TESTED]*
- **Match timing tolerance to the data** — if your reference timing is noisy, a too-strict matching window measures noise, not skill. Common practice: a ~50 ms tolerance. *[CITED]*

## 5. Honest limits of this review
- Most per-instrument cymbal accuracy in the field is published as charts, not numbers — so cross-study comparison is approximate.
- Some sources were paywalled; where a number couldn't be verified at the source, it's flagged rather than asserted.
- My own numbers reflect ParaKit's specific test setup and corpus; treat them as directional, not universal.
- Several promising directions (musical-pattern priors that disambiguate cymbals by rhythmic context; using note dynamics/velocity) remain **open and untested** here — flagged as future work, not results.

> **Bottom line.** The published field and my own testing point the same way: the win is in the **cleanup stage** (re-classifying cymbals, removing phantom hits), the cymbal problem is fundamentally about **data**, and "feel" (dynamics, musical plausibility) is an under-used quality lever worth pursuing next.

## Sources & references
Key works among the 31 reviewed (full reference details in [*What the Field Knows*](2-what-the-field-knows.md)):

1. Vogl, Widmer & Knees — "Towards Multi-Instrument Drum Transcription", DAFx 2018. [arXiv:1806.06676](https://arxiv.org/abs/1806.06676)
2. Wu et al. — "A Review of Automatic Drum Transcription", IEEE/ACM TASLP 2018. [doi:10.1109/TASLP.2018.2830113](https://doi.org/10.1109/TASLP.2018.2830113)
3. Cartwright & Bello — "Increasing Drum Transcription Vocabulary Using Data Synthesis", DAFx 2018 (per-cymbal accuracy figures).
4. Callender, Hawthorne & Engel — "Improving Perceptual Quality … (E-GMD)", 2020. [arXiv:2004.00188](https://arxiv.org/abs/2004.00188)
5. Zehren, Alunno & Bientinesi — "ADTOF", ISMIR 2021. [arXiv:2111.11737](https://arxiv.org/abs/2111.11737)
6. Wei, Wu & Su — "Improving Automatic Drum Transcription Using Large-Scale Audio-to-MIDI Aligned Data", ICASSP 2021.
7. Weber, Uhle, Müller & Lerch — "STAR Drums: A Dataset for Automatic Drum Transcription", TISMIR 2025. [doi:10.5334/tismir.244](https://doi.org/10.5334/tismir.244)
8. Wang, Salamon, Cartwright, Bryan & Bello — "Few-Shot Drum Transcription in Polyphonic Music", 2020. [arXiv:2008.02791](https://arxiv.org/abs/2008.02791)
9. Plus the peak-picking, NMF, data-augmentation and pattern-prior works referenced in [*What the Field Knows*](2-what-the-field-knows.md).
