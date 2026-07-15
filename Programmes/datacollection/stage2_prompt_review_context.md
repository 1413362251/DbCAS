# Stage2 Prompt Review Context

## Goal

I am building a literature screening pipeline to identify papers that **produce reusable biomedical databases/resources related to transcriptomics**.

This is the final high-precision screening stage. I want the Stage2 prompt to be strict enough to remove false positives, but not so narrow that it loses real transcriptomics database/resource papers.

## Current Pipeline Background

The search pipeline has already completed broad literature retrieval, citation expansion, keyword screening, and a low-cost high-recall Stage1 AI filter.

Current run:

```text
run_id: search_pre_aicheck_2015_2026_seed2000_20260701_145117
```

Search strategy summary:

- Search years: 2015 to 2026.
- Seed search: roughly 2,000 search results per year before deduplication.
- Then collected references from seed papers.
- Then deduplicated all referenced papers.
- Then applied keyword screening for resource/database terms plus RNA/transcriptomics scope terms.

Current counts:

```text
Deduplicated reference details: 376,263
After Stage4 keyword screening: 13,442
With abstract, eligible for AI screening: 13,415
Missing abstract, handled separately by manual/Codex search: 27
```

Stage1 used `gpt-5.4-mini` in `flex` mode as a high-recall filter.

Stage1 result:

```text
Stage1 input rows: 13,415
Pass: 4,665
Reject: 8,750
Unclear: 0
Failures: 0
```

Stage2 will run only on the `4,665` Stage1 Pass rows.

## Final Inclusion Need

For Stage2, I want to keep papers that satisfy both conditions:

1. The paper **introduces, describes, releases, hosts, or substantially updates** a reusable database/resource.
2. The database/resource contains or organizes data that is **at least related to transcriptomics / RNA-level biology**.

Alternative splicing is highly preferred, but **not required**.

In other words:

- A splicing database should be included.
- An alternative splicing event atlas should be included.
- A transcript isoform database should be included.
- A transcriptomics / RNA-seq / gene expression atlas should be included.
- A single-cell RNA atlas or transcriptomic portal should be included.
- A resource with transcript annotations, isoforms, RNA editing, RNA modification, eQTL/sQTL, or RNA-level variant consequences should be included.
- A general biological database with no RNA/transcriptomics focus should be excluded.

## Key Definitions

### What Counts as a Reusable Database/Resource

Answer `Yes` only if the paper itself releases, describes, hosts, or substantially updates one of the following:

- database
- atlas
- catalog / catalogue
- portal
- web server
- knowledge base
- curated dataset
- downloadable dataset
- public data collection
- searchable online resource
- reusable benchmark/annotation dataset, if transcriptomics/RNA-related

### Transcriptomics / RNA Scope

The resource should contain or organize data such as:

- RNA-seq
- gene expression
- transcript expression
- transcript annotations
- transcript models
- isoforms
- splice isoforms
- alternative splicing
- splice junctions
- splice sites
- RNA variants
- RNA editing
- RNA modifications
- RNA methylation
- eQTL / sQTL
- single-cell RNA / single-nucleus RNA data
- transcriptomics-level disease or variant data
- other RNA-level omics data

## Exclusion Need

Answer `No` if:

- The paper only **uses** an existing database/resource.
- The paper only reports biological findings without releasing a reusable resource.
- The paper only presents a general computational method/software package and does not provide a reusable transcriptomics database, portal, curated dataset, or downloadable data collection.
- The paper describes a database/resource, but the primary data focus is not RNA/transcriptomics.
- The paper is about genomics, proteomics, metabolomics, clinical phenotypes, imaging, pathways, PPI, drugs, taxonomy, ecology, or general annotation without a clear RNA/transcriptomics data focus.
- The evidence from title and abstract is insufficient.

## Important Edge Cases

Please evaluate whether the prompt handles these correctly:

- A general transcriptomics atlas should be `Yes`, even if it is not about alternative splicing.
- A single-cell RNA atlas should be `Yes` if it releases reusable transcriptomic data.
- A tool/web server should be `Yes` only if it hosts, exposes, or provides access to a reusable transcriptomics/RNA data resource.
- A pure method paper should be `No`, even if it analyzes RNA-seq.
- A paper using GTEx/TCGA/ENCODE or another database should be `No` unless it releases a new reusable RNA/transcriptomics resource.
- A broad database with only incidental RNA terms should be `No`.
- If uncertain, prefer `No` in Stage2, because Stage1 already handled recall.

## Desired Output Format

The pipeline expects one JSON object per paper.

Required fields:

```json
{
  "AI_check": "Yes or No",
  "AI_reason": "one short reason",
  "resource_type": "database/atlas/portal/web server/dataset/knowledge base/other/none",
  "transcriptomics_scope": "AS-focused/transcriptomics-general/RNA-related/none",
  "AS_relevance": "primary/partial/none"
}
```

`AI_check` is the main decision field.

The other fields help downstream ranking:

- `resource_type`: what kind of resource the paper releases.
- `transcriptomics_scope`: whether the resource is primarily alternative-splicing focused, broadly transcriptomics-related, or only RNA-related.
- `AS_relevance`: whether alternative splicing is the main focus, partial relevance, or absent.

## Current Draft Stage2 Prompt

Please review and improve this prompt if needed:

```text
Using the title, abstract text, and your biomedical knowledge, decide whether this paper should be kept as a candidate transcriptomics-related database/resource paper.

Answer Yes only if the paper introduces, describes, releases, hosts, or substantially updates a reusable database, atlas, catalog, portal, web server, knowledge base, curated dataset, downloadable dataset, or online resource.

The resource must contain or primarily organize transcriptomics-related biological data, such as RNA-seq, gene expression, transcript expression, transcript annotations, isoforms, splice isoforms, alternative splicing, splice junctions, RNA variants, RNA editing/modification, eQTL/sQTL, single-cell RNA data, or related RNA-level omics data.

Alternative splicing relevance is preferred but not required. A general transcriptomics database/resource should still be Yes if it provides reusable RNA/transcript-level data.

Answer No if the paper only uses an existing database, only presents a method or analysis without releasing a reusable data resource, only reports biological findings, or describes a resource whose data focus is not RNA/transcriptomics.

Answer No if the evidence is insufficient.

Return exactly one JSON object:
{"AI_check":"Yes" or "No","AI_reason":"one short reason","resource_type":"database/atlas/portal/web server/dataset/knowledge base/other/none","transcriptomics_scope":"AS-focused/transcriptomics-general/RNA-related/none","AS_relevance":"primary/partial/none"}
```

## What I Need From You

Please act as a strict biomedical literature screening prompt reviewer.

I need you to:

1. Check whether the draft prompt matches the inclusion/exclusion criteria above.
2. Identify any ambiguity that could cause false positives or false negatives.
3. Improve the prompt if needed.
4. Keep the final prompt concise enough for large-scale API screening.
5. Preserve exact JSON-only output behavior.
6. Make sure the final prompt does not exclude general transcriptomics databases simply because they are not AS-focused.
7. Make sure the final prompt does not include papers that only use existing databases or only present methods without releasing a reusable RNA/transcriptomics resource.

Please provide:

- A short critique of the draft prompt.
- A final recommended prompt.
- Any optional fields or wording changes that would improve downstream sorting without increasing false positives too much.
