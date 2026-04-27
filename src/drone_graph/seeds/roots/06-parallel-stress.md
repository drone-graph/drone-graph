# Intent

Build a small report comparing four CSV files against an HTTP API. The
report goes at `var/parallel-stress/report.md`.

The CSVs to fetch:

  1. https://example.com/data/sales-2024-q1.csv
  2. https://example.com/data/sales-2024-q2.csv
  3. https://example.com/data/sales-2024-q3.csv
  4. https://example.com/data/sales-2024-q4.csv

For each CSV: download it, summarize by region, and append a section to the
shared report file. Cross-reference one figure per quarter against the live
API at https://example.com/api/quarter-summary. Several sections must run
concurrently; the report file is the natural contention point.

Sub-work that must happen at least once:

  - install `requests` and `pandas` (each only needed once across the swarm)
  - run a long-running validation (≥60s) against the assembled report
  - capture HTTP fetch failures into the report instead of crashing the run

# Criteria

Filled when:

  - `var/parallel-stress/report.md` exists with one section per quarter
  - each section names the source CSV and at least one summary figure
  - the validation step has produced a `var/parallel-stress/validated.flag`
  - any HTTP/CSV failures are captured in a "## Failures" section rather
    than aborting the run
