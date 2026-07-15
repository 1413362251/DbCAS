import json
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
from tqdm.auto import tqdm

from pipeline_runtime import PipelineState, RateLimiter, append_csv_row, clean_str, read_csv_rows


DEFAULT_DATABASE_PATTERN = re.compile(
    r"\b(?:database|knowledge\s*base|knowledgebase|dataset|data\s*set|catalog(?:ue)?|"
    r"biobank|atlas|portal|web\s*server|resources?)\b",
    flags=re.IGNORECASE,
)

DEFAULT_RNA_SCOPE_PATTERN = re.compile(
    r"\b(?:"
    r"rna\w*|mRNA\w*|pre[-\s]?mRNA\w*|ncRNA\w*|lncRNA\w*|"
    r"long\s+non[-\s]?coding\s+RNA\w*|miRNA\w*|microRNA\w*|"
    r"circRNA\w*|circular\s+RNA\w*|snRNA\w*|snoRNA\w*|piRNA\w*|siRNA\w*|"
    r"transcript\w*|transcriptome\w*|transcriptomic\w*|RNA[-\s]?seq|RNAseq|"
    r"bulk\s+RNA|single[-\s]?cell\s+RNA|single[-\s]?nucleus\s+RNA|"
    r"scRNA[-\s]?seq|snRNA[-\s]?seq|expression\s+atlas|gene\s+expression|"
    r"transcript\s+expression|isoform\w*|exon\w*|intron\w*|exon[-\s]?usage|"
    r"junction\w*|splice[-\s]?junction\w*|UTR\w*|poly\(?A\)?|polyadenylation|"
    r"splic\w*|alternative\s+splicing|splice[-\s]?site\w*|"
    r"splice[-\s]?variant\w*|spliceosome|exon\s+skipping|intron\s+retention|"
    r"eQTL\w*|sQTL\w*|splice[-\s]?QTL\w*|"
    r"splicing\s+quantitative\s+trait\s+loci|"
    r"expression\s+quantitative\s+trait\s+loci|"
    r"transcriptome[-\s]?wide\s+association\w*|cDNA|EST\b|"
    r"expressed\s+sequence\s+tag\w*|gene\s+model\w*|"
    r"reference\s+sequence\w*|RefSeq|GENCODE|splice[-\s]?site\s+variant\w*|"
    r"synonymous\s+variant\w*|synonymous\s+mutation\w*|RNA\s+editing|"
    r"RNA\s+modification|RNA\s+methylation|transcript\s+consequence\w*|"
    r"protein\s+isoform\w*|proteome[-\s]?supported\s+isoform\w*"
    r")\b",
    flags=re.IGNORECASE,
)

DEFAULT_AI_QUESTION = (
    "Using the title, abstract text, and your biomedical knowledge, decide whether "
    "this paper should be included as a candidate splicing-related database/resource "
    "paper.\n\n"
    "Answer Yes only if the paper introduces, describes, releases, hosts, or "
    "substantially updates a database, atlas, catalog, portal, web server, knowledge "
    "base, curated dataset, downloadable dataset, or online resource whose primary "
    "biological focus is RNA splicing, alternative splicing, splice variants, splice "
    "isoforms, splice sites, splicing regulation, or splicing-related "
    "disease/variants.\n\n"
    "Answer Yes for a software tool or web server only if it provides a "
    "splicing-focused online resource, searchable database, curated dataset, "
    "downloadable data collection, or public portal.\n\n"
    "Answer No if the paper only uses an existing database, only presents a general "
    "analysis method, only reports biological findings without releasing a reusable "
    "splicing-focused resource, or describes a resource whose primary focus is not "
    "splicing.\n\n"
    "If the evidence from the title, abstract, and your biomedical knowledge is "
    "insufficient, answer No.\n\n"
    "Return exactly one JSON object:\n"
    '{"AI_check":"Yes" or "No","AI_reason":"one short reason, max 40 words"}'
)

DEFAULT_STAGE1_SYSTEM_PROMPT = (
    "You are a conservative biomedical literature triage assistant. "
    "You must return only the requested JSON object. Never reject plausible positives."
)

DEFAULT_STAGE1_SERVICE_TIER = "flex"

DEFAULT_STAGE1_TRIAGE_QUESTION = (
    "High-recall first-pass triage for candidate transcriptomics/RNA resource papers. "
    "Your only job is to decide whether a paper can be safely excluded before expert "
    "review. Protect recall: if not clearly Reject, choose Pass or Unclear.\n\n"
    "Pass or Unclear if the paper introduces, updates, or generates any reusable "
    "public RNA/transcriptomics resource: database, atlas, catalog/catalogue, portal, "
    "web server, knowledge base, curated/downloadable dataset, online resource, "
    "visualization app, browser/viewer, searchable table, project website, or "
    "supplementary reusable data.\n\n"
    "Relevant scope includes transcript annotation, isoforms, cDNA/EST-supported "
    "transcripts, RNA-seq or single-cell expression, exon/intron structure, splicing, "
    "splice variants/sites, alternative splicing, splicing regulation, eQTLs/sQTLs, "
    "splice-QTL effects, transcriptome-wide associations, proteome-supported isoforms, "
    "splice-site variants, synonymous variants with RNA/transcript consequences, and "
    "mutation resources with transcript/splicing interpretation. The resource may be "
    "disease-, tissue-, species-, gene-, variant-, or proteome-focused.\n\n"
    "Pass or Unclear even without a formal database name, portal label, URL, or explicit "
    "alternative-splicing event table if the abstract says the study creates, reports, "
    "compiles, generates, or provides a catalog, map, list, table, atlas, compendium, "
    "annotation set, benchmark set, or reusable dataset in the relevant scope. Do not "
    "Reject solely because it also reports biological findings or disease mechanisms.\n\n"
    "Reject only when it is clearly not a reusable RNA/transcript/transcriptomics/"
    "isoform/splicing-related resource: e.g. it only uses an existing database, only "
    "presents a general method without reusable public data/resource, only reports "
    "biology, or the resource is clearly unrelated to RNA/transcripts/isoforms/splicing.\n\n"
    "Return exactly one JSON object:\n"
    '{"stage1_decision":"Reject" or "Pass" or "Unclear","stage1_reason":"one short reason"}'
)

DEFAULT_STAGE2_SERVICE_TIER = "flex"
DEFAULT_STAGE2_REASONING_EFFORT = "none"
DEFAULT_STAGE2_PROMPT_CACHE_RETENTION = "24h"
SCREENING_POLICY_VERSION = "transcript_splicing_v2"
DEFAULT_STAGE2_PROMPT_CACHE_KEY = "dbc_stage2_transcript_splicing_v2"
DEFAULT_STAGE2_MAX_OUTPUT_TOKENS = 800

