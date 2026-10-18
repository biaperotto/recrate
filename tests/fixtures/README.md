# Test fixtures

- `paper_text.txt`: a short Results-style passage with section headings, counts, and a
  percentage with a confidence interval. Synthetic: invented and authored for the claim
  extraction test. It is not taken from any real paper, and the numbers mean nothing.
- `claims_response.json`: a recorded model response for `paper_text.txt`, written by hand
  for the same test. Synthetic. Every `anchor` occurs verbatim in `paper_text.txt` and every
  `paper_says` value is printed there.
