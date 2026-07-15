# Transcript-Level Searchable Database Curation Prompt

You will receive an Excel file with one row per candidate biological database or resource.

Your task is to verify whether each candidate is a transcript-level searchable database, check whether its website is accessible, and fill the required metadata fields.

## Core Scope

This task uses screening policy `transcript_splicing_v2`.

We only want databases with searchable, browsable, or queryable database functionality and transcript-level or splicing information.

Gene-level expression alone is not sufficient. A resource must provide transcript-level, isoform-level, alternative-splicing, exon-usage, junction-usage, splice-site, transcript-annotation, transcript-sequence, transcript-abundance, or other transcript-level information to qualify.

There is no exception for primary transcriptomics atlases, consortium resources, reference databases, or legacy/original entries. A gene-level matrix, RNA-seq-derived cell taxonomy, or general transcriptomic profile is still insufficient unless the resource exposes qualifying transcript/isoform/splicing content.

Online transcript/splicing analysis servers can qualify when they provide built-in searchable/browsable transcript-level content, such as gene/transcript lookup against reference annotations, transcript paths, splice graphs, exon/S-exon tables, alternative-splicing event tables, or reusable example/supplementary result databases. Do not mark such a resource `no` merely because it also provides software or runs an analysis pipeline online. Mark `no` only when it is upload-only/software-only and does not expose hosted or reference-backed transcript-level records or browsable results.

Set `db_type_confirmation = yes` only if the resource satisfies both requirements:

| Requirement | Meaning |
|---|---|
| Searchable database requirement | The resource is a database, portal, atlas, browser, searchable repository, or web resource with search, browse, or query functionality. |
| Transcript-level requirement | The resource itself stores, organizes, searches, browses, or serves transcript-level information, such as transcripts, isoforms, transcript IDs, transcript structures, alternative splicing, splice events, exon usage, junction usage, splice sites, transcript-level abundance/expression, full-length transcripts, long-read isoforms, or transcript-level annotations. |

For every `yes`, fill `qualification_basis` with one or more of these values, separated by `;`:

| Qualification basis | Required evidence |
|---|---|
| `splicing_event` | Alternative-splicing events, PSI, exon skipping, intron retention, or other event-level records. |
| `splice_site_or_junction` | Splice sites, splice junctions, junction usage, or back-splice junctions. |
| `splicing_regulation_or_sqtl` | Splicing regulation, splicing-factor relationships, sQTLs, or splice-affecting variants with transcript consequences. |
| `transcript_or_isoform_model` | Transcript/isoform IDs, exon-intron models, transcript structures, reference transcript annotations, transcript sequences, or full-length isoforms. |
| `transcript_level_abundance` | Abundance or expression explicitly quantified by transcript/isoform rather than only by gene. |

For every `no`, write `qualification_basis = not_applicable` and select one controlled `exclusion_code`. Gene expression, generic RNA records, or an RNA-related name never substitute for qualifying evidence.

Set `db_type_confirmation = no` if the resource is only one of the following:

| Excluded resource type | Rule |
|---|---|
| Static dataset only | A static data file or supplementary dataset without searchable, browsable, or queryable database functionality. |
| Paper only | A publication that describes data but does not provide a database or searchable web resource. |
| Software only | A tool, package, pipeline, or algorithm without database content. |
| Download-only or catalog-only | A resource that only provides downloadable files, static catalog pages, FTP directories, supplementary tables, or release files, without meaningful web search, browse, query, or record-level pages. Transcript-level data are not enough if users cannot search, browse, or query them in the resource website. |
| Not transcript-level | A resource that does not contain transcript-level, isoform-level, alternative-splicing, exon-usage, junction-usage, splice-site, transcript-annotation, transcript-sequence, or transcript-abundance information. |
| Gene-expression-only | A resource that provides only gene-level expression, differential gene expression, expression correlation, expression survival association, expression-mutation association, or expression-based immune/clinical analysis without transcript-level information. |
| Generic RNA but not target-level | A miRNA/lncRNA target, disease-association, interaction, RNA-editing, RNA-modification, RNA-family, or other RNA resource without qualifying transcript models or splicing content. |
| Downstream analysis webserver only | A webserver whose primary purpose is immune infiltration, survival analysis, mutation analysis, clinical association, pathway analysis, enrichment, drug response, or other downstream analysis. Mark `no` if gene expression is only an input, covariate, comparison module, or visualization layer rather than central transcript-level database content. TIMER, TIMER2.0, and TIMER3 are bad examples and should be `no` under this rule. |
| Transcription dynamics only | A resource focused on transcription speed, transcription dynamics, or transcription regulation only, without transcript-level, isoform-level, splicing, exon-usage, junction-usage, or transcript-abundance data. |
| Overlay or upload analysis only | A pathway, protein, PPI, chemical, disease, or general knowledge database that only lets users upload expression data, overlay expression values, or run transcriptomics enrichment, but does not itself host a searchable transcript-level database. |