LEGACY_STAGE2_EXPERT_PROMPT_V1 = (
    "You are a strict biomedical literature classifier for a high-precision screening pipeline.\n\n"
    "Task:\n"
    "Given only a paper title and abstract, classify whether the paper itself is a reusable "
    "biomedical database/resource paper related to transcriptomics or RNA-level biology.\n\n"
    "Use only explicit evidence in the title and abstract. Use biomedical knowledge only to "
    "interpret biomedical terms, abbreviations, and well-known resource names. Do not infer an "
    "unmentioned database release, unmentioned URL, unmentioned web portal, or unmentioned RNA "
    "scope from prior knowledge alone. If the evidence is insufficient, choose the more "
    "conservative class.\n\n"
    "The goal is to keep papers that introduce, describe, release, host, provide, maintain, or "
    "substantially update reusable resources containing or organizing transcriptomics/RNA-level "
    "data. Alternative splicing is highly preferred, but it is not required. General "
    "transcriptomics, RNA-seq, single-cell RNA, expression atlas, transcript annotation, "
    "isoform, RNA editing, RNA modification, eQTL, sQTL, TWAS, and RNA-level variant "
    "consequence resources can all be relevant.\n\n"
    "Assign exactly one integer AI_class.\n\n"
    "Class priority:\n"
    "First check class 1. If class 1 does not apply, check whether the paper is a major "
    "public/consortium resource paper for class 3. If not, check classes 2, 4, 5, 6, and 7. "
    "Do not let class 2 absorb major consortium resources that are better represented by "
    "class 3.\n\n"
    "AI_class definitions:\n\n"
    "1 = Direct reusable RNA/transcriptomics resource paper with explicit access evidence.\n"
    "Use class 1 when the paper directly introduces, describes, releases, hosts, maintains, "
    "or substantially updates a relevant database, atlas, portal, catalog, knowledge base, "
    "searchable resource, web resource, or data server, and the title/abstract explicitly "
    "gives a URL, web address, access link, or direct availability location. Evidence can "
    "include strings or phrases such as http, https, www, .org, .edu, .gov, available at, "
    "accessible at, online at, freely available at, database available from, web server "
    "available at, or similar direct access wording. Class 1 requires both a reusable "
    "RNA/transcriptomics resource and explicit access evidence.\n\n"
    "2 = Direct named reusable RNA/transcriptomics resource paper, no explicit URL.\n"
    "Use class 2 when the paper directly introduces, describes, releases, hosts, maintains, "
    "or substantially updates a named database, atlas, portal, catalog/catalogue, knowledge "
    "base, searchable online resource, curated database, or web-accessible resource with clear "
    "RNA/transcriptomics data, but the title/abstract does not explicitly provide a URL or "
    "direct access location. Use this for ordinary named resource papers that are not "
    "primarily major public/consortium resources.\n\n"
    "3 = Major public or consortium resource paper with substantial RNA/transcriptomics data.\n"
    "Use class 3 for papers about GTEx, TCGA, ENCODE, FANTOM, Human Cell Atlas/HCA, "
    "Expression Atlas, GENCODE, RefSeq, Roadmap-style public multi-omics resources, or similar "
    "large public/consortium resources, if the paper itself describes a resource, release, "
    "update, atlas, portal, reference annotation, public data collection, or substantial "
    "reusable RNA/transcriptomics module. This class is for resource/release/update papers, "
    "not ordinary downstream analyses. If a GTEx/TCGA/ENCODE paper has an explicit URL and "
    "otherwise satisfies class 1, choose class 1.\n\n"
    "4 = Reusable RNA/transcriptomics dataset or collection, not clearly a database/portal.\n"
    "Use class 4 when the paper releases or describes a reusable RNA/transcriptomics dataset, "
    "benchmark, annotation collection, catalog, curated downloadable dataset, public data "
    "collection, reference dataset, structured data resource, training/evaluation resource, "
    "or reusable data compendium, but it is not clearly a named database, atlas, portal, "
    "knowledge base, or consortium resource. Reuse must be explicit. Ordinary supplementary "
    "tables from one analysis do not count unless the abstract frames them as a reusable "
    "dataset, benchmark, atlas, catalog, annotation collection, or public resource.\n\n"
    "5 = Method, software, viewer, or web server with hosted reusable RNA/transcriptomics data.\n"
    "Use class 5 only when the method/tool/server explicitly hosts, exposes, searches, "
    "visualizes, browses, or provides downloadable access to reusable RNA/transcriptomics "
    "data. A pure algorithm, package, predictor, model, or web server that only accepts user "
    "input and returns computed results is not enough. If the tool is mainly computational "
    "but also provides a reusable hosted data collection, class 5 can apply.\n\n"
    "6 = Uses existing RNA/transcriptomics resources only.\n"
    "Use class 6 when the paper analyzes, mines, benchmarks, validates, integrates, "
    "interprets, or trains models using existing resources such as GTEx, TCGA, ENCODE, GEO, "
    "SRA, ArrayExpress, Expression Atlas, GENCODE, RefSeq, dbGaP, or similar, but does not "
    "itself release, describe, host, or substantially update a reusable resource. Class 6 is "
    "not a keep class by default, but it distinguishes existing-resource use from completely "
    "irrelevant papers.\n\n"
    "7 = Not a relevant reusable RNA/transcriptomics resource paper.\n"
    "Use class 7 for ordinary biological studies, disease mechanism studies, biomarker papers, "
    "differential expression analyses, pathway analyses, pure methods, pure software without "
    "hosted reusable data, resources focused mainly on proteins, pathways, PPI, drugs, "
    "clinical phenotypes, imaging, taxonomy, ecology, literature mining, or general annotation, "
    "broad databases with only incidental RNA terms, and papers with insufficient evidence.\n\n"
    "RNA/transcriptomics scope:\n"
    "A relevant resource may contain, organize, curate, index, search, visualize, annotate, "
    "or provide access to RNA-seq, bulk RNA-seq, single-cell RNA-seq, single-nucleus RNA-seq, "
    "scRNA-seq, snRNA-seq, gene expression, transcript expression, expression atlas, "
    "transcriptome, transcriptomics, transcript annotations, transcript models, reference "
    "transcripts, transcript isoforms, splice isoforms, exons, introns, exon usage, intron "
    "retention, splice junctions, splice sites, alternative splicing, splicing regulation, "
    "splice variants, RNA variants, RNA editing, RNA modifications, RNA methylation, mRNA, "
    "lncRNA, miRNA, circRNA, ncRNA, eQTL, sQTL, splice QTL, TWAS, transcriptome-wide "
    "association, RNA-level disease consequences, RNA-level variant consequences, "
    "cDNA/EST-based transcript evidence, GENCODE/RefSeq-style transcript annotation, or "
    "substantial RNA modules within broader multi-omics resources.\n\n"
    "Broad multi-omics resources:\n"
    "A broad multi-omics resource can be relevant only when RNA/transcriptomics data are "
    "explicit and substantial, not incidental. If the abstract says the resource integrates "
    "genomics, transcriptomics, epigenomics, proteomics, and other omics, and the paper is "
    "about releasing or updating that reusable resource, it may be class 3 or 4 depending "
    "on the resource. If RNA is mentioned only as one example, one possible analysis type, "
    "or one minor annotation among many unrelated data types, choose class 7 unless a "
    "substantial RNA module is clear.\n\n"
    "Existing-resource distinction:\n"
    "A paper about GTEx, TCGA, ENCODE, FANTOM, Human Cell Atlas, Expression Atlas, GENCODE, "
    "RefSeq, or similar resources can be kept if it describes the resource itself, a data "
    "release, a reference annotation release, a portal, an atlas, a public data collection, "
    "or a reusable RNA/transcriptomics module. A paper that merely uses these resources to "
    "find biomarkers, build a model, study disease, perform association analysis, or "
    "validate a method is class 6.\n\n"
    "Web server distinction:\n"
    "A web server is not automatically a reusable resource. It counts only if it hosts, "
    "exposes, searches, browses, visualizes, or provides access to reusable RNA/transcriptomics "
    "data. If the server only runs an algorithm on user-submitted sequences, variants, or "
    "expression matrices, and does not provide a reusable hosted data resource, choose class "
    "7 unless another class clearly applies.\n\n"
    "Supplementary-data distinction:\n"
    "Ordinary supplementary result tables from a single biological analysis are not a "
    "reusable database/resource. A supplementary dataset can count only when the abstract "
    "explicitly frames it as a reusable dataset, benchmark, atlas, catalog, annotation "
    "collection, reference collection, public data collection, curated dataset, or resource "
    "intended for reuse.\n\n"
    "Boundary examples for calibration:\n"
    "Example A: An abstract says \"Database X is a curated database of alternative splicing "
    "events and is freely available at https://...\". This is class 1, resource_type "
    "database, transcriptomics_scope AS-focused, AS_relevance primary.\n"
    "Example B: An abstract says \"We developed Atlas Y, a single-cell transcriptomic atlas "
    "of human fetal tissues,\" but gives no URL. This is class 2 if Atlas Y is a named "
    "reusable atlas.\n"
    "Example C: An abstract presents a GTEx data release, tissue expression atlas, or "
    "consortium portal with substantial gene expression data. This is class 3 unless "
    "explicit URL evidence makes it class 1.\n"
    "Example D: An abstract says \"Using TCGA and GTEx, we identified prognostic lncRNAs "
    "in cancer.\" This is class 6, because it uses existing resources only.\n"
    "Example E: An abstract introduces a downloadable benchmark set of splice junction "
    "annotations for evaluating transcript assemblers. This is class 4 if reuse is explicit.\n"
    "Example F: An abstract introduces software for differential expression analysis of "
    "RNA-seq data, with no hosted reusable dataset. This is class 7, not class 5.\n"
    "Example G: An abstract introduces a web server that predicts splice effects from "
    "user-submitted variants, with no hosted curated resource. This is class 7.\n"
    "Example H: An abstract introduces a browser that visualizes a curated collection of "
    "RNA editing sites across tissues. This is class 5 if the browser/tool hosts reusable "
    "RNA data.\n"
    "Example I: An abstract describes a multi-omics disease portal integrating genomics, "
    "transcriptomics, proteomics, and clinical data. This can be class 2, 3, or 4 only if "
    "the resource itself is released/described and transcriptomics data are substantial.\n"
    "Example J: An abstract describes a protein interaction database and only mentions mRNA "
    "expression as one minor annotation. This is class 7.\n\n"
    "Field rules:\n"
    "AI_check must be \"Yes\" for AI_class 1, 2, 3, 4, or 5. AI_check must be \"No\" for "
    "AI_class 6 or 7.\n\n"
    "AI_reason must be short, maximum 20 words. It should mention the decisive evidence, "
    "such as \"AS database with URL\", \"GTEx resource release\", \"uses TCGA only\", or "
    "\"pure method without hosted data\".\n\n"
    "resource_name must be the main resource name introduced, described, released, hosted, "
    "or updated. If multiple relevant resources are introduced, separate names with "
    "semicolons. If no clear resource name exists, use \"none\". For class 6, use the "
    "central existing resource name only if it is important; otherwise use \"none\".\n\n"
    "resource_type must be one of:\n"
    "database, atlas, portal, web server, dataset, knowledge base, consortium resource, "
    "software-data server, other, none.\n"
    "Use consortium resource for GTEx, TCGA, ENCODE, FANTOM, HCA, Expression Atlas, "
    "GENCODE, RefSeq, or similar major public resources when class 3 applies. Use "
    "software-data server for tools/web servers that also host reusable RNA/transcriptomics "
    "data. Use none for class 7, and usually for class 6 unless a central used resource "
    "is named.\n\n"
    "url_mentioned must be \"Yes\" only if the title/abstract explicitly gives a URL, web "
    "address, access link, or direct availability location. Do not mark \"Yes\" merely "
    "because the resource is known to have a website. \"Available online\" without a "
    "direct access location is not enough.\n\n"
    "transcriptomics_scope must be one of:\n"
    "AS-focused, transcriptomics-general, RNA-related, multi-omics-with-RNA, none.\n"
    "Use AS-focused when alternative splicing/splicing is the main resource focus. Use "
    "transcriptomics-general for RNA-seq, expression, transcriptome, single-cell RNA, "
    "transcript annotation, or isoform resources. Use RNA-related for RNA editing, RNA "
    "modification, eQTL, sQTL, TWAS, RNA-level variant consequences, or substantial RNA "
    "modules. Use multi-omics-with-RNA for broad multi-omics resources with explicit "
    "substantial RNA data. Use none for class 7, and usually class 6.\n\n"
    "AS_relevance must be one of:\n"
    "primary, partial, none.\n"
    "Use primary when alternative splicing, splicing, splice variants, splice isoforms, "
    "splice sites, splice junctions, exon skipping, intron retention, or splicing regulation "
    "is the main resource focus. Use partial when isoforms, exons, junctions, transcript "
    "models, sQTLs, splice consequences, or splice-related variant interpretation are "
    "important but not the main focus. Use none otherwise.\n\n"
    "Conservative tie-breaks:\n"
    "If uncertain between class 1 and 2, choose 2 unless explicit access evidence is present.\n"
    "If uncertain between class 2 and 4, choose 4 unless database/atlas/portal/knowledge-base "
    "framing is clear.\n"
    "If uncertain between class 3 and 6, choose 6 unless the paper itself describes the "
    "public resource, release, update, atlas, portal, or reusable data collection.\n"
    "If uncertain between class 4 and 7, choose 7 unless reusable data release is explicit.\n"
    "If uncertain whether RNA/transcriptomics scope is substantial, choose 7.\n"
    "If the abstract is too vague, choose 7.\n\n"
    "Return exactly one valid JSON object and no other text.\n"
    "Do not wrap the JSON in markdown.\n"
    "Do not include comments.\n"
    "Do not include trailing commas.\n"
    "Use an integer for AI_class.\n"
    "Use only the allowed values.\n"
    "The JSON schema is exactly:\n"
    "{\"AI_class\":1,\"AI_check\":\"Yes\",\"AI_reason\":\"max 12 words\","
    "\"resource_name\":\"name or none\",\"resource_type\":\"database/atlas/portal/web "
    "server/dataset/knowledge base/consortium resource/software-data server/other/none\","
    "\"url_mentioned\":\"Yes/No\",\"transcriptomics_scope\":\"AS-focused/"
    "transcriptomics-general/RNA-related/multi-omics-with-RNA/none\","
    "\"AS_relevance\":\"primary/partial/none\"}\n\n"
    "Now classify the paper in the next user message."
)

