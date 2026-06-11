# Roadmap

This open-source drop is the **stable v4.x** ParaKit. A few things are in flight and will
arrive as follow-ups.

## ParaKit v5 (PySide6 rewrite)
ParaKit is being rebuilt from scratch on **PySide6 / Qt** (replacing Tkinter/TTK). The goals
are a modern, fully themeable UI and a cleaner, more maintainable codebase. **v5 is in an
early stage** and is **not part of this release**. The shipping, day-to-day app remains v4.x.

## UI Studio
**UI Studio** is a visual UI/layout designer — drag widgets, arrange tabs, theme the app.
It is built **for v5 (PySide6)** and depends on v5 code to run, so:
- It **cannot** edit or re-theme this v4.x (Tkinter) app.
- It is **not included** in this release.
- It will ship **alongside v5**, once v5 is far enough along. At that point you'll be able to
  use it to design for v5 or build your own custom ParaKit layout.

## RTX 50-series GPU build
A separate, **creator-verified RTX 50-series (Blackwell) build** with stem-splitter GPU
acceleration configured and tested out of the box is planned as a follow-up. The standard
build keeps its CPU fallback regardless, so stem splitting works on every machine. See
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

## Practice Window v2
The new Pygame-CE falling-note **Practice v2** (in `practice_v2/`) is an **alpha** still under
development. The stable Practice mode is **v1**, built into the main app. v2 is included so
the community can help mature it — or fork it. **However** there is the recently released
html version of the completed practice mode v2 redesign, it will be folded into the main app later.

---

*Have an improvement? Because the source is open (GPLv3), you're welcome to build on any of
the above rather than wait.*