Multi-omics databases are acceptable only if they include transcript-level information. Gene-level expression as one omics layer is not enough.

circRNA and alternative-polyadenylation resources are not automatic positives. A circRNA resource must expose back-splice junctions, isoforms, or transcript structures. An APA resource must expose qualifying transcript/isoform structures or another allowed basis; cleavage-site records alone are not sufficient under this policy.

General reference databases, genome browsers, and broad atlases can be acceptable. Do not reject resources such as RefSeq, GENCODE, UCSC Genome Browser, Ensembl, TAIR, FlyBase, Expression Atlas, or GTEx merely because they are broad or general-purpose. Mark them `yes` if they provide searchable, browsable, or queryable transcript-level records or views, such as transcript IDs, transcript models, isoforms, exon/junction/splicing data, or transcript-level expression/abundance.

Atlas resources are acceptable if they include searchable, browsable, or queryable transcript-level data. For example, an atlas with transcript/isoform expression, exon or junction usage, long-read transcript data, or transcript-level annotations can be `yes`. An atlas with only gene-level expression should be `no`.

Pathway, protein, PPI, chemical, drug, compound, phenotype, disease, immune-infiltration, survival-analysis, clinical-analysis, and general annotation databases should default to `db_type_confirmation = no` unless their own database content includes a substantial searchable transcript-level module. Do not mark a broad database as `yes` only because it has gene names, pathway diagrams, RNA as a molecule type, RNA-seq raw counts at gene level, gene expression modules, differential gene expression, expression correlation, survival-by-expression analysis, expression upload tools, expression overlays, enrichment analysis, or a small incidental expression-related feature.

A dead website can still have `db_type_confirmation = yes` if reliable evidence shows that it was a searchable transcript-level database.

## Final Stage 8 Database-Level Review

After row-level curation, Stage 8 performs one database-level finalization pass. Automated URL checks are triage only; every surviving canonical `db_type_confirmation = yes` database must be opened and inspected by an Agent in a browser before publication.

Only `yes` rows are database-level deduplication candidates. Group candidates using normalized database names, reliable aliases, normalized URLs, and confirmed redirect destinations. Never merge records merely because they share a generic host such as NCBI, EBI, UCSC, GitHub, Zenodo, or Dryad.

For each candidate group, assign an audit-only `canonical_id`:

- Rows referring to the same database receive the same `canonical_id` and are merged.
- False-positive groups must be split into different canonical IDs.
- If one database appears in multiple papers, choose one representative paper. Prefer the formal paper for the current or updated database version; otherwise choose the database paper that most completely proves searchable functionality and qualifying transcript/splicing content. Break remaining ties by DOI/PMID completeness, newer publication year, then smaller original ID.
- The final `id`, `title`, `doi`, `pmid`, and `year` must all come unchanged from the selected representative row. Do not synthesize a mixed publication identity.
- If any group member has `original_48_database = yes`, the canonical row retains `yes`.
- Normalize the canonical database name, current direct official database URL, classification, metadata, and evidence as needed.

`canonical_id` and merge explanations belong only in `08_duplicate_merge_audit.xlsx`; do not add them to the published 30-column workbook. After review, run duplicate detection again and resolve any newly created `yes` duplicate groups. `no` rows are not merged by this database-level rule.

