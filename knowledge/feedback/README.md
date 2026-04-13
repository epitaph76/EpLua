# Future Feedback Capture

This folder is a placeholder for automatic saving of good generation examples.

## Why it lives here

`knowledge/feedback/` is close to the retrieval corpus, so accepted user-rated outputs can later be promoted into `knowledge/examples/` without mixing runtime code and stored knowledge.

## Suggested future flow

1. A generation finishes with a valid result.
2. The user marks the result as good.
3. The system builds a stable signature from:
   - task archetype
   - output mode
   - normalized input roots
   - normalized risk tags
   - a compact task fingerprint
4. The system checks existing accepted examples for a matching or near-matching signature.
5. If no close match exists, it stores a candidate record in `pending/`.
6. A later review or automated quality gate promotes the candidate into `knowledge/examples/`.

## Candidate record idea

Each saved record can include:

- `id`
- `task`
- `archetype`
- `output_mode`
- `input_roots`
- `risk_tags`
- `expected_outputs`
- `source_ref`
- `origin`
- `quality_signal`
- `signature`

## Duplicate control

To avoid saving the same hint many times:

- compare exact signatures first
- compare normalized task text second
- compare normalized generated code third
- reject candidates that only rename variables without changing the underlying pattern

## Folder sketch

- `pending/` for newly captured examples
- `accepted/` for reviewed examples ready to merge into retrieval
- `rejected/` for discarded duplicates or low-value captures