DEFAULT_STAGE2_EXPERT_PROMPT = """
You are a strict biomedical literature classifier for screening searchable
transcript-level and RNA-splicing databases.

Use only explicit evidence in the title and abstract. Biomedical knowledge may
clarify terminology, but must not invent a database release, website, or data
level that is not stated. Stage 2 is a high-recall gate before website review,
but gene-level expression alone is outside the target scope.

A target resource must plausibly be a database, atlas, portal, browser, queryable
repository, knowledge base, or web server and must plausibly provide at least one
of these data types:

- alternative-splicing events, PSI, exon usage, intron retention, splice sites,
  splice junctions, back-splice junctions, or splicing regulation;
- sQTLs or splice-affecting variants with transcript/splicing consequences;
- transcript or isoform identifiers, exon-intron models, transcript structures,
  reference transcript annotations, or full-length isoforms;
- abundance or expression quantified at transcript/isoform level rather than
  gene level.

Do not keep a resource merely because it contains RNA-seq, transcriptomic, or
gene-expression data. Gene-level expression atlases, differential-expression
resources, generic functional-genomics repositories, and single-cell atlases
without explicit transcript/isoform/splicing content are not target resources.
Generic miRNA/lncRNA targets or disease associations, RNA interactions, RNA
editing/modification records, and multi-omics portals are also out of scope
unless qualifying transcript-model or splicing content is explicit.

circRNA and alternative-polyadenylation resources are not automatic positives.
Use a positive class only when the abstract indicates back-splice junctions,
isoforms, transcript structures, or another qualifying feature. A plausible
named resource whose website may contain such details can use class 4.

Assign exactly one AI_class:

1 = Direct splicing database/resource with splicing events, splice sites,
junctions, PSI, splicing regulation, sQTLs, or splice consequences.

2 = Transcript/isoform model database/resource with transcript IDs, isoform
models, exon-intron structures, reference annotations, transcript sequences, or
full-length isoforms.

3 = Transcript-level abundance or regulatory database/resource with abundance
quantified by transcript/isoform, or transcript-level/splicing QTL data.
Gene-level expression alone is class 7.

4 = Named reusable database/atlas/portal that plausibly has qualifying content,
but the abstract does not state the data level clearly enough. Use this only to
protect recall before website verification. Explicit gene-expression-only or
generic RNA resources are class 7, not class 4.

5 = Method, viewer, or web server with built-in searchable/browsable qualifying
records or reference-backed transcript/splicing content. Upload-only software,
prediction-only servers, and tools without hosted records are class 7.

6 = Uses or analyzes existing resources but does not itself introduce, maintain,
or substantially update a reusable target resource.

7 = Not a target resource: gene-expression-only; generic RNA without qualifying
transcript/splicing content; download-only/static files; paper-only; pure method
or software; downstream clinical/pathway/immune/survival analysis; unrelated; or
insufficient evidence that a reusable resource exists.

AI_check must be Yes for classes 1-5 and No for classes 6-7.

target_scope_hint must be one of: direct_splicing, transcript_model,
transcript_level_abundance, needs_web_verification, none. Use the corresponding
value for classes 1-4. For class 5, choose the built-in data level; use
needs_web_verification only when it is not distinguishable. Use none for 6-7.

resource_type must be one of: database, atlas, portal, web server, dataset,
knowledge base, consortium resource, software-data server, other, none.
url_mentioned is Yes only when the title/abstract gives a URL or direct access
location. AI_reason must be at most 20 words. Use none when no resource name or
resource type applies.

Return exactly one valid JSON object and no other text:
{"AI_class":1,"AI_check":"Yes","AI_reason":"splicing database with event records","resource_name":"ExampleDB","resource_type":"database","url_mentioned":"Yes","target_scope_hint":"direct_splicing"}
""".strip()