For an incremental cross-chunk duplicate table, keep all identity/candidate columns unchanged and fill the six audit decision fields. Use `incremental_action=merge` only when the rows are the same database; give every member the same incremental canonical ID and representative ID. Use `incremental_action=split` for a false candidate; give every row its own canonical ID and itself as representative. Each row needs a decision statement of at least 20 characters. Missing or inconsistent incremental decisions block final publication.

## Required Output Columns

Use exactly these columns and preserve this order:

Do not rename, reorder, add, or delete columns. In Excel output, keep the original header text exactly as provided.

| Column header | Field meaning |
|---|---|
| `<main,t-word-id> id` | Original row ID. Do not modify. |
| `<main,t-word> title` | Publication title. Do not modify unless clearly corrupted. |
| `<main,t-word> database_name` | Candidate database or resource name. Fill missing values when reliable evidence is found. |
| `<main,t-word-url> database_url` | Mutable current official database URL. Follow redirects/interstitials and replace an obsolete URL with the stable direct database URL whenever possible. |
| `<sub,t-word-doi> doi` | DOI. Fill missing values when reliable evidence is found. |
| `<sub,t-word-pmid> pmid` | PMID. Fill missing values when reliable evidence is found. |
| `<sub,t-numeric> year` | Publication year. Fill missing values when reliable evidence is found. |
| `<main,t-bool> original_48_database` | Source marker from the curated input: `yes` or `no`. Do not modify and do not use it to override screening. |
| `<sub,t-word-tag> screening_policy_version` | Must remain `transcript_splicing_v2`. Do not modify. |
| `<main,t-word-tag> accessibility` | Current website accessibility: `live` or `dead`. |
| `<main,t-bool> db_type_confirmation` | Whether the resource meets the transcript-level searchable database requirement: `yes` or `no`. |
| `<sub,t-word> confirmation_reason` | Short reason for the `db_type_confirmation` decision. Keep it within about 30 words. |
| `<main,t-word-tag> qualification_basis` | Semicolon-separated qualifying basis values for `yes`; `not_applicable` for `no`. |
| `<main,t-word-tag> exclusion_code` | `not_applicable` for `yes`; one controlled exclusion code for `no`. |
| `<sub,t-word-url> evidence_url` | One HTTP(S) source URL supporting the decision. A live row must use an official page that was actually opened; a dead row must use its database publication. |
| `<sub,t-word-tag> evidence_source_type` | Schema values are `official_database`, `official_documentation`, `publication`, or `web_archive`; finalization requires official page evidence for live and publication evidence for dead. |
| `<sub,t-word> evidence_statement` | A 20–300 character source-faithful statement supporting the decision. For `yes`, it must prove both searchable database functionality and qualifying content. |
| `<sub,t-word> evidence_checked_date` | Date the source was checked in `YYYY-MM-DD` format. |
| `<main,t-bool> manual_review_needed` | `yes` only for a high-priority `no` caused by insufficient evidence; otherwise `no`. |
| `<main,t-word-tag> neural_link` | Brain or neural relevance: `primary`, `partial`, or `none`. |
| `<main,t-word-tag> focus` | Transcript-level focus: `AS_focused`, `transcriptomics_general`, or `unknown`. |
| `<main,t-bool> gene_expression_available` | Whether gene-level expression data are available in addition to qualifying transcript-level information: `yes` or `no`. |
| `<main,t-word-tag> species` | Species covered by the database. Use `;` to separate multiple values. |
| `<sub,t-word-tag> disease_association` | Disease associations if present. Use disease names separated by `;`. |
| `<sub,t-word-tag> developmental_association` | Developmental stages if present. Use `;` to separate multiple values. |
| `<main,t-word-tag> tissue_or_brain_region` | Tissues, organs, or brain regions. Use `;` to separate multiple values. |
| `<sub,t-word-tag> cell_type` | Cell types. Use `;` to separate multiple values. |
| `<main,t-word-tag> sequencing_resolution` | Sequencing or assay resolution: `bulk`, `single_cell`, or both as `bulk;single_cell`. |
| `<main,t-word-tag> read_technology` | Read technology: `short`, `long`, or both as `long;short`. |
| `<sub,t-word> visualization_methods` | Plain text description of visualization methods. Use `;` to separate multiple methods. |

