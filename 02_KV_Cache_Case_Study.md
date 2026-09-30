# KV-Cache Case Study: Feasibility Test and Analysis

Report date: 30 September 2026.

## Research Question

> How do architectural interventions in autoregressive language models alter KV-cache or equivalent persistent-state requirements, and what efficiency, quality and adaptation trade-offs are supported by primary studies?

The review window is 1 January 2017–30 September 2025. Eligible primary studies concern attention parameterisation, KV representation or sharing, learned memory updates, or recurrent and hybrid state for autoregressive human-language generation. Pure serving, offloading and paging methods, and secondary reviews, fall outside the core architectural synthesis.

The test examines whether available metadata and public full texts can support this review, and whether decisions and extracted information can be linked to primary sources. Therefore, no specific agent framework was designed for this project; it was completed by ChatGPT 6 Astra.

## Sources and Provisional Results

Discovery used the September 2025 OpenAlex snapshot, four completed arXiv API query families, and recorded source follow-ups. The snapshot scan covered 271,335,309 raw records, returning 8,625 candidates. The arXiv supplement contains 1,507 records.

Merging and provisional report grouping produced the following checkpoint. Counts refer to report units; study-family and version reconciliation is still required.

| Stage | Count |
|---|---:|
| Merged report units | 8,847 |
| Excluded at title screening | 7,856 |
| Assessed at abstract screening | 991 |
| Excluded at abstract screening | 462 |
| Full text sought | 529 |
| Files acquired | 465 |
| Retrieval unresolved | 64 |
| Full-text decisions completed | 56 |
| Currently included | 33 |
| Excluded after full-text assessment | 23 |
| Awaiting full-text assessment | 473 |
| Formal extraction and seven-domain appraisal completed | 1 |
| Other included records awaiting extraction | 32 |

File acquisition reached **465/529 (87.9%)**, comprising 375 PDFs and 90 HTML documents. Eight PDFs remain flagged for date checks. This rate describes the current queue; it does not measure field-wide retrieval recall or final inclusion. Document identity, eligible version and readable evidence require further checks.


## What the Test Establishes

Public full-text acquisition is feasible at this scale for the selected computer-science topic. OpenAlex can provide the initial candidate set, while supplementary searches and publisher or repository routes recover additional documents.


## Next Validation Steps

1. Resolve retrieval gaps, document versions and report families.
2. Complete full-text decisions and extraction for the final included set.
3. Manually check screening labels, extracted fields and numerical claims.
4. Run the acquired documents through the application and inspect the complete evidence chain.
5. Measure quality, runtime, cost and human correction effort against a fixed workflow.
