# Detection Cleanup & Results

*ParaKit — Detection Research Notes · June 2026 (point-in-time snapshot, not maintained as the app evolves)*
*Findings marked **[CITED]** come from published literature; **[TESTED]** are my own measurements on real songs. Numbers are directional and reflect my specific test setup — not universal guarantees.*

> **Status — June 2026:** the cleanup described here is **validated in testing** and is being integrated into ParaKit in an upcoming update; this report documents the research behind it.

How ParaKit refines the raw detector output, and the measured impact.

## In brief
ParaKit's detector marks where drum hits occur and makes a first guess at the instrument. A second *cleanup* stage runs on that output — **without changing the detector itself** — to fix the most common mistakes: re-classifying cymbal hits (hi-hat / crash / ride) and removing "phantom" kick-drum hits that don't correspond to a real onset. On a set of songs the detector had never seen, the cleanup measurably improved cymbal accuracy — notably recovering ride-cymbal detection from nothing — with no loss on the instruments that were already accurate.

*(All scores are directional — the reference charts are human-quantized, ~50 ms timing.)*

## 1. The idea: clean up the output, don't re-tune the detector
Raw detection has reached the practical ceiling of the underlying model — many attempts to squeeze more out of the detector itself gave nothing. The reachable wins come from a **supervised cleanup pass** that reads the detector's output (plus the audio) and corrects it.

**Re-tuning the detector — exhausted:**
- threshold / gap sweeps for cymbals and toms — no safe gain (model-capacity ceiling)
- a single global sensitivity knob — net-negative
- per-instrument detection on separated stems — clearly worse
- swapping in an alternate model — input-format mismatch

**Post-detection cleanup — the lever:**
- operates on the detector's **output + audio** — the core detector is untouched
- supervised, onset-centered (~40 spectral features), validated **out-of-fold** (train and test on different songs)
- carries a **confidence gate** that protects instruments that are already accurate
- two shapes: **relabel** a misclassified hit (keep its timing) · **remove** a genuine phantom

> **The core detector is never modified.** The cleanup stage only *reads* the detector's output and the audio. Onset detection, the conversion routine, tempo/offset estimation, and chart building are unchanged — verified by independent review. This keeps the proven behavior intact while improving the parts that were weak.

## 2. The two cleanup tools
- **Cymbal re-classifier** *[TESTED]* — re-labels cymbal-lane hits among hi-hat / crash / ride. The detector has no dedicated "ride" class (it tends to call every cymbal a crash), so **ride can be recovered purely by re-classification**. A confidence gate is **lenient about moving hits into ride** (nothing to lose) but **strict on hi-hat↔crash swaps** (protecting what's already right).
- **False-kick remover** *[TESTED]* — removes detected kick hits that match **no real kick** anywhere in the reference. Deliberately conservative — it barely touches recall, because a missed real hit is worse than an extra one.
- **They stack** — the two tools act on different instruments, so their gains are additive.

## 3. The measured result (held-out songs)
Trained on one set of songs, scored on a separate set the models never saw. Accuracy = onset F-measure (higher is better). *[TESTED]*

| Instrument | detector alone | + cleanup | change |
|---|---|---|---|
| **overall (macro avg)** | 0.381 | 0.437 | **+0.056** |
| hi-hat | 0.495 | 0.578 | +0.083 |
| crash | 0.474 | 0.534 | +0.061 |
| **ride** | 0.000 | 0.247 | **+0.247** |

Every cymbal class improved; nothing regressed. Ride went from undetectable to usable — the single biggest gain.

## 4. Why ride was stuck — a *data* problem, not a method problem
For a long time ride accuracy looked capped no matter what I changed. Growing the test corpus from ~100 to ~450 songs broke that ceiling with the **same features** — proof the limit was the amount of training data, not the approach. *[TESTED]*

- ride accuracy, small corpus (~100 songs): **0.225**
- ride accuracy, larger corpus (~450 songs): **0.447** *(roughly doubled, same method)*

Hi-hat and crash improved too (+0.03 and +0.07). For your own use: a **clean, pre-separated drum stem** gives the best results — but the cleanup still helps on a full mix.

## 5. What I tried that I rejected
**Extending the re-classifier to every instrument: it backfired.** *[TESTED]*

It's tempting to generalize "re-label, don't delete" to all instruments. I built an all-instrument version and it **hurt** — every strong lane (kick, snare, hi-hat, crash) lost more than the tiny gain on the weak ones (overall −0.048). The strong instruments have everything to lose and little to gain from being second-guessed. **Lesson: keep the re-classifier specialized to the cymbals**, where the instruments are genuinely confusable.

## 6. How I kept it honest
- **Train/test split by song** — models are always scored on songs they never trained on, so the gains aren't a memorization artifact. (An early version had a subtle train-on-test leak; I caught and fixed it, and the numbers above are post-fix.)
- **Independent re-checks** — results were re-derived and adversarially reviewed before being trusted; one over-credit was caught and corrected.
- **Directional, not absolute** — reference charts are human-made and timed to ~50 ms, so treat these scores as direction-of-improvement, not lab-grade precision.
- **Scoring sanity-checked** — I verified the accuracy metric doesn't hand out "free credit" for simply not predicting a rare instrument.

> **Bottom line.** The cleanup pass — a specialized cymbal re-classifier plus a conservative false-kick remover, both running on the detector's output — is a real, measured improvement (overall +0.056; ride recovered from zero) with no regression on the instruments that were already accurate.

## Sources & tools
Primarily my own measurement, built on these open tools and reference datasets, and informed by the literature in [*What the Field Knows*](2-what-the-field-knows.md).

1. **ADTOF** — large real-music dataset behind the detector's training lineage. Zehren, Alunno & Bientinesi, ISMIR 2021. [arXiv:2111.11737](https://arxiv.org/abs/2111.11737)
2. **mir_eval** — onset matching & F-measure scoring (Raffel et al., ISMIR 2014). [github.com/mir-evaluation/mir_eval](https://github.com/mir-evaluation/mir_eval)
3. **madmom** — onset peak-picking conventions. [github.com/CPJKU/madmom](https://github.com/CPJKU/madmom)
4. **Demucs (htdemucs)** — drum-stem separation for full-mix inputs. [github.com/facebookresearch/demucs](https://github.com/facebookresearch/demucs)
5. **scikit-learn** — the random-forest classifier behind both cleanup tools. [scikit-learn.org](https://scikit-learn.org)
6. **librosa** — onset-centered spectral feature extraction. [librosa.org](https://librosa.org)
7. **Reference / evaluation datasets:** E-GMD, MDB-Drums, ENST-Drums, IDMT-SMT-Drums (full details in [*What the Field Knows*](2-what-the-field-knows.md)). ADTOF and ENST-Drums are non-commercial / research-only and were used only as internal evaluation references.