## Controlled Values and Field Rules

Use `;` to separate multiple values in all list-like fields.

## Missing Value and Specificity Rules

Use specific values whenever reliable information can be found. Do not use vague aggregate placeholders.

| Rule | Requirement |
|---|---|
| Unified missing value | Use lowercase `unknown` when information cannot be verified, is absent, or is not applicable after reasonable searching. Do not use blank cells, `none`, `Unclear`, `NA`, `N/A`, or `not specified` for missing metadata. |
| Only allowed `none` use | The only allowed `none` value is `neural_link = none`. It means that a dedicated check of the official database, documentation, and database publication found no explicit brain or neural content; it must not be used merely because the main database evidence does not mention brain. Do not use `none` in other final output fields. |
| No vague aggregate labels | Do not write values such as `multiple species`, `various species`, `many species`, `multiple tissues`, `various tissues`, `multiple cell types`, or `various cell types`. This rule applies to species, disease, developmental stage, tissue/brain region, cell type, and visualization fields. |
| Species specificity | For `species`, write actual species names or a specific official taxonomic scope found on the site. If species coverage cannot be verified, write `unknown`. Never write `none`, `multiple species`, or similar vague text. |
| Tissue and cell specificity | For `tissue_or_brain_region` and `cell_type`, list actual tissues, regions, or cell types when available. If not verifiable, absent, or not applicable, write `unknown`. |