AI_RESULT_COLUMNS = ["AI_check", "AI_reason"]

STAGE1_RESULT_COLUMNS = ["stage1_decision", "stage1_reason"]

AI_USAGE_COLUMNS = [
    "ai_model",
    "ai_prompt_tokens",
    "ai_completion_tokens",
    "ai_reasoning_tokens",
    "ai_total_tokens",
]

STAGE2_RESULT_COLUMNS = [
    "AI_class",
    "AI_check",
    "screening_policy_version",
    "AI_reason",
    "resource_name",
    "resource_type",
    "url_mentioned",
    "target_scope_hint",
]

STAGE2_USAGE_COLUMNS = [
    "stage2_model",
    "stage2_service_tier",
    "stage2_input_tokens",
    "stage2_cached_tokens",
    "stage2_output_tokens",
    "stage2_reasoning_tokens",
    "stage2_total_tokens",
]

STAGE1_USAGE_COLUMNS = [
    "stage1_model",
    "stage1_service_tier",
    "stage1_prompt_tokens",
    "stage1_completion_tokens",
    "stage1_reasoning_tokens",
    "stage1_total_tokens",
]

AI_PREFETCH_MULTIPLIER = 2
AI_MAX_RETRIES = 2


def keyword_screen(
    reference_details_df: pd.DataFrame,
    pattern: re.Pattern[str] = DEFAULT_DATABASE_PATTERN,
    scope_pattern: Optional[re.Pattern[str]] = DEFAULT_RNA_SCOPE_PATTERN,
) -> pd.DataFrame:
    df = reference_details_df.copy()
    for column in ("title", "abstractText"):
        if column not in df.columns:
            df[column] = ""

    screen_text = (
        df["title"].fillna("").astype(str)
        + " "
        + df["abstractText"].fillna("").astype(str)
    ).str.strip()

    df["matched_terms"] = screen_text.apply(
        lambda text: sorted({match.lower() for match in pattern.findall(text)})
    )
    df["has_database_terms"] = df["matched_terms"].apply(lambda terms: len(terms) > 0)
    if scope_pattern is None:
        df["matched_scope_terms"] = [[] for _ in range(len(df))]
        df["has_rna_scope_terms"] = True
    else:
        df["matched_scope_terms"] = screen_text.apply(
            lambda text: sorted({match.lower() for match in scope_pattern.findall(text)})
        )
        df["has_rna_scope_terms"] = df["matched_scope_terms"].apply(lambda terms: len(terms) > 0)
    return df[df["has_database_terms"] & df["has_rna_scope_terms"]].reset_index(drop=True)


