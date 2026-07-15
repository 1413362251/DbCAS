# DbCAS literature search and screening methods

## Paper-ready draft

### Literature search and citation expansion

Candidate resources were identified using a multi-stage literature-mining workflow centred on Europe PMC. The search was performed on 1 July 2026 using the query `("alternative splicing" OR splicing)`. Searches were executed in overlapping publication-year windows by appending `PUB_YEAR:[y-1 TO y]` for each value of *y* from 2015 to 2026. Up to 2,000 records were retrieved per window through cursor-based pagination of the Europe PMC REST API (`resultType=core`; synonym expansion disabled). Because the implemented windows overlapped, the retrieved seed set covered publications dated 2014–2026. Records returned by Europe PMC were retained in API relevance order and deduplicated by case-normalized title.

To improve recall beyond papers directly retrieved by the keyword query, we performed backward citation expansion. For every seed article with a PubMed identifier, the complete reference list was obtained from the Europe PMC references endpoint. Unique cited records were defined initially by the combination of source database and source identifier. Core metadata—including title, abstract, DOI, PMID, PMCID, publication year, journal and citation count—were then retrieved from Europe PMC. Metadata were requested in batches of up to 50 identifiers grouped by source; identifiers absent from a batch response were re-queried individually. Duplicate records were resolved hierarchically using normalized DOI, PMID, PMCID and normalized title, in that order, and the record with the highest Europe PMC citation count was retained. The deduplicated cited-record set was combined with the seed set; cited-record metadata were retained preferentially and seed-only records were appended after identity-based deduplication.

### Deterministic candidate screening

Titles and abstracts were concatenated and screened with two case-insensitive regular-expression vocabularies. A record advanced only when it contained at least one resource term and at least one RNA/transcriptomics-scope term. Resource terms comprised *database*, *knowledge base/knowledgebase*, *dataset/data set*, *catalog/catalogue*, *biobank*, *atlas*, *portal*, *web server* and *resource(s)*. Scope terms covered RNA species; transcriptome and RNA-sequencing terminology; transcripts and isoforms; exons, introns, junctions and untranslated regions; alternative-splicing events and regulation; eQTLs and sQTLs; cDNA/EST and reference-transcript terminology; and RNA- or splice-related variant consequences. The generic term *annotation* was not used alone because pilot testing showed that it admitted many non-transcriptomic resources. Formally, a paper *p* passed this stage when

\[
K(p)=\mathbb{1}\{R(p)>0\}\,\mathbb{1}\{S(p)>0\}=1,
\]

where \(R(p)\) and \(S(p)\) denote the numbers of distinct resource and RNA/transcriptomics term matches, respectively. This stage was designed for high recall rather than final eligibility.

### Two-stage model-assisted abstract screening

Records with an abstract underwent two sequential model-assisted screens. The first screen used GPT-5.4-mini (OpenAI; `flex` service tier) as a high-recall triage classifier. Each title and abstract was assigned *Pass*, *Reject* or *Unclear*. A record was rejected only when it clearly did not describe a reusable public RNA/transcriptomics resource; both *Pass* and *Unclear* were forwarded to the second screen. Invalid or non-conforming model output was conservatively converted to *Unclear*. The screening prompt explicitly protected databases, atlases, catalogues, portals, web servers, curated or downloadable datasets, browsers and other reusable resources involving transcript annotation, isoforms, RNA sequencing, exon–intron structure, splicing, eQTLs/sQTLs or transcript-level variant consequences.

The second screen used GPT-5.5 through the OpenAI Responses API (`flex` service tier; reasoning effort set to `none`). It classified each retained title–abstract pair into one of seven mutually exclusive categories: direct reusable resource with access evidence; named reusable resource without an explicit URL; major public or consortium resource; reusable dataset or collection; software or web server hosting reusable data; use of an existing resource without release of a new resource; or non-eligible/insufficient evidence. Categories 1–5 were provisionally retained and categories 6–7 were excluded. The classifier also returned the resource name and type, whether an access location was mentioned, the inferred data scope and a short decision rationale. JSON outputs were schema-validated; invalid responses were retried up to two times, and failed records remained incomplete for later resumption rather than being silently excluded. A set of 191 previously curated resources was used to assess recall and to prioritize records for early review, but benchmark membership did not determine final inclusion.

Records without an abstract were not assigned a negative decision automatically. Instead, they were reviewed separately using the article title, DOI and identifier searches, followed by inspection of the publisher page, PubMed/PMC or Europe PMC record, the resource website, or available full-text evidence.

### Website verification and final eligibility

All provisionally retained candidates underwent evidence-based website verification under the versioned policy `transcript_splicing_v2`. A resource was finally eligible only when both of the following conditions were met: (i) the website provided meaningful record-level search, browse or query functionality; and (ii) the resource stored or organized at least one qualifying transcript-level or splicing data type. Qualifying data included alternative-splicing events, PSI values, exon or intron usage, splice sites or junctions, splicing regulation or sQTLs, transcript/isoform identifiers and exon–intron models, reference transcript annotations, full-length isoforms, or abundance quantified at transcript/isoform level.

Resources containing only gene-level expression, generic RNA associations or modifications without qualifying transcript structure, or incidental transcriptomic annotations were excluded. We also excluded paper-only descriptions, static or download-only collections without meaningful web interrogation, upload-only or prediction-only software, and tools that did not host reusable records. circRNA and alternative-polyadenylation resources were eligible only when qualifying junction, isoform or transcript-structure information was demonstrated. General reference databases, genome browsers and multi-omics portals were included only when their interfaces explicitly exposed qualifying transcript-level records.