| Field | Allowed values | Rule |
|---|---|---|
| `accessibility` | `live`; `dead` | Use `live` if the database is currently accessible. Use `dead` only if the website is truly unavailable after reasonable attempts. |
| `db_type_confirmation` | `yes`; `no` | Use `yes` only for searchable, browsable, or queryable databases that themselves host or organize transcript-level information. This field is not about whether the website is alive. Use `no` for gene-expression-only resources and for broad pathway/protein/PPI/chemical/immune-infiltration/survival/clinical/general annotation databases that only support gene expression modules, expression upload, expression overlay, enrichment analysis, downstream association analysis, or incidental gene/RNA references. |
| `confirmation_reason` | Short plain text | Give the shortest useful reason for the yes/no decision, within about 30 words, such as `searchable isoform records`, `gene expression only`, `download-only files`, or `immune analysis webserver`. |
| `qualification_basis` | Allowed basis values separated by `;`, or `not_applicable` | Required for `yes`. Use `not_applicable` for every `no`. Do not use broad labels such as `RNA database` or `transcriptomics atlas`. |
| `exclusion_code` | `not_applicable`; `gene_expression_only`; `rna_not_transcript_splicing`; `no_search_browse_query`; `download_or_static_only`; `software_or_upload_only`; `paper_only`; `insufficient_evidence` | Use `not_applicable` for `yes`; use exactly one other value for `no`. |
| `evidence_url` | One HTTP(S) URL | For `accessibility=live`, use the official database page or official documentation that the Agent actually opened after all required interstitials. For `accessibility=dead`, use the corresponding database publication. Search results, unsupported secondary summaries, warning/interstitial pages, and Web Archive pages are not valid final evidence. |
| `evidence_source_type` | `official_database`; `official_documentation`; `publication`; `web_archive` | Finalization accepts `official_database` or `official_documentation` only for live rows and `publication` only for dead rows. `web_archive` may aid historical investigation but must not remain the final evidence source. |
| `evidence_statement` | 20–300 characters | Use a short direct excerpt or faithful paraphrase. A `yes` statement must identify both the search/browse/query function and at least one allowed qualification basis. |
| `evidence_checked_date` | `YYYY-MM-DD` | Record the date on which the evidence URL was checked. |
| `manual_review_needed` | `yes`; `no` | `yes` is allowed only when `db_type_confirmation=no` and `exclusion_code=insufficient_evidence`. Use it only for the strongest unresolved candidate in the chunk. |
| `neural_link` | `primary`; `partial`; `none` | Use `primary` when brain, nervous system, neural tissue, neural cell types, neurodevelopment, or neurological disease is the main database scope. Use `partial` for a general resource that explicitly exposes searchable or displayed brain/neural tissue, region, cell-type, disease, transcript, isoform, or splicing evidence. Use `none` only after the dedicated neural check below finds no explicit neural content. Generic ability to search an arbitrary neural-gene symbol is not sufficient by itself. |
| `focus` | `AS_focused`; `transcriptomics_general`; `unknown` | Use `AS_focused` if alternative splicing, splice events, isoforms, exon usage, junction usage, splice sites, splicing factors, splicing QTLs, or AS-level analysis are central. Use `transcriptomics_general` if qualifying transcript-level information is present but AS is not central. Use `unknown` when `db_type_confirmation = no` or focus cannot be verified. Do not use `transcriptomics_general` for gene-expression-only resources. |
| `gene_expression_available` | `yes`; `no` | Use `yes` if gene-level expression data are available in addition to qualifying transcript-level data. Use `no` if only transcript-level data are available, or when `db_type_confirmation = no`. This field does not affect `db_type_confirmation`; gene expression alone is still `db_type_confirmation = no`. |
| `species` | Specific species/taxon tags, or `unknown` | Write actual species names such as `Human`, `Mouse`, or `Human;Mouse`, or a specific official taxonomic scope if exact species are too broad to enumerate. If species cannot be verified, write `unknown`. Never write `none`, `multiple species`, `various species`, or similar vague text. |
| `disease_association` | Disease names, or `unknown` | If disease associations are present, write disease names such as `cancer;Alzheimer's disease`. If disease information is absent, not applicable, or cannot be verified, write `unknown`. Never write `none`, `Unclear`, `yes`, `no`, `true`, or `false`. |
| `developmental_association` | Developmental stages, or `unknown` | If developmental stages are present, write stages such as `fetal;adult;E14;P0`. If developmental information is absent, not applicable, or cannot be verified, write `unknown`. Never write `none`, `Unclear`, `yes`, `no`, `true`, or `false`. |
| `tissue_or_brain_region` | Specific tissue/region tags, or `unknown` | Write actual tissues, organs, or brain regions such as `cortex;hippocampus;whole brain`. If tissue or region information cannot be verified, write `unknown`. Do not write `multiple tissues`, `various tissues`, or similar vague text. |
| `cell_type` | Specific cell-type tags, or `unknown` | Write actual cell types such as `neuron;astrocyte;microglia`. If cell-type information is absent, not applicable, or cannot be verified, write `unknown`. Do not write `none`, `multiple cell types`, `various cell types`, or similar vague text. |
| `sequencing_resolution` | `bulk`; `single_cell`; `bulk;single_cell` | Use `single_cell` only for single-cell or single-nucleus data. Use `bulk` for bulk tissue, cell line, curated annotation, reference transcript, EST/cDNA, Sanger, microarray, ordinary RNA-seq, or other non-single-cell evidence. If both bulk and single-cell data are present, write `bulk;single_cell`. Do not write `unknown` in this field. |
| `read_technology` | `short`; `long`; `long;short` | Use `long` for PacBio, Oxford Nanopore, Iso-Seq, full-length long-read transcript sequencing, or explicitly long-read evidence. Use `short` for Illumina, standard RNA-seq, EST/cDNA, Sanger, microarray, CLIP-seq, small RNA-seq, curated/reference annotations without explicit long-read evidence, or when the read technology is not clearly stated. If both long-read and short-read evidence are present, write `long;short`. Default to `short`; do not write `unknown` in this field. |
| `visualization_methods` | Plain text, or `unknown` | Write concise specific methods such as `genome browser;splice graph;sashimi plot;bar plot;heatmap`. If visualization methods are absent, not applicable, or cannot be verified, write `unknown`. Do not write `none`, `various plots`, or `multiple visualizations`. |

## Dedicated Neural-Link Verification

