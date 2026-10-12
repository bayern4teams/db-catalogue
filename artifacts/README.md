# Live test of the catalogue on real databases (2026-10-06 to 2026-10-12)

Start with **[live-test-report.md](live-test-report.md)**: results at a glance, failures first, then each section of [docs/LIVE_TEST.md](../docs/LIVE_TEST.md) in detail, limitations and open items.

For the engineer who will fix the app: **[engineer-fixes.md](engineer-fixes.md)** lists every proposed change (A1 to D9) with the code location and a check that fails before the fix and passes after. Supporting analysis: [candidate-trace.md](candidate-trace.md) (why the wrong relationships were proposed), [statistics-analysis.md](statistics-analysis.md) (why some statistics are empty), [model-questions.md](model-questions.md) and [model-questions-results.md](model-questions-results.md) (20 harder questions and how the app and the model did).

What was tested: PostgreSQL 16.15, ClickHouse 26.8.2.7 and MongoDB 7.0.41, read-only, plaintext on loopback; a private `Qwen3.8-Flash-Next` model served by llama.cpp. Branch `claude/gracious-bardeen-x1y6he`, commit `2e87fa7`. No source code was changed by the test.

Safety: these files contain catalogue metadata (table, column and collection names, types, statistics) and counts. They contain no row values, passwords, keys or connection strings. The repository is private; the names are your company's schema names.
