# Retrieval eval questions

Corpus: Aditya Birla "Group Activ Health" insurance certificates
(`docs/InsuranceFather.pdf`, and `docs/InsuranceMother.pdf` once uploaded).
Ask these from the ingestion-ui Chat tab. Baseline (old parser): 7/10.

Reconstructed from `RAG_IMPROVEMENT_PLAN.md` + `section_header_problem.md`
after the HybridChunker parser rewrite — treat Q1, Q2, Q7, Q11, Q12 as the
frozen regression core.

## Text only

| # | Question | What it tests |
|---|----------|---------------|
| Q1 | What is the premium? | Header-bias fix: premium table must beat the bare "Premium Details" heading (was 0.045 vs 0.971) |
| Q2 | What is the coverage? | Same fix, "Coverage Details" table |
| Q3 | What is the sum insured? | Table retrieval variant |
| Q4 | What is the master policy number? | Form-label merging ("Master Policy Number:" was a bare chunk) |
| Q5 | What is the certificate number? | Form-label merging |
| Q6 | Who is the insured person and what are their details? | "Insured Person Detail" section merging |
| Q7 | Who signed the certificate? | "Authorized Signatory" appeared as 4 bare chunks; must now be merged into context |
| Q8 | What is the policy period / start and end date? | Form fields ("Date:", "Place:") |
| Q9 | How do I raise a grievance? | "Grievance Redressal" section |
| Q10 | What are the steps for cashless claims? | FAQ sections (duplicated "a) Start by downloading..." blocks) |

## Text + image

Answer should embed a figure via `[figure:HANDLE]` token → inline image in UI.

| # | Question | What it tests |
|---|----------|---------------|
| Q11 | Show me the health insurance ID card. | Card art on pages 7-8; known-hard — expected to fail until page-region crops (plan Step 3) are implemented |
| Q12 | What details are shown on the front side of the ID card? | Must be grounded in card caption; must NOT list nominee / sum insured (caption context-blending, F3) |
| Q13 | Show me the signature on the certificate. | Signature graphic on page 1; decorative-crop filtering (F4) |
| Q14 | Is there a company logo in the document? Describe it. | Noise-image handling — tiny logo crops used to get impossible captions |

## Cross-document (requires InsuranceMother.pdf uploaded to docs/)

| # | Question | What it tests |
|---|----------|---------------|
| Q15 | What is the premium for the father? | Doc-scoped retrieval, duplicate pollution (F5) |
| Q16 | What is the premium for the mother? | Same |
| Q17 | Who signed the father's certificate? | Must not blend both documents' signatures (original Q6 failure) |

## Scoring

Track per question: (a) retrieval hit — did the gold chunk rank top-1/top-3;
(b) answer faithfulness — is the answer grounded in the retrieved passages.
Log results in `RAG_IMPROVEMENT_PLAN.md` → "Results log".