Evaluate `neural_link` independently from the database-type evidence. For every row, check the official database or help pages and the database publication for concrete neural terms and records, including `brain`, `neural`, `neuron`, `glia`, `cortex`, `hippocampus`, `cerebellum`, `spinal cord`, neurodevelopment, neurological disease, and named neural cell types or brain regions.

- `primary`: neural content is the main purpose or dominant dataset scope.
- `partial`: the resource is general, but users can search, filter, browse, or display explicit neural tissue, region, cell, disease, transcript, isoform, or splicing evidence.
- `none`: after the dedicated search, no explicit neural content is found. A generic gene-symbol search alone does not establish `partial`.
- Do not infer `none` from an `evidence_statement` written only to prove `db_type_confirmation`; follow the database links and inspect neural-specific records or documentation.
- `neural_link=none` is invalid when `tissue_or_brain_region`, `cell_type`, `disease_association`, or `developmental_association` contains explicit neural content.
- Positive calibration: AceView is `partial`, not `none`, because its general transcript records explicitly include brain and hippocampus tissue evidence. SASdb cannot remain `none` while its tissue metadata includes brain.

## Decision Consistency Rules

| Decision | Required field combination |
|---|---|
| Qualified resource | `db_type_confirmation=yes`; one or more allowed `qualification_basis` values; `exclusion_code=not_applicable`; `manual_review_needed=no`. |
| Direct splicing basis present | `focus=AS_focused`. Direct bases are `splicing_event`, `splice_site_or_junction`, and `splicing_regulation_or_sqtl`. |
| Transcript model/abundance only | `focus=transcriptomics_general`. |
| Excluded resource | `db_type_confirmation=no`; `qualification_basis=not_applicable`; a non-`not_applicable` `exclusion_code`; `focus=unknown`; `gene_expression_available=no`. |
| Insufficient evidence | Still use `db_type_confirmation=no`. `manual_review_needed=yes` is optional only with `exclusion_code=insufficient_evidence`. |

At most 5% of a result chunk may use `manual_review_needed=yes`. With the standard 25-row chunk size, this means at most one row. Explicitly out-of-scope resources must not consume this allowance.

## Accessibility Rules

Before Agent review, the pipeline scans normalized URLs with 32 workers and a total load budget of 120 seconds per URL. The automatic statuses are `reachable`, `restricted`, `continue_required`, `unreachable`, and `missing`. HTTP 401/403/429, captchas, institutional redirects, disclaimers, and continue pages are not automatic proof that a database is dead.

Model routing: use `gpt-5.6-terra` with `medium` reasoning for URL-only accessibility, redirect, TLS, and Continue/Proceed path checks. Any task that also judges database qualification, transcript/splicing content, duplicate-group membership, or representative-paper selection remains a full curation task and must use the main screening model.

The automatic result never replaces manual browser inspection for a canonical `db_type_confirmation=yes` database. Open every canonical yes site yourself. If a page presents `Continue`, `Proceed`, `Enter site`, `I understand`, `继续访问`, a disclaimer, a verification page, or another reasonable interstitial, follow the action until the actual database is visible. An interstitial or warning page is never the evidence page.

| Situation | `accessibility` value | Additional action |
|---|---|---|
| The provided URL works | `live` | Keep `database_url` unless a more official current URL is found. |
| The provided URL redirects to a working official site | `live` | Replace `database_url` with the current official URL. |
| The original URL is broken but a new official URL exists | `live` | Replace `database_url` with the new official URL. |
| A continue/disclaimer page leads to the actual official database | `live` | Click through, verify database content, and replace `database_url` with the stable final direct URL. Record the click path in the accessibility audit. |
| The site has a verification page, captcha, institutional redirect, or partial access | `live` | Do not mark as dead solely for this reason. Inspect it manually and record the restriction or required click path. |
| A necessary official interstitial cannot be bypassed with a stable direct URL | `live` | Retain the necessary entry URL and record the full click path; use the post-entry database/documentation page as evidence, never the warning page. |
| Some subpages are broken but the database still exists | `live` | Keep or update `database_url` to the best official landing page. |
| The website is truly unavailable after URL variants, migration search, redirects, and interstitial handling | `dead` | Keep the most relevant known URL in `database_url` and use the corresponding database publication as evidence. |

