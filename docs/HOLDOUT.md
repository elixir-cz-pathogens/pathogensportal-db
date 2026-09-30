# Holdout — validation episodes that must not be touched

Declared on 2026-09-06 (phase 0 audit, issue #56). The point: a system tuned "until it catches the
known epidemic" is optimised for a single point, and its performance figures are worthless. So a
part of the documented episodes is frozen **in advance** and may be used only in the final
evaluation (gates C and D), never during development.

## Development episode (admittedly contaminated)

- **Pertussis 2024** (first signal 11/2023) — it was used when tuning the threshold floors and
  when designing the rules. Catching it is a necessary condition, but it is **not** evidence of
  quality.

## Holdout (do NOT touch these during development)

| Episode | Signal period (back-test v0.3.0) | Note |
|---|---|---|
| Acute hepatitis A | 2021-07 … 2025-12 (23 months, max 599 per month nationally) | ongoing epidemic |
| Scarlet fever [scarlatina] | 2022-10 … 2023-08 (11 months, max 973) | post-COVID rebound |
| Mumps [parotitis] | 2022-05 … 2024-08 (16 months, max 136) | multi-wave episode |

In the ISIN data the diagnoses are named `Akutní hepatitida A`, `Spála [scarlatina]` and
`Příušnice [parotitis epidemica]`.

Rules:

1. No decision about parameters, thresholds, weights or architecture is justified by behaviour on
   the holdout episodes.
2. Back-test listings over the holdout are neither generated nor quoted during development
   (aggregate figures over all series are fine — individual episodes are not).
3. The holdout is opened once, at the evaluation of gates C/D; the result is recorded whatever it
   turns out to be.
4. Changing this list requires a justification in the PR and means that the development so far
   may have been contaminated — this is then stated in every validation report.
