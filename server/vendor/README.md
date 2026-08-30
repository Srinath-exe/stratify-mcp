# Vendored, not fetched

`lightweight-charts.js` — TradingView Lightweight Charts v5.0.9, Apache-2.0, zero runtime
dependencies. 182 KB raw, ~55 KB over the wire once nginx gzips it.

It is committed here rather than loaded from a CDN because the report is a single
self-contained file: it has to render inside an artifact sandbox that blocks external
requests, behind a corporate proxy, and after being saved to disk.

**The licence requires attribution on any user-facing page.** The chart is created with
`attributionLogo: true`, which draws TradingView's own corner link. Do not turn that off.
The library's `@license` header must also stay intact in the file above — do not re-minify
or strip it.