Do not mark a resource as `dead` only because:

| Do not mark dead for this reason |
|---|
| The site redirects. |
| A captcha or verification page appears. |
| The site loads slowly. |
| The original URL changed but a new official URL exists. |
| Some subpages are broken. |

Cross-domain redirects, parked domains, malware warnings, and phishing warnings require explicit Agent judgment. Do not click through a clear security threat merely to classify the resource. Record the result in `08_accessibility_audit.xlsx`.

## Search Strategy

For each row, follow this search workflow:

| Step | Action |
|---|---|
| 1 | Start with the provided `database_url`. |
| 2 | If the URL is missing or broken, search using `database_name`, `title`, `doi`, and `pmid`. |
| 3 | Try reasonable URL variants, including HTTP/HTTPS, www/non-www, root domain, and redirected official pages. Follow reasonable Continue/Proceed/Enter site/I understand/继续访问 or disclaimer actions to reach the actual database. |
| 4 | If the database is live, inspect the official database page or official documentation and use that official page as evidence. If the website remains dead after reasonable attempts, use its database publication. PubMed/PMC/DOI may be used to reach the publication. Search-result snippets, model knowledge, archives, and interstitial warning pages are not final evidence. If sequencing resolution or read technology is absent from the site, the publication may supplement those metadata fields but cannot replace the official evidence URL for a live row. |
| 5 | Decide whether the resource is `live` or `dead`; all canonical yes databases require direct Agent browser confirmation regardless of the automatic scan status. |
| 6 | Decide whether `db_type_confirmation` is `yes` or `no` based on both searchable database functionality and an allowed `qualification_basis`. |
| 7 | Update mutable `database_url` to the stable current direct official database URL after redirects/interstitials. If a necessary official entry page cannot be bypassed, keep it and document the click path in the accessibility audit. |
| 8 | Save one `evidence_url`, its source type, a 20–300 character combined evidence statement, and the checked date. Then fill every remaining field using evidence-backed information only. |

## Output Rules

| Rule | Requirement |
|---|---|
| Preserve row identity | Never modify `id`. |
| Preserve source contract | Never modify `original_48_database` or `screening_policy_version` during row review. During canonical merge, apply logical OR to `original_48_database`; the policy version remains unchanged. The original marker is provenance, not an inclusion override. |
| Preserve correct existing values | Do not change correct prefilled `title`, `doi`, `pmid`, or `year`. |
| Maintain database destination | `database_url` is intentionally mutable. Fill a missing URL or replace an obsolete, redirecting, or interstitial-only URL only when the current official database destination is verified. |
| Preserve publication identity | Outside canonical deduplication, do not rewrite existing `id`, `title`, `doi`, `pmid`, or `year`. In a duplicate group, inherit all five fields from the one Agent-selected representative row. |
| Fill every agent field | Final output must not leave agent-filled fields blank. Evidence fields are mandatory for both yes and no decisions. Use `unknown` only for metadata fields whose rules allow it. |
| Avoid guessing | If evidence is insufficient, use `unknown` according to the field rules. Do not use `none`, blank cells, `Unclear`, `NA`, `N/A`, or `not specified` for missing metadata. |
| Separate multiple values | Use `;` with no comma-separated lists. |
| Keep values concise | Use short controlled values or concise tags whenever possible. |
| Preserve schema | Keep every original column header exactly unchanged. Do not create explanatory headers or modify controlled-value notes inside headers. |
| Evidence is singular | Provide exactly one evidence URL and one combined evidence statement per row; do not place semicolon-separated URLs in `evidence_url`. |
| Avoid boolean leakage | Only fields explicitly defined as boolean may use `yes` or `no`. Do not use `yes` or `no` in `disease_association` or `developmental_association`. |
| Avoid vague placeholders | Do not use `multiple species`, `multiple tissues`, `multiple cell types`, `various species`, `various tissues`, `various cell types`, or similar generic placeholders. Record specific values if found; otherwise write `unknown`. |