def split_missing_abstract(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = df.copy()
    if "abstractText" not in work.columns:
        work["abstractText"] = ""
    has_abstract = work["abstractText"].apply(lambda value: clean_str(value) != "")
    return (
        work[has_abstract].reset_index(drop=True),
        work[~has_abstract].reset_index(drop=True),
    )


def normalize_yes_no(raw: str) -> str:
    text = clean_str(raw)
    normalized = re.sub(r"[^a-zA-Z]", "", text).lower()
    if re.search(r"\byes\b", text, flags=re.IGNORECASE) or normalized.startswith("yes"):
        return "Yes"
    if re.search(r"\bno\b", text, flags=re.IGNORECASE) or normalized.startswith("no"):
        return "No"
    return "No"


def _strip_json_code_fence(raw: str) -> str:
    text = clean_str(raw)
    fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    return fence_match.group(1).strip() if fence_match else text


def parse_ai_json_response(raw: str) -> Dict[str, str]:
    text = _strip_json_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    if isinstance(data, dict):
        ai_check = normalize_yes_no(data.get("AI_check", ""))
        reason = clean_str(data.get("AI_reason", ""))
        if reason:
            return {"AI_check": ai_check, "AI_reason": reason}

    fallback_check = normalize_yes_no(raw)
    fallback_reason = "Model returned non-JSON output; parsed answer from text."
    if fallback_check == "No" and not re.search(r"\bno\b", clean_str(raw), flags=re.IGNORECASE):
        fallback_reason = "Model returned invalid output; defaulted to No."
    return {"AI_check": fallback_check, "AI_reason": fallback_reason}


def normalize_stage1_decision(raw: str) -> str:
    normalized = re.sub(r"[^a-zA-Z]", "", clean_str(raw)).lower()
    if normalized.startswith("reject"):
        return "Reject"
    if normalized.startswith("pass"):
        return "Pass"
    if normalized.startswith("unclear"):
        return "Unclear"
    return "Unclear"


def parse_stage1_json_response(raw: str) -> Dict[str, str]:
    text = _strip_json_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    if isinstance(data, dict):
        decision = normalize_stage1_decision(data.get("stage1_decision", ""))
        reason = clean_str(data.get("stage1_reason", ""))
        if reason:
            return {"stage1_decision": decision, "stage1_reason": reason}

    return {
        "stage1_decision": "Unclear",
        "stage1_reason": "Model returned invalid output; routed to expert model.",
    }


def _load_json_object(raw: str) -> Dict[str, Any]:
    text = _strip_json_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Model returned no JSON object")
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Model returned JSON that is not an object")
    return data


def _allowed_value(raw: Any, allowed: set[str], default: str) -> str:
    value = clean_str(raw)
    return value if value in allowed else default


def parse_stage2_json_response(raw: str) -> Dict[str, Any]:
    data = _load_json_object(raw)
    try:
        ai_class = int(data.get("AI_class", ""))
    except (TypeError, ValueError):
        raise ValueError("Stage2 response missing valid integer AI_class")
    if ai_class not in {1, 2, 3, 4, 5, 6, 7}:
        raise ValueError(f"Stage2 response has invalid AI_class: {ai_class}")

    ai_check = "Yes" if ai_class in {1, 2, 3, 4, 5} else "No"
    resource_type = _allowed_value(
        data.get("resource_type", ""),
        {
            "database",
            "atlas",
            "portal",
            "web server",
            "dataset",
            "knowledge base",
            "consortium resource",
            "software-data server",
            "other",
            "none",
        },
        "none" if ai_class in {6, 7} else "other",
    )
    default_scope = {
        1: "direct_splicing",
        2: "transcript_model",
        3: "transcript_level_abundance",
        4: "needs_web_verification",
        5: "needs_web_verification",
        6: "none",
        7: "none",
    }[ai_class]
    target_scope_hint = _allowed_value(
        data.get("target_scope_hint", ""),
        {
            "direct_splicing",
            "transcript_model",
            "transcript_level_abundance",
            "needs_web_verification",
            "none",
        },
        default_scope,
    )
    if ai_class in {1, 2, 3, 4}:
        target_scope_hint = default_scope
    elif ai_class == 5 and target_scope_hint == "none":
        target_scope_hint = "needs_web_verification"
    elif ai_class in {6, 7}:
        target_scope_hint = "none"
    return {
        "AI_class": ai_class,
        "AI_check": ai_check,
        "screening_policy_version": SCREENING_POLICY_VERSION,
        "AI_reason": clean_str(data.get("AI_reason", "")),
        "resource_name": clean_str(data.get("resource_name", "")) or "none",
        "resource_type": resource_type,
        "url_mentioned": normalize_yes_no(data.get("url_mentioned", "")),
        "target_scope_hint": target_scope_hint,
    }


def create_openai_client(api_key: Optional[str] = None) -> Any:
    from openai import OpenAI

    resolved_key = api_key or os.getenv("OPENAI_API_KEY")
    if not resolved_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set")
    return OpenAI(api_key=resolved_key)


def supports_temperature(model: str) -> bool:
    return not clean_str(model).lower().startswith("gpt-5.5")


def usage_to_record(completion: Any, requested_model: str) -> Dict[str, Any]:
    usage = getattr(completion, "usage", None)
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else {}
    completion_details = usage_dict.get("completion_tokens_details") or {}
    return {
        "ai_model": clean_str(getattr(completion, "model", "")) or requested_model,
        "ai_prompt_tokens": usage_dict.get("prompt_tokens", ""),
        "ai_completion_tokens": usage_dict.get("completion_tokens", ""),
        "ai_reasoning_tokens": completion_details.get("reasoning_tokens", ""),
        "ai_total_tokens": usage_dict.get("total_tokens", ""),
    }


def usage_to_stage1_record(
    completion: Any,
    requested_model: str,
    requested_service_tier: str = DEFAULT_STAGE1_SERVICE_TIER,
) -> Dict[str, Any]:
    usage = getattr(completion, "usage", None)
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else {}
    completion_details = usage_dict.get("completion_tokens_details") or {}
    return {
        "stage1_model": clean_str(getattr(completion, "model", "")) or requested_model,
        "stage1_service_tier": clean_str(getattr(completion, "service_tier", ""))
        or requested_service_tier,
        "stage1_prompt_tokens": usage_dict.get("prompt_tokens", ""),
        "stage1_completion_tokens": usage_dict.get("completion_tokens", ""),
        "stage1_reasoning_tokens": completion_details.get("reasoning_tokens", ""),
        "stage1_total_tokens": usage_dict.get("total_tokens", ""),
    }


def usage_to_stage2_record(
    response: Any,
    requested_model: str,
    requested_service_tier: str = DEFAULT_STAGE2_SERVICE_TIER,
) -> Dict[str, Any]:
    usage = getattr(response, "usage", None)
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else {}
    input_details = usage_dict.get("input_tokens_details") or {}
    output_details = usage_dict.get("output_tokens_details") or {}
    return {
        "stage2_model": clean_str(getattr(response, "model", "")) or requested_model,
        "stage2_service_tier": clean_str(getattr(response, "service_tier", ""))
        or requested_service_tier,
        "stage2_input_tokens": usage_dict.get("input_tokens", ""),
        "stage2_cached_tokens": input_details.get("cached_tokens", ""),
        "stage2_output_tokens": usage_dict.get("output_tokens", ""),
        "stage2_reasoning_tokens": output_details.get("reasoning_tokens", ""),
        "stage2_total_tokens": usage_dict.get("total_tokens", ""),
    }


def ask_yes_no_with_usage(
    client: Any,
    title: str,
    text: str,
    question: str = DEFAULT_AI_QUESTION,
    model: str = "gpt-5.4",
) -> tuple[Dict[str, str], Dict[str, Any]]:
    params = {
        "model": model,
        "max_completion_tokens": 100,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict biomedical literature classifier. "
                    "You must return only the requested JSON object."
                ),
            },
            {
                "role": "user",
                "content": f"""
Title:
{title}

Text:
{text}

Question:
{question}

Output rule:
Return exactly one valid JSON object and no extra text.
""".strip(),
            },
        ],
    }
    if supports_temperature(model):
        params["temperature"] = 0

    completion = client.chat.completions.create(**params)
    raw = completion.choices[0].message.content or ""
    return parse_ai_json_response(raw), usage_to_record(completion, model)


