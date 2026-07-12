# Serialization fuzz corpus

Shrunk Hypothesis counterexamples for the contract round-trip properties
(`tests/property/test_contract_roundtrip.py`: `render(parse(D)) == D` and
`parse(render(G)) == G`) land here as regression fixtures.

As of T3.7, no falsifying example has been found: T3.4's own suite ran at
`max_examples=500` with none found, and T3.7 additionally re-ran both
round-trip directions (`test_render_parse_round_trip_on_generated_documents`
and `test_parse_render_round_trip_on_generated_block_sets`) at
`max_examples=3000` each via a throwaway local script (not committed --
`tests/property/test_contract_roundtrip.py` itself is T3.4's sealed file and
was not edited) specifically hunting for a counterexample. None was found.

This directory is therefore empty of fixtures; keep the README so the
corpus path exists and the trivial fuzz-corpus test
(`test_fuzz_corpus_directory_present_and_green` in
`tests/golden/test_serialization.py`) stays green. When a future property
failure shrinks to a minimal example, commit it under this directory
(`fuzz/<short-name>/{input.md,note.md}`) and wire it into
`tests/golden/test_serialization.py`.