For each candidate, curators checked the supplied URL and, when necessary, searched by resource name, article title, DOI and PMID. Evidence sources were prioritized as follows: official resource website, official documentation, resource publication and archived official website. Each decision was accompanied by one evidence URL, its source type, a concise evidence statement and the date checked. The same evidence had to support both interactive database functionality and at least one qualifying data type for an affirmative decision. Resource accessibility was recorded separately as live or dead; redirection, partial page failure or an access-verification page alone was not treated as evidence that a resource was dead. Structured consistency rules were applied before batch results could be merged, including controlled values, immutable record identifiers, complete evidence fields and agreement between the final decision, qualification basis and exclusion code.

### Reproducibility and audit trail

The workflow was implemented in Python using `requests`, `pandas`, `openpyxl`, `tqdm` and the OpenAI Python client. Each stage wrote an immutable tabular output and a CSV checkpoint. A run-specific JSON state file recorded completed years, PMIDs, reference identifiers, model-screened rows, web-curation batches, counts and failures, allowing interrupted runs to resume without repeating completed API calls. Network requests were globally rate-limited across worker threads, and checkpoint and state files were written only by the main thread. The search query, year windows, model names, service tiers, prompts, policy version and run parameters were preserved in the run configuration and output summaries.

## Verified flow counts from the principal search run

These counts are useful for a PRISMA-style diagram, but the notes below must be resolved before they are presented as the final study flow.

| Step | Verified count |
|---|---:|
| Seed articles after title deduplication | 16,212 |
| Reference links collected | 880,941 |
| Unique cited records after metadata deduplication | 376,263 |
| Combined seed-plus-reference candidate pool | 383,439 |
| Original deterministic keyword screen | 13,442 |
| Records with abstracts in the original keyword-screened set | 13,415 |
| Records without abstracts | 27 |
| Stage-1 Pass/Unclear | 4,665 |
| Stage-1 Reject | 8,750 |
| Initial Stage-2 records, including the 27 no-abstract reviews | 4,692 |
| Initial Stage-2 provisional inclusions | 3,112 |
| Expanded Stage-2 table after later seed-only and legacy additions | 5,509 |
| Expanded provisional inclusions | 3,340 |
| Candidates prepared for the first website-curation pass | 3,333 |

## Internal audit notes — resolve before submission

1. **Publication years.** The run configuration is labelled 2015–2026, but `build_year_query()` uses `PUB_YEAR:[y-1 TO y]`. The actual seed workbook contains 870 papers from 2014. The paper must therefore report 2014–2026, as drafted above, unless the search is rerun with single-year windows.
2. **Candidate-pool chronology.** The original Stage-1 run used 13,442 keyword-screened cited records. Seed articles were added to the candidate pool later, producing 14,250 keyword-screened records and separate seed-only Stage-2 results. The final Methods and flow diagram should either describe this as a protocol amendment or use results from a clean rerun in which seeds and cited records are combined before Stage 1.
3. **Stage-1 implementation.** The archived run proves that GPT-5.4-mini produced the reported `Pass`/`Reject` decisions. In the current code, however, `run_pipeline()` still calls the older binary `ai_screen_dataframe()` path, while the newer `ask_stage1_with_usage()` function is not wired into the orchestrator. Reproducibility requires either restoring the archived Stage-1 runner or integrating the triage function into the main pipeline.
4. **Stage-2 policy history.** The completed initial Stage-2 run used the broader cache key `dbc_stage2_transcriptomics_resource_v1`. The current strict policy is `transcript_splicing_v2`. The paper should state that the broad abstract screen generated candidates and that final inclusion was re-adjudicated under v2 website criteria; it should not imply that every abstract was originally classified with the v2 prompt.
5. **Final curation status.** The first 973 strict-v2 recuration records are complete, whereas the remaining 2,360-record strict-v2 run is not yet fully merged (69 of 95 chunks were complete at the time of this audit). Final included-resource counts must not be reported until the remaining website-verification batches and merge validation are complete.
6. **Legacy additions.** Fourteen legacy/forced candidate rows were appended during later merging. These rows were not automatically accepted by the final v2 policy, but their provenance and reason for addition should be stated explicitly in the study flow or supplementary methods.

## Compact algorithm for supplementary methods

```text
INPUT: Europe PMC query Q, target years Y, per-window limit L

1. For each y in Y:
       retrieve up to L records for Q AND PUB_YEAR:[y-1 TO y]
2. Deduplicate seeds by normalized title, preserving API relevance order.
3. For every seed PMID, retrieve the complete reference list.
4. Deduplicate reference targets by (source, external identifier).
5. Retrieve core metadata; deduplicate by DOI > PMID > PMCID > title,
   retaining the record with the highest citation count.
6. Combine cited records and seeds; deduplicate by the same identity hierarchy.
7. Keep records matching both a resource-term vocabulary and an
   RNA/transcriptomics-term vocabulary in title + abstract.
8. If an abstract is present:
       Stage 1: discard only definite Reject records;
       Stage 2: retain provisional classes 1–5 and reject classes 6–7.
   Else:
       route the record to targeted web/full-text review.
9. Verify each provisional candidate on official web sources.
10. Include only if interactive search/browse/query functionality AND a
    qualifying transcript/isoform/splicing data type are both evidenced.
11. Validate the structured decision and evidence fields before merging.

OUTPUT: evidence-backed, deduplicated DbCAS resource records.
```