def ask_stage1_with_usage(
    client: Any,
    title: str,
    text: str,
    question: str = DEFAULT_STAGE1_TRIAGE_QUESTION,
    model: str = "gpt-5.4-mini",
    service_tier: str = DEFAULT_STAGE1_SERVICE_TIER,
) -> tuple[Dict[str, str], Dict[str, Any]]:
    params = {
        "model": model,
        "max_completion_tokens": 120,
        "messages": [
            {
                "role": "system",
                "content": DEFAULT_STAGE1_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"""
Title:
{title}

Text:
{text}

Question:
{question}

Output rule:
Return exactly one valid JSON object and no extra text.
""".strip(),
            },
        ],
    }
    if supports_temperature(model):
        params["temperature"] = 0
    if clean_str(service_tier):
        params["service_tier"] = clean_str(service_tier)

    completion = client.chat.completions.create(**params)
    raw = completion.choices[0].message.content or ""
    return parse_stage1_json_response(raw), usage_to_stage1_record(
        completion,
        model,
        requested_service_tier=clean_str(service_tier),
    )


def ask_stage2_with_usage(
    client: Any,
    title: str,
    text: str,
    prompt: str = DEFAULT_STAGE2_EXPERT_PROMPT,
    model: str = "gpt-5.5",
    service_tier: str = DEFAULT_STAGE2_SERVICE_TIER,
    reasoning_effort: str = DEFAULT_STAGE2_REASONING_EFFORT,
    prompt_cache_key: str = DEFAULT_STAGE2_PROMPT_CACHE_KEY,
    prompt_cache_retention: str = DEFAULT_STAGE2_PROMPT_CACHE_RETENTION,
    max_output_tokens: int = DEFAULT_STAGE2_MAX_OUTPUT_TOKENS,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    params: Dict[str, Any] = {
        "model": model,
        "max_output_tokens": max_output_tokens,
        "input": [
            {"role": "developer", "content": prompt},
            {"role": "user", "content": f"Title:\n{title}\n\nAbstract:\n{text}"},
        ],
    }
    if clean_str(service_tier):
        params["service_tier"] = clean_str(service_tier)
    if clean_str(reasoning_effort):
        params["reasoning"] = {"effort": clean_str(reasoning_effort)}
    if clean_str(prompt_cache_key):
        params["prompt_cache_key"] = clean_str(prompt_cache_key)
    if clean_str(prompt_cache_retention):
        params["prompt_cache_retention"] = clean_str(prompt_cache_retention)

    response = client.responses.create(**params)
    raw = clean_str(getattr(response, "output_text", ""))
    return parse_stage2_json_response(raw), usage_to_stage2_record(
        response,
        model,
        requested_service_tier=clean_str(service_tier),
    )


def ask_yes_no(
    client: Any,
    title: str,
    text: str,
    question: str = DEFAULT_AI_QUESTION,
    model: str = "gpt-5.4",
) -> str:
    result, _ = ask_yes_no_with_usage(client, title, text, question=question, model=model)
    return result["AI_check"]


def paper_key_from_mapping(row: Dict[str, Any], index: int) -> str:
    doi = clean_str(row.get("doi", "")).lower()
    if doi:
        return f"doi:{doi}"
    pmid = clean_str(row.get("pmid", ""))
    if pmid:
        return f"pmid:{pmid}"
    pmcid = clean_str(row.get("pmcid", ""))
    if pmcid:
        return f"pmcid:{pmcid}"
    title = clean_str(row.get("title", "")).lower()
    return f"title:{title}" if title else f"row:{index}"


def paper_key(row: pd.Series, index: int) -> str:
    return paper_key_from_mapping(row.to_dict(), index)


def ai_targets_to_fetch(work: pd.DataFrame, completed_rows: Iterable[str]) -> List[Dict[str, Any]]:
    completed = set(completed_rows)
    targets: List[Dict[str, Any]] = []
    for index, row in work.iterrows():
        record = row.to_dict()
        key = paper_key_from_mapping(record, index)
        if key in completed:
            continue
        targets.append(
            {
                "index": index,
                "key": key,
                "record": record,
                "title": clean_str(record.get("title", "")),
                "text": clean_str(record.get("abstractText", "")),
            }
        )
    return targets


def sort_ai_rows(rows: List[Dict[str, Any]], work: pd.DataFrame, fieldnames: List[str]) -> pd.DataFrame:
    result = pd.DataFrame(rows, columns=fieldnames)
    if result.empty:
        return result
    order = {
        paper_key_from_mapping(row.to_dict(), index): position
        for position, (index, row) in enumerate(work.iterrows())
    }
    if "paper_key" in result.columns:
        result["_ai_order"] = result["paper_key"].map(order).fillna(len(order)).astype(int)
        result = result.sort_values("_ai_order").drop(columns=["_ai_order"])
        result = result.drop(columns=["paper_key"])
    return result.reset_index(drop=True)


def _thread_openai_client(thread_local: threading.local, api_key: Optional[str]) -> Any:
    client = getattr(thread_local, "openai_client", None)
    if client is None:
        client = create_openai_client(api_key=api_key)
        thread_local.openai_client = client
    return client


def _screen_ai_target(
    target: Dict[str, Any],
    model: str,
    question: str,
    limiter: Optional[RateLimiter],
    thread_local: threading.local,
    api_key: Optional[str] = None,
    client: Any = None,
    max_retries: int = AI_MAX_RETRIES,
) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            if limiter:
                limiter.wait()
            resolved_client = client or _thread_openai_client(thread_local, api_key)
            ai_result, usage_record = ask_yes_no_with_usage(
                resolved_client,
                target["title"],
                target["text"],
                question=question,
                model=model,
            )
            record = dict(target["record"])
            record["paper_key"] = target["key"]
            record.update(ai_result)
            record.update(usage_record)
            return {"key": target["key"], "record": record}
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(min(2 ** attempt, 8))
    raise last_error or RuntimeError("AI screening failed")


def _ai_fieldnames(df: pd.DataFrame) -> list[str]:
    fieldnames = [
        column
        for column in df.columns
        if column not in {"paper_key", *AI_RESULT_COLUMNS, *AI_USAGE_COLUMNS}
    ]
    return fieldnames + ["paper_key"] + AI_RESULT_COLUMNS + AI_USAGE_COLUMNS


def ai_screen_dataframe(
    matched_df: pd.DataFrame,
    checkpoint_path: Path,
    state: PipelineState,
    model: str = "gpt-5.4",
    question: str = DEFAULT_AI_QUESTION,
    client: Any = None,
    api_key: Optional[str] = None,
    max_rows: Optional[int] = None,
    max_new_rows: Optional[int] = None,
    max_workers: int = 1,
    rate_limit_per_sec: float = 0,
    logger: Any = None,
) -> pd.DataFrame:
    work = matched_df.head(max_rows).copy() if max_rows else matched_df.copy()
    fieldnames = _ai_fieldnames(work)
    rows = read_csv_rows(checkpoint_path)
    completed = state.completed_keys("ai_rows") | {
        clean_str(row.get("paper_key", "")) for row in rows if clean_str(row.get("paper_key", ""))
    }

    if work.empty:
        empty = work.copy()
        empty["AI_check"] = []
        return empty

    targets = ai_targets_to_fetch(work, completed)
    if max_new_rows is not None:
        targets = targets[: max(0, int(max_new_rows))]
    worker_count = max(1, int(max_workers or 1))
    limiter = RateLimiter(rate_limit_per_sec) if rate_limit_per_sec and rate_limit_per_sec > 0 else None
    if not targets:
        return sort_ai_rows(rows, work, fieldnames)

    if worker_count == 1:
        thread_local = threading.local()
        resolved_client = client or create_openai_client(api_key=api_key)
        for target in tqdm(targets, total=len(targets), desc="AI Yes/No checking", unit="paper"):
            try:
                result = _screen_ai_target(
                    target,
                    model,
                    question,
                    limiter,
                    thread_local,
                    api_key=api_key,
                    client=resolved_client,
                )
                record = result["record"]
                append_csv_row(checkpoint_path, record, fieldnames)
                rows.append({field: record.get(field, "") for field in fieldnames})
                state.mark_key_complete("ai_rows", result["key"])
                completed.add(result["key"])
            except Exception as exc:
                if logger:
                    logger.exception("AI check failed for %s", target["key"])
                state.record_failure("ai_rows", target["key"], str(exc))
    else:
        thread_local = threading.local()
        target_iter = iter(targets)
        futures: Dict[Future[Dict[str, Any]], Dict[str, Any]] = {}
        max_outstanding = max(worker_count * AI_PREFETCH_MULTIPLIER, worker_count)

        def submit_next(executor: ThreadPoolExecutor) -> bool:
            try:
                target = next(target_iter)
            except StopIteration:
                return False
            future = executor.submit(
                _screen_ai_target,
                target,
                model,
                question,
                limiter,
                thread_local,
                api_key,
                client,
            )
            futures[future] = target
            return True

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for _ in range(min(max_outstanding, len(targets))):
                submit_next(executor)

            with tqdm(total=len(targets), desc="AI Yes/No checking", unit="paper") as progress:
                while futures:
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    completed_keys: List[str] = []
                    for future in done:
                        target = futures.pop(future)
                        try:
                            result = future.result()
                            record = result["record"]
                            append_csv_row(checkpoint_path, record, fieldnames)
                            rows.append({field: record.get(field, "") for field in fieldnames})
                            completed_keys.append(result["key"])
                        except Exception as exc:
                            if logger:
                                logger.exception("AI check failed for %s", target["key"])
                            state.record_failure("ai_rows", target["key"], str(exc))
                        progress.update(1)
                        submit_next(executor)
                    if completed_keys:
                        state.mark_keys_complete("ai_rows", completed_keys)
                        completed.update(completed_keys)

    return sort_ai_rows(rows, work, fieldnames)


def _normalize_decimal_identifier(value: Any) -> str:
    text = clean_str(value)
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def _normalize_doi(value: Any) -> str:
    text = clean_str(value).lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.strip()


def _normalize_title(value: Any) -> str:
    text = clean_str(value).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .")


def _stage2_benchmark_keysets(benchmark_df: pd.DataFrame) -> Dict[str, set[str]]:
    keysets: Dict[str, set[str]] = {"doi": set(), "pmid": set(), "pmcid": set(), "title": set()}
    for _, row in benchmark_df.iterrows():
        doi = _normalize_doi(row.get("doi", ""))
        if doi:
            keysets["doi"].add(doi)
        pmid = _normalize_decimal_identifier(row.get("pmid", ""))
        if pmid:
            keysets["pmid"].add(pmid)
        pmcid = clean_str(row.get("pmcid", "")).upper()
        if pmcid:
            keysets["pmcid"].add(pmcid)
        title = _normalize_title(row.get("title", ""))
        if title:
            keysets["title"].add(title)
    return keysets


def _row_matches_stage2_benchmark(row: pd.Series, keysets: Dict[str, set[str]]) -> bool:
    doi = _normalize_doi(row.get("doi", ""))
    if doi and doi in keysets["doi"]:
        return True
    pmid = _normalize_decimal_identifier(row.get("pmid", ""))
    if pmid and pmid in keysets["pmid"]:
        return True
    pmcid = clean_str(row.get("pmcid", "")).upper()
    if pmcid and pmcid in keysets["pmcid"]:
        return True
    title = _normalize_title(row.get("title", ""))
    return bool(title and title in keysets["title"])


def prioritize_stage2_benchmark_rows(
    matched_df: pd.DataFrame,
    benchmark_path: Optional[Path] = None,
) -> pd.DataFrame:
    work = matched_df.copy().reset_index(drop=True)
    work["stage2_original_order"] = range(len(work))
    work["stage2_benchmark_priority"] = False
    if benchmark_path and Path(benchmark_path).exists():
        benchmark_df = pd.read_excel(benchmark_path)
        keysets = _stage2_benchmark_keysets(benchmark_df)
        work["stage2_benchmark_priority"] = work.apply(
            lambda row: _row_matches_stage2_benchmark(row, keysets),
            axis=1,
        )
    return (
        work.sort_values(
            ["stage2_benchmark_priority", "stage2_original_order"],
            ascending=[False, True],
        )
        .reset_index(drop=True)
    )


def _screen_stage2_target(
    target: Dict[str, Any],
    model: str,
    prompt: str,
    service_tier: str,
    reasoning_effort: str,
    prompt_cache_key: str,
    prompt_cache_retention: str,
    max_output_tokens: int,
    limiter: Optional[RateLimiter],
    thread_local: threading.local,
    api_key: Optional[str] = None,
    client: Any = None,
    max_retries: int = AI_MAX_RETRIES,
) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            if limiter:
                limiter.wait()
            resolved_client = client or _thread_openai_client(thread_local, api_key)
            ai_result, usage_record = ask_stage2_with_usage(
                resolved_client,
                target["title"],
                target["text"],
                prompt=prompt,
                model=model,
                service_tier=service_tier,
                reasoning_effort=reasoning_effort,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
                max_output_tokens=max_output_tokens,
            )
            record = dict(target["record"])
            record["paper_key"] = target["key"]
            record.update(ai_result)
            record.update(usage_record)
            return {"key": target["key"], "record": record}
        except Exception as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            time.sleep(min(2 ** attempt, 8))
    raise last_error or RuntimeError("Stage2 AI screening failed")


def _stage2_fieldnames(df: pd.DataFrame) -> list[str]:
    fieldnames = [
        column
        for column in df.columns
        if column not in {"paper_key", *STAGE2_RESULT_COLUMNS, *STAGE2_USAGE_COLUMNS}
    ]
    return fieldnames + ["paper_key"] + STAGE2_RESULT_COLUMNS + STAGE2_USAGE_COLUMNS


def _validate_stage2_checkpoint_policy(rows: List[Dict[str, Any]]) -> None:
    versions = {
        clean_str(row.get("screening_policy_version", ""))
        for row in rows
    }
    versions.discard("")
    if rows and versions != {SCREENING_POLICY_VERSION}:
        found = ", ".join(sorted(versions)) or "legacy/unversioned"
        raise ValueError(
            "Stage2 checkpoint screening policy mismatch: "
            f"expected {SCREENING_POLICY_VERSION}, found {found}. "
            "Use a new run or remove the incompatible checkpoint."
        )


def stage2_screen_dataframe(
    matched_df: pd.DataFrame,
    checkpoint_path: Path,
    state: PipelineState,
    model: str = "gpt-5.5",
    prompt: str = DEFAULT_STAGE2_EXPERT_PROMPT,
    client: Any = None,
    api_key: Optional[str] = None,
    max_rows: Optional[int] = None,
    max_new_rows: Optional[int] = None,
    max_workers: int = 1,
    rate_limit_per_sec: float = 0,
    service_tier: str = DEFAULT_STAGE2_SERVICE_TIER,
    reasoning_effort: str = DEFAULT_STAGE2_REASONING_EFFORT,
    prompt_cache_key: str = DEFAULT_STAGE2_PROMPT_CACHE_KEY,
    prompt_cache_retention: str = DEFAULT_STAGE2_PROMPT_CACHE_RETENTION,
    max_output_tokens: int = DEFAULT_STAGE2_MAX_OUTPUT_TOKENS,
    logger: Any = None,
) -> pd.DataFrame:
    work = matched_df.head(max_rows).copy() if max_rows else matched_df.copy()
    fieldnames = _stage2_fieldnames(work)
    rows = read_csv_rows(checkpoint_path)
    _validate_stage2_checkpoint_policy(rows)
    completed = state.completed_keys("stage2_ai_rows") | {
        clean_str(row.get("paper_key", "")) for row in rows if clean_str(row.get("paper_key", ""))
    }

    if work.empty:
        empty = work.copy()
        for column in STAGE2_RESULT_COLUMNS:
            empty[column] = []
        return empty

    targets = ai_targets_to_fetch(work, completed)
    if max_new_rows is not None:
        targets = targets[: max(0, int(max_new_rows))]
    worker_count = max(1, int(max_workers or 1))
    limiter = RateLimiter(rate_limit_per_sec) if rate_limit_per_sec and rate_limit_per_sec > 0 else None
    if not targets:
        return sort_ai_rows(rows, work, fieldnames)

    if worker_count == 1:
        thread_local = threading.local()
        resolved_client = client or create_openai_client(api_key=api_key)
        for target in tqdm(targets, total=len(targets), desc="Stage2 expert checking", unit="paper"):
            try:
                result = _screen_stage2_target(
                    target,
                    model,
                    prompt,
                    service_tier,
                    reasoning_effort,
                    prompt_cache_key,
                    prompt_cache_retention,
                    max_output_tokens,
                    limiter,
                    thread_local,
                    api_key=api_key,
                    client=resolved_client,
                )
                record = result["record"]
                append_csv_row(checkpoint_path, record, fieldnames)
                rows.append({field: record.get(field, "") for field in fieldnames})
                state.mark_key_complete("stage2_ai_rows", result["key"])
                completed.add(result["key"])
            except Exception as exc:
                if logger:
                    logger.exception("Stage2 AI check failed for %s", target["key"])
                state.record_failure("stage2_ai_rows", target["key"], str(exc))
    else:
        thread_local = threading.local()
        target_iter = iter(targets)
        futures: Dict[Future[Dict[str, Any]], Dict[str, Any]] = {}
        max_outstanding = max(worker_count * AI_PREFETCH_MULTIPLIER, worker_count)

        def submit_next(executor: ThreadPoolExecutor) -> bool:
            try:
                target = next(target_iter)
            except StopIteration:
                return False
            future = executor.submit(
                _screen_stage2_target,
                target,
                model,
                prompt,
                service_tier,
                reasoning_effort,
                prompt_cache_key,
                prompt_cache_retention,
                max_output_tokens,
                limiter,
                thread_local,
                api_key,
                client,
            )
            futures[future] = target
            return True

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for _ in range(min(max_outstanding, len(targets))):
                submit_next(executor)

            with tqdm(total=len(targets), desc="Stage2 expert checking", unit="paper") as progress:
                while futures:
                    done, _ = wait(futures, return_when=FIRST_COMPLETED)
                    completed_keys: List[str] = []
                    for future in done:
                        target = futures.pop(future)
                        try:
                            result = future.result()
                            record = result["record"]
                            append_csv_row(checkpoint_path, record, fieldnames)
                            rows.append({field: record.get(field, "") for field in fieldnames})
                            completed_keys.append(result["key"])
                        except Exception as exc:
                            if logger:
                                logger.exception("Stage2 AI check failed for %s", target["key"])
                            state.record_failure("stage2_ai_rows", target["key"], str(exc))
                        progress.update(1)
                        submit_next(executor)
                    if completed_keys:
                        state.mark_keys_complete("stage2_ai_rows", completed_keys)
                        completed.update(completed_keys)

    return sort_ai_rows(rows, work, fieldnames)


def mark_ai_skipped(matched_df: pd.DataFrame, max_rows: Optional[int] = None) -> pd.DataFrame:
    result = matched_df.head(max_rows).copy() if max_rows else matched_df.copy()
    result["AI_check"] = "Skipped"
    return result.reset_index(drop=True)


def yes_only(ai_df: pd.DataFrame) -> pd.DataFrame:
    if "AI_check" not in ai_df.columns:
        return pd.DataFrame(columns=ai_df.columns)
    return ai_df[ai_df["AI_check"].astype(str).str.strip().str.lower() == "yes"].reset_index(drop=True)


def rebuild_web_from_doi(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    if "doi" not in result.columns:
        return result
    doi_norm = (
        result["doi"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.replace(r"^https?://(dx\.)?doi\.org/", "", regex=True)
    )
    result["web"] = doi_norm.apply(lambda doi: f"https://doi.org/{doi}" if doi else "")
    return result
