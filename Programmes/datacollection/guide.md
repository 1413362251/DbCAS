# Datacollection Search + Screening Guide

本文档说明 `Programmes/datacollection` 中 paper search + screening pipeline 的原理、目录结构、调用方式和完整工作流。

## 1. 这个部分负责什么

`datacollection` 负责从 Europe PMC 检索与 splicing / alternative splicing 相关的论文，然后从这些论文的参考文献中寻找可能介绍数据库、平台、资源、atlas、portal 等内容的论文。

它不直接更新 web，也不直接写入 `Programmes/database/data.db`。web 使用的是 database 部分整理后的 SQLite 数据库。

当前正式流程由以下文件组成：

- `searchscreening_pipeline.py`: 主程序，负责配置、阶段编排、恢复运行和输出文件。
- `paper_search.py`: Europe PMC 检索、参考文献拉取、参考文献详情补全。
- `paper_screening.py`: 关键词筛选、OpenAI Yes/No 判断、DOI 链接标准化。
- `pipeline_runtime.py`: run 目录、日志、`state.json`、CSV checkpoint、限速器。
- `tests/`: 单元测试。

旧 notebook 已归档到：

- `Programmes/archive/datacollection/notebooks/`

旧 Excel/log/save/handle 输出已归档到：

- `Programmes/archive/datacollection/legacy_outputs/`

## 2. 目录结构

每次 search 都会创建一个独立 run 文件夹：

```text
Programmes/datacollection/
  searchscreening_pipeline.py
  paper_search.py
  paper_screening.py
  pipeline_runtime.py
  runs/
    <run_id>/
      config.json
      state.json
      logs/
        pipeline.log
      checkpoints/
        01_seed_articles.csv
        02_reference_list_full.csv
        03_reference_details.csv
        05_ai_check.csv
        06_stage2_ai_check.csv
        07_agent_input_rows.csv
      outputs/
        01_seed_articles.xlsx
        02_reference_list_full.xlsx
        03_reference_details_dedup.xlsx
        04_keyword_screened.xlsx
        05_missing_abstract_for_codex.xlsx
        05_ai_check.xlsx
        05_ai_yes_only.xlsx
        06_stage2_ai_check.xlsx
        06_stage2_ai_yes_only.xlsx
        06_stage2_ai_check.summary.json
        07_agent_input_all.xlsx
        07_agent_input.summary.json
        07_agent_input_chunks/
          07_agent_input_part_001.xlsx
          transcript_database_curation_prompt.md
        08_chunk/
          08_result_part_001.xlsx
        08_agent_merged.xlsx
        08_agent_merged.summary.json
```

`runs/<run_id>/` 下的内容是生成结果，默认不会进入 git。`runs/.gitkeep` 只是为了保留空目录。

## 3. Pipeline 原理

### Stage 1: seed paper search

默认查询：

```text
("alternative splicing" OR splicing)
```

程序会对每个年份追加 Europe PMC 年份过滤：

```text
("alternative splicing" OR splicing) AND PUB_YEAR:[year-1 TO year]
```

如果设置了 `--seed-limit-per-year`，程序会用 Europe PMC `cursorMark` 分页取满每年的目标数量。Europe PMC 单页请求会限制在最多 `1000` 条；例如 `--seed-limit-per-year 2000` 会按约两页抓取该年的前 `2000` 个相关性结果。不设置 `--seed-limit-per-year` 时，程序保留默认单页 seed search 行为。

从 Europe PMC search API 获取文章后，提取：

- title
- keywordList
- DOI / DOI URL
- publication year
- journal
- citedByCount
- PMID / PMCID
- source
- abstractText

然后按 `citedByCount` 降序排序，并按标题去重。

输出：

```text
outputs/01_seed_articles.xlsx
```

### Stage 2: collect references

对 seed papers 中每个 PMID 调用 Europe PMC references API：

```text
/MED/{pmid}/references
```

每篇 seed paper 的参考文献会被保存为：

- seed_pmid
- ref_source
- ref_id
- ref_title
- ref_doi
- ref_pubYear

输出：

```text
outputs/02_reference_list_full.xlsx
```

并发规则：

- `--reference-workers` 是拉取 seed references 的 worker thread 数，用来并发等待网络响应。
- 限速是全局 HTTP request 限速，由同一个 `RateLimiter` 共享，不是每个线程各自限速。
- `REFERENCE_RATE_LIMIT_PER_SEC = 10` 表示所有 reference workers 合计约 10 requests/s。
- CSV checkpoint 和 `state.json` 由主线程写入，避免并发写坏文件。

### Stage 3: fetch reference details

对去重后的 `ref_source + ref_id` 补全 reference detail。

默认保守模式是一篇 reference 调用一次 Europe PMC article API：

```text
/article/{source}/{ref_id}
```

正式大规模运行可以启用批量 + 多线程模式：

```powershell
--detail-workers 8 --detail-batch-size 50
```

批量模式会按 `ref_source` 分组，把同一 source 的多个 `ref_id` 合并成 Europe PMC search query：

```text
SRC:{source} AND (EXT_ID:{ref_id_1} OR EXT_ID:{ref_id_2} OR ...)
```

如果批量 search 漏掉个别 ID，程序会自动 fallback 到单篇 `/article/{source}/{ref_id}`，保证完整性。

并发规则：

- `--detail-workers` 是 worker thread 数，用来并发等待网络响应。
- 限速是全局 HTTP request 限速，由同一个 `RateLimiter` 共享，不是每个线程各自限速。
- `DETAIL_RATE_LIMIT_PER_SEC = 9` 表示所有线程合计约 9 requests/s。
- `--detail-batch-size 50` 表示一个 request 最多补 50 个 reference，所以 ref/s 会明显高于 request/s。
- workers 只做 HTTP；CSV checkpoint 和 `state.json` 由主线程写入，避免并发写坏文件。

补全每篇 reference 的 title、DOI、abstract、journal、citation count 等字段。

详情去重规则：

1. 优先用 DOI 去重。
2. 没有 DOI 时用 PMID。
3. 没有 PMID 时用 PMCID。
4. 都没有时用标准化后的 title。

去重时保留 `citedByCount` 更高的记录。

输出：

```text
outputs/03_reference_details_dedup.xlsx
```

### Stage 4: keyword screening

Stage 4 input is now a seed-inclusive candidate pool, not only `outputs/03_reference_details_dedup.xlsx`.
The pipeline first combines `outputs/03_reference_details_dedup.xlsx` and `outputs/01_seed_articles.xlsx`, then runs keyword screening on the combined table. Dedup priority is normalized DOI, PMID, PMCID, then normalized title. Existing reference/detail rows keep their order and content; seed-only rows are appended after them. The output keeps `candidate_source` as `reference_detail` or `seed_article` for debugging seed recall.

程序把 `title + abstractText` 合并后做两层确定性关键词初筛。论文必须同时命中：

1. 资源词。
2. RNA / transcriptomics scope 词。

资源词包括：

- database
- knowledge base / knowledgebase
- dataset / data set
- catalog / catalogue
- biobank
- atlas
- portal
- web server
- resource / resources

RNA / transcriptomics scope 词包括以下组：

- RNA molecules：RNA, mRNA, pre-mRNA, ncRNA, lncRNA, long non-coding RNA, miRNA, microRNA, circRNA / circular RNA, snRNA, snoRNA, piRNA, siRNA。
- Transcriptomics：transcript, transcriptome, transcriptomic, RNA-seq / RNAseq, bulk RNA, single-cell RNA, single-nucleus RNA, scRNA-seq, snRNA-seq, expression atlas, gene expression, transcript expression。
- Isoform / exon / intron：isoform, exon, intron, exon usage, junction, splice junction, UTR, poly(A), polyadenylation。
- Splicing：splicing / splice words, alternative splicing, splice site, splice variant, spliceosome, exon skipping, intron retention。
- QTL / regulatory：eQTL, sQTL, splice QTL, splicing quantitative trait loci, expression quantitative trait loci, transcriptome-wide association。
- cDNA / EST / reference transcript terms：cDNA, EST, expressed sequence tag, gene model, reference sequence, RefSeq, GENCODE。Bare `annotation` is deliberately excluded because it admits broad non-RNA resources such as DAVID, KEGG, STRING, and iTOL.
- Variant consequence：splice-site variant, synonymous variant / mutation, RNA editing, RNA modification, RNA methylation, transcript consequence, protein isoform, proteome-supported isoform。

这样可以先免费过滤掉泛泛的 protein / drug / clinical / image / ecology 等数据库资源论文，减少后续 stage1 token。当前词库在 `Updated_List.xlsx` benchmark 上本地验证为 `191/191` 保留；在 run `search_pre_aicheck_2015_2026_seed2000_20260701_145117` 的旧 Stage 4 输出上复算，会从 `21,510` 条降到 `13,442` 条，其中有 abstract 的 AI 调用从 `21,398` 降到 `13,415`。

Stage 4 只负责高召回候选发现。命中 `gene expression`、`RNA-seq`、`transcriptomics` 或其他广义 RNA 词不代表最终合格；`transcript_splicing_v2` 的严格数据层级判定发生在 Stage 2 和 Stage 8。

输出：

```text
outputs/04_keyword_screened.xlsx
```

### Stage 5: AI screening

如果启用 AI，程序会先把关键词命中的论文分成两类：

1. 有 abstract：调用 OpenAI 做 Yes/No 判断。
2. 没有 abstract：不进入普通 API AI check，单独写入 `outputs/05_missing_abstract_for_codex.xlsx`。后续必须由 Codex agent 对这些论文逐篇执行文章搜索、官网/DOI/PMC/PubMed/Europe PMC 等页面查找，再根据检索到的标题、摘要、全文片段、网站信息或数据库页面进行鉴定。

低成本 stage1 初筛的默认策略：

- stage1 只做高召回初筛，输出 `Pass` / `Reject` / `Unclear`。
- 只有 `Reject` 会被过滤；`Pass` 和 `Unclear` 都进入后续高模型 stage2。
- stage1 默认模型建议使用 `gpt-5.4-mini`。
- stage1 默认 OpenAI `service_tier` 是 `flex`，程序常量为 `DEFAULT_STAGE1_SERVICE_TIER = "flex"`，pipeline config 字段为 `stage1_service_tier`。
- `flex` 的成本约为标准同步 API 的 50%，但响应更慢，也可能有资源不可用。大批量离线初筛推荐使用 `flex`；如果需要更稳定低延迟，可以用 `--stage1-service-tier default` 覆盖。
- 当前 benchmark `Updated_List.xlsx` 上，压缩版 stage1 prompt 使用 `gpt-5.4-mini` 可以做到 `191/191` 放行；在排除 benchmark 后的 100 条随机候选上，过滤率约 `77%`。

stage1 压缩版 prompt 的核心原则：

```text
High-recall first-pass triage for candidate transcriptomics/RNA resource papers.
If not clearly Reject, choose Pass or Unclear.
Pass or Unclear for reusable RNA/transcriptomics databases, catalogs, portals,
web servers, curated/downloadable datasets, viewers, project websites, or
supplementary reusable data. Relevant scope includes transcript annotation,
isoforms, RNA-seq/single-cell expression, exon/intron structure, splicing,
eQTLs/sQTLs, splice-QTL effects, transcriptome-wide associations, proteome-
supported isoforms, splice-site variants, synonymous variants with RNA/
transcript consequences, and mutation resources with transcript/splicing
interpretation. Reject only when clearly not reusable and clearly unrelated.
```

普通 API AI check 的判断规则是：

```text
Using the title, abstract text, and your biomedical knowledge, decide whether
this paper should be included as a candidate splicing-related database/resource
paper.

Answer Yes only if the paper introduces, describes, releases, hosts, or
substantially updates a database, atlas, catalog, portal, web server, knowledge
base, curated dataset, downloadable dataset, or online resource whose primary
biological focus is RNA splicing, alternative splicing, splice variants, splice
isoforms, splice sites, splicing regulation, or splicing-related
disease/variants.

Answer Yes for a software tool or web server only if it provides a
splicing-focused online resource, searchable database, curated dataset,
downloadable data collection, or public portal.

Answer No if the paper only uses an existing database, only presents a general
analysis method, only reports biological findings without releasing a reusable
splicing-focused resource, or describes a resource whose primary focus is not
splicing.

If the evidence from the title, abstract, and your biomedical knowledge is
insufficient, answer No.

Return exactly one JSON object:
{"AI_check":"Yes" or "No","AI_reason":"one short reason, max 40 words"}
```

程序会解析 JSON，把 `AI_check` 标准化为 `Yes` / `No`，并把 `AI_reason` 写入结果文件。虽然 prompt 要求模型把 reason 控制在 40 words 以内，程序不会截断 `AI_reason`，会保存模型返回的完整内容。如果模型偶尔返回代码块包裹的 JSON，程序也会尝试解析；如果返回非 JSON，会从文本中兜底解析 Yes/No，并在 `AI_reason` 记录格式问题。

输出：

```text
outputs/05_missing_abstract_for_codex.xlsx
outputs/05_ai_check.xlsx
outputs/05_ai_yes_only.xlsx
```

如果使用 `--no-ai` 或把 `ENABLE_AI = False`，程序不会调用 OpenAI，而是把 `AI_check` 标为 `Skipped`。

## 4. 如何调用

### 推荐方式：打开主程序改顶部参数

平时可以直接打开：

```text
Programmes/datacollection/searchscreening_pipeline.py
```

修改文件开头的参数：

```python
SEARCH_QUERY = '("alternative splicing" OR splicing)'
SEARCH_YEARS = [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016, 2015]
RUN_ID = ""
RESUME = True
SEED_LIMIT_PER_YEAR = None
SEED_DEDUPE_ORDER = "citations"
DETAIL_RATE_LIMIT_PER_SEC = 9
DETAIL_WORKERS = 1
DETAIL_BATCH_SIZE = 1
ENABLE_AI = True
OPENAI_MODEL = "gpt-5.4"
STAGE1_SERVICE_TIER = "flex"
AI_WORKERS = 1
AI_RATE_LIMIT_PER_SEC = 500 / 60
MAX_ROWS = None
```

然后运行：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py
```

如果 `RUN_ID` 为空，程序会自动创建类似这样的目录：

```text
runs/20260701_153000/
```

### 命令行方式

快速测试，不调用 OpenAI：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --limit 5 --no-ai
```

指定 run id：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id my_search_001
```

恢复中断的 run：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id my_search_001 --resume
```

修改查询词：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --query '("alternative splicing" OR splicing OR isoform)'
```

修改年份：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --years 2026-2015
```

或：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --years 2026,2025,2024
```

指定 OpenAI model：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --model gpt-5.4
```

指定低成本 stage1 初筛的 OpenAI service tier：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --stage1-service-tier flex
```

说明：stage1 默认就是 `flex`。需要低延迟或临时排查时，可以用 `--stage1-service-tier default`。

每年限制 seed articles，并保留 Europe PMC 相关性顺序：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --years 2026-2015 --seed-limit-per-year 500 --seed-dedupe-order relevance
```

更全面的 AI check 前检索推荐每年取 `2000` 篇。该模式会自动分页抓取 seed，然后合并去重、拉取所有 seed 的 references、按 reference id 去重补全详情，最后执行 keyword screening：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <your_run_id> --years 2026-2015 --seed-limit-per-year 2000 --seed-dedupe-order relevance --stop-before-ai --reference-workers 8 --detail-workers 8 --detail-batch-size 50
```

严格停在 AI check 之前：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --stop-before-ai
```

正式大规模 detail 补全推荐使用批量 + 多线程：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --reference-workers 8 --detail-workers 8 --detail-batch-size 50
```

说明：`--reference-workers 8` 和 `--detail-workers 8` 都不是每个线程各自 9 或 10 requests/s。所有线程共享对应阶段的全局 request/s 限速。`--detail-batch-size 50` 会让一个 detail request 最多处理 50 个 reference，因此实际 ref/s 会显著高于 request/s。

正式 AI check 可以使用多线程：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <your_run_id> --resume --model gpt-5.5 --ai-workers 32
```

说明：`--ai-workers 32` 表示最多 32 个 OpenAI 请求 worker 并发等待响应。CSV checkpoint 和 `state.json` 仍由主线程写入。`--ai-rate-limit-per-sec` 是所有 AI worker 共享的全局 request/s 限速，不是每个线程单独限速。默认 `AI_RATE_LIMIT_PER_SEC = 500 / 60`，即所有 AI worker 合计最多约 500 requests/min。需要临时覆盖时可以显式传 `--ai-rate-limit-per-sec <requests_per_second>`。

正式 AI check 如果需要先跑一部分来核对 token 和费用，使用 `--ai-max-new-rows`：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <your_run_id> --resume --model gpt-5.4 --ai-workers 32 --ai-max-new-rows 1000
```

说明：`--ai-max-new-rows 1000` 只限制本次新增 OpenAI 请求数。已经写入 `checkpoints/05_ai_check.csv` / `state.completed_keys.ai_rows` 的 row 会跳过，不会重复计费。如果本次没有完成全部 AI rows，pipeline 只保留 checkpoint 和 state，不写正式 `outputs/05_ai_check.xlsx` / `outputs/05_ai_yes_only.xlsx`，也不会把 `ai_screen` 标记为完成。后续去掉 `--ai-max-new-rows` 并继续 `--resume`，即可从下一条未完成 row 继续。

不要用全局 `--limit 1000` 来做正式 AI 分块。`--limit` 是 quick test 的全局 stage 限制，不是“本次新增 AI 请求数”。

### Stage2 expert AI check: transcript/splicing candidate resources

Stage2 使用筛选策略 `transcript_splicing_v2`。它仍然是网页核查前的高召回候选筛选，但目标已收窄为可检索的转录本层级或剪接数据库：直接剪接信息优先，也接受 transcript/isoform 模型、转录本级丰度和 sQTL；只有 gene-level expression、普通 RNA 关联或修改信息不再算合格范围。Stage2 的完整 prompt 写在程序常量 `DEFAULT_STAGE2_EXPERT_PROMPT`。

Stage2 默认策略：

- 输入：`outputs/05_stage1_pass_unclear_for_stage2.xlsx`。
- 输出 checkpoint：`checkpoints/06_stage2_ai_check.csv`。
- 全量完成后输出：`outputs/06_stage2_ai_check.xlsx`、`outputs/06_stage2_ai_yes_only.xlsx`、`outputs/06_stage2_ai_check.summary.json`。
- 模型：`gpt-5.5`。
- API：OpenAI Responses API。
- service tier：`flex`。
- reasoning：`none`，即关闭推理。
- prompt cache：默认 `prompt_cache_retention="24h"`，`prompt_cache_key="dbc_stage2_transcript_splicing_v2"`。
- 并发：默认建议 `--stage2-workers 32`。
- 限速：所有 Stage2 worker 共享 `500 requests/min`，不是每线程 500 requests/min。
- 输出字段：`AI_class`、`AI_check`、`screening_policy_version`、`AI_reason`、`resource_name`、`resource_type`、`url_mentioned`、`target_scope_hint`，以及 Stage2 token usage 字段。

Stage2 class 规则：

- `AI_class` 1-5 -> `AI_check=Yes`。
- `AI_class` 6-7 -> `AI_check=No`。
- class 1：直接提供 AS event、PSI、splice site/junction、splicing regulation、sQTL 或 splice consequence 的数据库/资源。
- class 2：提供 transcript/isoform ID、exon-intron model、reference transcript annotation、transcript sequence 或 full-length isoform 的数据库/资源。
- class 3：明确提供 transcript/isoform-level abundance 或 transcript/splicing QTL 的数据库/资源；gene-level expression 不属于这一类。
- class 4：明确是 named reusable database/atlas/portal，且可能有目标内容，但 abstract 没写清数据层级，必须进入网站核查。
- class 5：method/software/web server 同时提供内置、可检索或可浏览的目标转录本/剪接记录；upload-only 或 prediction-only 不算。
- class 6：只使用已有目标资源，不释放或维护新资源。
- class 7：gene-expression-only、普通 RNA 关联/interaction/modification、静态下载、纯方法、普通分析、无关或证据不足。

`AI_class` 1-5 进入 Stage 7/8，6-7 排除。Stage2 不会因为 GTEx、GENCODE、RefSeq、Expression Atlas、Allen atlas 或 legacy benchmark 身份自动放行；最终仍需 Stage 8 网页证据。`target_scope_hint` 只能是 `direct_splicing`、`transcript_model`、`transcript_level_abundance`、`needs_web_verification` 或 `none`。

Stage2 checkpoint 会记录 `screening_policy_version=transcript_splicing_v2`。旧版或没有版本字段的 checkpoint 不能在 v2 run 中 resume，必须使用新的 run ID。

Stage2 会优先处理 benchmark：

- 默认 benchmark 文件：`Programmes/archive/datacollection/legacy_outputs/Updated_List.xlsx`。
- 匹配字段：DOI、PMID、PMCID、title。
- 命中的 row 会排在 Stage2 队列前面；非 benchmark row 保持原始顺序。

先跑 1000 条正式 Stage2 checkpoint：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <v2_run_id> --resume --stage2-only --model gpt-5.5 --stage2-workers 32 --stage2-max-new-rows 1000
```

说明：`--stage2-max-new-rows 1000` 只限制本次新增 Stage2 OpenAI 请求数。已经写入 `checkpoints/06_stage2_ai_check.csv` / `state.completed_keys.stage2_ai_rows` 的 row 会跳过，不会重复计费。如果本次没有完成全部 Stage2 rows，pipeline 只保留 checkpoint 和 state，不写正式 `outputs/06_stage2_ai_check.xlsx` / `outputs/06_stage2_ai_yes_only.xlsx`，也不会把 `stage2_ai_check` 标记为完成。

统计 1000 条 token：

```powershell
python -c "import pandas as pd; p=r'Programmes/datacollection/runs/<v2_run_id>/checkpoints/06_stage2_ai_check.csv'; df=pd.read_csv(p); cols=['stage2_input_tokens','stage2_cached_tokens','stage2_output_tokens','stage2_reasoning_tokens','stage2_total_tokens']; print({c:int(pd.to_numeric(df[c], errors='coerce').fillna(0).sum()) for c in cols}); print({'rows':len(df),'yes':int((df['AI_check'].astype(str).str.lower()=='yes').sum())})"
```

继续跑剩余全量：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <v2_run_id> --resume --stage2-only --model gpt-5.5 --stage2-workers 32
```

### Stage 7: prepare agent curation batches

Optional forced include file: `Programmes/datacollection/legacy_forced_includes.xlsx`. When passed with `--stage7-forced-include`, Stage 7 appends those legacy/user-specified rows after the normal Stage 6 yes-only rows. Dedup priority is DOI, PMID, PMCID, title, then `resource_name`; agent-fill columns still start blank and Stage 8 remains responsible for website verification.

Stage 7 接在 `outputs/06_stage2_ai_yes_only.xlsx` 之后，用来准备交给浏览器 agent 逐站核查的网站表格。

这一阶段不调用 OpenAI，也不访问网页；它只做表格准备：

- 读取 Stage 6 Yes-only 候选。
- 生成只包含 agent 需要填写字段的总表。
- 默认每 25 行切分为一个 chunk 文件。
- 把 `transcript_database_curation_prompt.md` 复制到 chunk 输出目录，方便和每批 Excel 一起交给 agent。
- 使用 checkpoint 和 `state.json` 支持中断恢复。

推荐命令：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <v2_run_id> --resume --stage7-only
```

如果要指定其他 Stage 6 输入文件：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <run_id> --resume --stage7-only --stage7-input <path_to_06_stage2_ai_yes_only.xlsx> --stage7-chunk-size 25
```

默认 `--stage7-input-format stage2`。如果要把现有 curated/final workbook（例如手工整理后的 `09_finaldata.xlsx`）转换成新的严格核查 chunks，必须使用一个新的 run ID，并显式指定 `curated`：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <new_strict_v2_run_id> --stage7-only --stage7-input <path_to_09_finaldata.xlsx> --stage7-input-format curated --stage7-chunk-size 25
```

`curated` 模式保留原始 `id`、title、database name/URL、DOI、PMID、year 和 `original_48_database`，写入 `screening_policy_version=transcript_splicing_v2`，并清空旧的 yes/no、reason、focus、metadata 和 evidence 字段，避免旧判定影响 agent。`curated` 不能与 `--stage7-forced-include` 同时使用。

Forced include command:

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <run_id> --resume --stage7-only --stage7-forced-include Programmes\datacollection\legacy_forced_includes.xlsx --stage7-chunk-size 25
```

输出：

```text
outputs/07_agent_input_all.xlsx
outputs/07_agent_input.summary.json
outputs/07_agent_input_chunks/07_agent_input_part_001.xlsx
outputs/07_agent_input_chunks/07_agent_input_part_002.xlsx
...
outputs/07_agent_input_chunks/transcript_database_curation_prompt.md
checkpoints/07_agent_input_rows.csv
```

`stage2` 输入模式不会直接复制 Stage 6 的 `web` 字段，因为 `web` 通常是 DOI 链接；程序只从 `abstractText`、`title`、`AI_reason` 中预填非 DOI、非 PubMed、非 Europe PMC 的 URL。`curated` 输入模式则原样保留现有 `database_url`，但不会保留旧判定或旧 metadata。

### Stage 8: Codex sub-agent web curation and merge

Stage 8 使用 `transcript_splicing_v2` 作为最终判定标准。不存在 gene-level atlas、consortium、reference database、legacy 或历史名单自动放行例外；这些资源也必须同时证明可检索数据库功能和目标转录本/剪接内容。

Stage 8 接在 `outputs/07_agent_input_chunks/` 之后，用来让 Codex 启动多个小代理，逐批访问网站并填写 Stage 7 生成的表格。

#### Stage 8 最终表头、网页位置与默认 hidden 规则

Stage 8 最终合并表及其后续网页部署副本必须遵守 [`../database/COLUMN_RULES.md`](../database/COLUMN_RULES.md)。表头按 `main`、`sub`、无 `<>` 的 `hidden` 三组排列；`main` 进入搜索结果主表，`sub` 进入 Expand 详情表，`hidden` 不显示且不进入搜索。

下表是当前明确批准的网页特征。只有表中列出的字段可以带 `main` 或 `sub` 标签：

| 分组 | 顺序 | 标准表头 |
|---|---:|---|
| main | 1 | `<main,t-word> database_name` |
| main | 2 | `<main,t-word-url> database_url` |
| main | 3 | `<main,t-bool-access> accessibility` |
| main | 4 | `<main,t-numeric> year` |
| main | 5 | `<main,t-numeric-cite> citation` |
| main | 6 | `<main,t-word-tag> species` |
| main | 7 | `<main,t-word-tag> tissue_or_brain_region` |
| main | 8 | `<main,t-word-tag> sequencing_resolution` |
| main | 9 | `<main,t-word-tag> read_technology` |
| main | 10 | `<main,t-word-tag> classification_code` |
| sub | 11 | `<sub,t-word> title` |
| sub | 12 | `<sub,t-word-doi> doi` |
| sub | 13 | `<sub,t-word-tag> disease_association` |
| sub | 14 | `<sub,t-word-tag> developmental_association` |
| sub | 15 | `<sub,t-word-tag> cell_type` |
| sub | 16 | `<sub,t-word> description` |
| hidden | 17 | `id` |
| hidden | 18 | `pmid` |
| hidden | 19 | `confirmation_reason` |
| hidden | 20 | `qualification_basis` |
| hidden | 21 | `neural_link` |
| hidden | 22 | `gene_expression_available` |
| hidden | 23 | `visualization_methods` |
| hidden | 24 | `main_collection` |

Stage 8 对其他特征采用 **hidden by default**：凡是不在上表中的列，无论来自旧 schema、Agent 审核、分类扩展还是审计流程，都先去掉整个 `<>` 标签前缀，只保留唯一的 `snake_case` 列名。它们可以继续作为内部数据写入工作表或 SQLite，但不得进入主表、Expand、筛选项或全文搜索。任何字段要从 hidden 升为 `main` 或 `sub`，必须先同时修改本节和 `COLUMN_RULES.md`，不得只在某个 Excel 中临时加标签。

`original_48_database` 是唯一的明确删除项：新 Stage 8 schema 不再生成该列；旧 run 若仍带有该列，finalization 必须在最终发布前删除，不得改成 hidden 保留。`t-word-doi` 允许用半角 `;` 保存多个 DOI；更新 `citation` 时先对整表唯一 DOI 使用 Semantic Scholar 批量接口，批量成功但个别 DOI 缺失时才单条回退，并写入每行所有成功结果中的最高引用次数。整批请求失败时不触发大规模单条请求。

这一阶段分为两层：

- Python pipeline 负责发现 chunk、校验小代理结果、记录 checkpoint、合并最终总表。
- Codex 负责实际启动小代理、把 chunk Excel 和 prompt 分发给小代理、等待结果、继续分配下一个 chunk。

当用户说“运行 08”时，Codex 必须使用子代理工具启动小代理。默认最多并发 5 个小代理；如果用户说“运行 08，用 10 个代理”，则最多并发 10 个。

推荐的 Python 扫描/合并命令：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <v2_run_id> --resume --stage8-only --stage8-agent-count 5
```

只校验并合并已有小代理结果时：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <v2_run_id> --resume --stage8-only --stage8-merge-only
```

Codex 调度流程：

1. 先运行 Stage 8 扫描，读取 `outputs/07_agent_input_chunks/07_agent_input_part_*.xlsx`。
2. 查看 `outputs/08_agent_merged.summary.json` 或 `checkpoints/08_agent_chunks.csv` 中的 pending / invalid chunk。
3. 最多同时启动 `--stage8-agent-count` 个小代理。
4. 每个小代理只处理一个 chunk，例如：
   - 输入：`outputs/07_agent_input_chunks/07_agent_input_part_001.xlsx`
   - Prompt：`outputs/07_agent_input_chunks/transcript_database_curation_prompt.md`
   - 输出：`outputs/08_chunk/08_result_part_001.xlsx`
5. 一个小代理完成并通过校验后，Codex 再分配下一个 pending chunk，直到全部完成。
6. 最后再次运行 Stage 8，让 Python 校验所有 `08_result_part_*.xlsx` 并写出合并总表。

已创建的旧 v2 run 为保持 checkpoint 兼容，小代理结果 chunk 仍必须保持对应 Stage 7 的 30 列工作 schema，不增加、删除或重排列；这只是旧 run 的中间格式，不是最终网页列规则。迁移后的新 Stage 8 run 必须使用上面的标准。每个结果 chunk 必须满足：

- 列名和 Stage 7 完全一致。
- 行数与输入 chunk 一致。
- `id` 顺序与输入 chunk 一致。
- `id`、title、DOI、PMID、year 和 `screening_policy_version` 是来源/论文身份字段，不得在普通 row review 中改写；policy version 必须是 `transcript_splicing_v2`。`database_url` 是可修改字段：旧地址发生迁移、跳转或存在更直接的官方入口时，必须改成当前官方数据库直达 URL。最终数据库级去重时，database name 和代表论文字段按下文的 canonical database 规则处理。旧 chunk 中若仍有 `original_48_database`，它只用于兼容读取，并在 finalization 发布前删除。
- `accessibility` 只能是 `live` 或 `dead`。
- `db_type_confirmation` 和 `gene_expression_available` 只能是 `yes` 或 `no`。
- `confirmation_reason` 必须非空，简短说明 `db_type_confirmation` 的依据，例如 `searchable isoform records`、`gene expression only`、`download-only files`。
- `qualification_basis` 的受控值是 `splicing_event`、`splice_site_or_junction`、`splicing_regulation_or_sqtl`、`transcript_or_isoform_model`、`transcript_level_abundance`；多个值使用 `;`。`no` 必须写 `not_applicable`。
- `exclusion_code` 的受控值是 `not_applicable`、`gene_expression_only`、`rna_not_transcript_splicing`、`no_search_browse_query`、`download_or_static_only`、`software_or_upload_only`、`paper_only`、`insufficient_evidence`。`yes` 必须写 `not_applicable`。
- 每行必须提供一个 HTTP(S) `evidence_url`、一个 `evidence_source_type`、一段 20–300 字符 `evidence_statement` 和 `YYYY-MM-DD` 的 `evidence_checked_date`。`yes` 的同一组证据必须同时支持数据库功能和至少一个 qualification basis。
- 最终合并的 `outputs/08_agent_merged.xlsx` 必须把 `database_url` 和 `evidence_url` 中所有有效 HTTP(S) 地址写成可点击的 Excel 超链接，同时保留原始 URL 显示文本；`unknown` 等非 URL 值保持普通文本，不得伪造链接。
- `evidence_source_type` schema 仍只接受 `official_database`、`official_documentation`、`publication` 或 `web_archive`，但最终化结果使用更严格的 accessibility 配对规则：`accessibility=live` 只能使用实际访问过的 `official_database` 或 `official_documentation`；`accessibility=dead` 只能使用对应数据库的 `publication`。Web Archive 可以辅助定位历史事实，但不能作为最终 08 的单一 evidence。
- `manual_review_needed=yes` 只允许出现在 `db_type_confirmation=no` 且 `exclusion_code=insufficient_evidence` 的最高优先级未决候选。每个 25 行 chunk 最多 1 条，合并结果总量也不得超过 5%。
- `sequencing_resolution` 只能是 `bulk`、`single_cell` 或 `bulk;single_cell`。bulk 包括 bulk tissue、cell line、curated annotation、reference transcript、EST/cDNA、Sanger、microarray、普通 RNA-seq、以及其他非 single-cell 证据；只有明确 single-cell / single-nucleus 才写 `single_cell`。不要写 `unknown`。
- `read_technology` 只能是 `short`、`long` 或 `long;short`。PacBio、Oxford Nanopore、Iso-Seq、full-length long-read transcript sequencing 写 `long`；Illumina、普通 RNA-seq、EST/cDNA、Sanger、microarray、CLIP-seq、small RNA-seq、curated/reference annotation 或未明确说明 long-read 时默认写 `short`。如果 long 和 short 都有，写 `long;short`。不要写 `unknown`。
- 如果网站页面没有写清 `sequencing_resolution` 或 `read_technology`，必须继续查对应 paper 的 abstract/methods/database publication，再填写这两列。
- `disease_association` 和 `developmental_association` 不能写 `yes` / `no` / `true` / `false` / `Unclear`；查不到、没有或不适用都写 `unknown`。
- 不要把通路、蛋白、PPI、化学、药物、免疫浸润、生存分析、临床关联或泛注释数据库仅因支持 gene expression module / expression overlay / enrichment / user-upload expression analysis 标为 `db_type_confirmation=yes`。
- `db_type_confirmation=yes` 必须要求数据库自身保存或组织可检索的 transcript-level 信息，例如 transcript / isoform / AS / exon usage / junction usage / splice site / transcript abundance / transcript annotation。只有 gene-level expression 没有意义，必须标为 `no`。
- circRNA/APA 不自动属于 `yes`：circRNA 必须证明 back-splice junction、isoform 或 transcript structure；APA 必须证明 transcript/isoform structure 或其他允许的 basis。
- 泛用参考库、genome browser 和 atlas 只有在证据明确显示可搜索、可浏览或可查询的 transcript-level 记录或视图时才可以是 `yes`；资源名称本身不构成证据。
- 只有下载目录、静态 catalog、FTP/release files 或 supplementary tables 不行；即使有 transcript-level 数据，如果网站没有 meaningful web search / browse / query / record-level pages，也必须标为 `no`。
- TIMER / TIMER2.0 / TIMER3 这类以 immune infiltration、survival、mutation、clinical association 为主的网站是坏例子；如果 gene expression 只是输入、协变量、比较模块或展示层，而不是核心 transcript-level 数据库内容，必须标为 `no`。
- 当 `db_type_confirmation=no` 时，`focus` 必须写 `unknown`，`gene_expression_available` 必须写 `no`。
- 不允许写 `multiple species`、`multiple tissues`、`multiple cell types`、`various species`、`various tissues`、`various cell types` 这类泛词。能找到具体信息就写具体值；找不到统一写 `unknown`。这个规则同样适用于 species、disease、developmental stage、tissue/brain region、cell type、visualization methods 等列。
- 最终 agent 输出统一使用 `unknown` 表示查不到、不能确认、确认没有或不适用；不要用空白、`none`、`Unclear`、`NA`、`N/A`、`not specified` 表示缺失。
- 唯一允许的 `none` 是 `neural_link=none`。它表示对官网、帮助文档和数据库论文完成专项检查后，仍没有找到明确脑/神经内容；不能仅因原有数据库证据文本没有写 brain 就判 `none`。
- `neural_link=primary` 表示数据库的核心范围以脑、神经系统、神经细胞、神经发育或神经疾病为主。
- `neural_link=partial` 表示数据库虽是通用资源，但明确提供可搜索、筛选、浏览或展示的 brain、cortex、hippocampus、cerebellum、spinal cord、neuron、glia、神经疾病或神经转录本/剪接证据。仅能按任意神经相关基因名搜索，不足以单独判 `partial`。
- 每行必须独立检查 neural link，至少查看官方网站/帮助页和数据库论文，并检索 brain、neural、neuron、glia、cortex、hippocampus、cerebellum、spinal cord、neurodevelopment、neurological disease 以及具体神经细胞或脑区词。
- `neural_link=none` 不得与 `tissue_or_brain_region`、`cell_type`、`disease_association` 或 `developmental_association` 中的明确神经内容并存。
- 正向校准：AceView 是通用转录本资源，但其记录明确包含 brain 和 hippocampus 组织证据，因此应判 `partial`；SASdb 的 tissue metadata 已包含 brain，也不能继续判 `none`。

输出：

```text
outputs/08_chunk/08_result_part_001.xlsx
outputs/08_chunk/08_result_part_002.xlsx
...
outputs/08_agent_merged.xlsx
outputs/08_agent_merged.summary.json
checkpoints/08_agent_chunks.csv
```

如果仍有缺失、无效 schema、行数不一致、id 不一致或受控字段非法的 chunk，Stage 8 不会写最终 `08_agent_merged.xlsx`，只会更新 checkpoint 和 summary，下一次 resume 时继续处理未完成 chunk。

#### 下一次 Stage 8 收集的多标签数据库能力分类

> 实现状态（2026-07-20）：本节、`classification_standard.xlsx`、`full_collection_neural.xlsx` 和 DbCASweb 分类卡片已同步到 `multilabel_v2` 的 11 个小类；本次未重新运行 Stage 8。当前 Python pipeline、prompt 和测试仍校验旧的 30 列工作 schema；下一次真正运行 08 前，必须先迁移工作 schema、最终表头排序、prompt、校验器和测试，并使用新的 run ID。已有 v2 run 可以保持原工作 schema，但最终发布必须执行本节前述的 main/sub/hidden 规则并删除 `original_48_database`。

分类是数据库的**多标签能力画像**，不强制选择唯一大类或唯一小类。一个数据库可以同时属于多个大类和小类；大类只能从已确认的小类代码推导，不允许独立填写出与小类矛盾的大类。所有分类名称、代码、置信度、理由和 t-word tags 必须使用英文。

##### 开设小类的必要条件

新小类必须同时满足以下条件；“有 5 个独立产品”只是必要条件，不是充分条件：

1. 在当前 287 个数据库行中，至少有 `5` 个证据明确的**不同数据库产品**。同一数据库的不同版本、旧站点、论文更新和镜像只计为一个产品。
2. 成员共享同一种清晰的数据对象、处理机制或主要检索任务；不得为了达到数量门槛而合并不同层级概念。
3. 该能力没有被现有小类充分覆盖。能用现有小类和 t-word tag 表达时，不额外开类。
4. 官网、官方文档或数据库论文必须证明该能力实际可 search、browse、query、download 或以结构化记录提供。数据库名称、论文背景或摘要中的偶然术语不算成员证据。
5. 少于 5 个产品的主题并入最接近的现有小类，并保留细粒度 t-word tag；不得用纯 RNA modification/editing、通用门户或仅在背景中提及剪接的资源凑数。

当前分类共 `4` 个大类、`11` 个小类。下表数量来自 `full_collection_neural.xlsx` 的 287 个数据库行，允许多标签重叠。前三大类给出行数；第四类同时给出独立产品数与行数。

| Major code | Major class | Subclass code | Subclass | Current reviewed members |
|---|---|---|---|---:|
| `I` | `I. Foundational Transcriptome Resources` | `I_1` | `Genome Annotation and Transcript Reference` | `175 rows` |
| `I` | `I. Foundational Transcriptome Resources` | `I_2` | `Expression and Transcriptome Atlas` | `129 rows` |
| `II` | `II. Core Splicing Outcome Resources` | `II_1` | `Alternative Splicing Event and Isoform` | `124 rows` |
| `II` | `II. Core Splicing Outcome Resources` | `II_2` | `Splice Junction and Splice Site` | `83 rows` |
| `III` | `III. Indirect Splicing Regulation Resources` | `III_1` | `Splicing Regulation and RBP` | `16 rows` |
| `III` | `III. Indirect Splicing Regulation Resources` | `III_2` | `Splicing Variant and Disease Association` | `27 rows` |
| `IV` | `IV. Specialized RNA Forms, Splicing and Cleavage Resources` | `IV_1` | `Circular RNA and Back-Splicing` | `21 products / 22 rows` |
| `IV` | `IV. Specialized RNA Forms, Splicing and Cleavage Resources` | `IV_2` | `Chimeric and Fusion Transcript` | `8 products / 9 rows` |
| `IV` | `IV. Specialized RNA Forms, Splicing and Cleavage Resources` | `IV_3` | `lncRNA Transcript and Isoform` | `31 products / 32 rows` |
| `IV` | `IV. Specialized RNA Forms, Splicing and Cleavage Resources` | `IV_4` | `Alternative Polyadenylation and 3′-End Processing` | `12 products / 12 rows` |
| `IV` | `IV. Specialized RNA Forms, Splicing and Cleavage Resources` | `IV_5` | `Other Specialized Splicing and Cleavage` | `11 products / 11 rows` |

##### 完整小类边界与代表数据库

下表每类固定列出 3 个代表数据库。每个例子必须存在于当前 collection、`neural_link` 为 `primary` 或 `partial`，并且复核时能够进入实际 search/browse/query 页面；登录页、维护页、空白页或只返回 HTTP 的 endpoint 不合格。

| Code | Positive inclusion rule | Exclusion and cross-classification rule | Three representative examples |
|---|---|---|---|
| `I_1` | Searchable transcript/isoform IDs, exon–intron models, reference annotations, transcript sequences, ORF-aware transcript structures or full-length isoforms | Gene-only annotation or a paper that merely mentions transcripts is insufficient | Ensembl; AceView; APPRIS |
| `I_2` | Transcript/isoform-level expression, abundance, usage, fraction, TPM/FPKM/counts or other quantitative measurements | Gene-level expression alone is insufficient | GTEx Portal; Expression Atlas; CattleGTEx |
| `II_1` | AS events, PSI/delta-PSI, exon skipping, intron retention, A5SS/A3SS, MXE, differential splicing, isoform switching, cryptic-exon inclusion or other explicit splicing outcomes | Static transcript models without event-level information remain `I_1` only | VastDB; TCGA SpliceSeq; MAJIQlopedia |
| `II_2` | Splice sites, donor/acceptor sites, splice junctions, junction usage, branch points, intron boundaries, back-splice junctions, cryptic/de novo splice sites or breakpoint-linked RNA junctions | Exon names without site or junction records are insufficient | Intropolis/Snaptron; RJunBase; DBASS3/DBASS5 |
| `III_1` | RBPs, splicing factors, motifs, cis-elements, spliceosome components, CLIP binding, perturbations or other direct splicing-regulatory evidence | General transcription regulation, ordinary RBP expression or a discussion of regulation is insufficient | SpliceAid 2; POSTAR3; Nova brain-specific splicing |
| `III_2` | sQTL/isoQTL/junction-QTL, splice-altering SNP/SNV/indel/mutation or an explicit disease-associated splicing/isoform consequence | Ordinary eQTL, GWAS, mutation or structural-variant records without a splicing/isoform consequence are insufficient | CancerSplicingQTL; ValidSpliceMut; ExonSkipAD |
| `IV_1` | Searchable circRNA records, circular isoforms, back-splice junctions, circRNA expression, interactions, functions or disease associations | A background mention of circRNA is insufficient. Add `II_2` for back-splice junctions, `I_1` for full-length circular isoforms and `I_2` for quantitative expression | circBase; CIRCpedia v3; FL-circAS |
| `IV_2` | Chimeric/fusion transcripts, partner genes, fusion junctions, RNA breakpoints or breakpoint-linked transcript structures | Fusion proteins or DNA structural variants without RNA products are insufficient. `trans-splicing` remains a mechanism tag unless it produces a chimeric/fusion transcript object | ChimerDB 4.0; FusionGDB; ChiTaRS 2.1 |
| `IV_3` | A dedicated searchable lncRNA catalog/module providing lncRNA transcript records, isoforms, splice variants, transcript structures, expression, interactions or functional annotations | A general database containing a few lncRNA genes, or a paper that only mentions lncRNA, is insufficient. Add `I_1`/`I_2` when transcript models or expression are provided | NONCODE; LNCipedia; LncBook 2.0 |
| `IV_4` | APA events, poly(A) sites, cleavage sites, PAS usage, alternative terminal exons or alternative 3′UTR isoforms with searchable or downloadable records | Ordinary 3′UTR sequence or gene expression without alternative 3′-end processing is insufficient | PolyASite Atlas; APAatlas; TREND-DB |
| `IV_5` | Specialized splicing/cleavage records for exon skipping, intron retention, branch points, TE exonization, cryptic/tandem splice sites, NMD-coupled AS or lncRNA-specific AS | Controlled residual only: pure RNA modification/editing, general portals and background-only mentions are excluded | SpliceAPP Branch Point Query; ExoPLOT; NMD AS database |

##### Fusion/trans-splicing、APA 与 IV_5 边界

- `Fusion` and `chimeric` remain together because their shared database object is a transcript joining sequence from different genes or loci, normally represented by partner genes, RNA junctions and breakpoints.
- `trans-splicing` is not listed in the subclass name because it is a biogenesis mechanism. Use the t-word tag `trans-splicing`; assign `IV_2` only when the database provides chimeric/fusion transcript products.
- `APA` and alternative 3′-end processing belong to `IV_4`.
- `NMD` alone does not create a subclass. Only a dedicated NMD-coupled alternative-splicing resource may enter `IV_5`, together with applicable `II_1`/`II_2` codes.
- `IV_5` is not a generic RNA catch-all. Pure RNA modification/editing and self-splicing/group-I/group-II intron resources are excluded from the formal fourth class.

##### `classification_code` and hidden taxonomy fields

正式分类中只有 `<main,t-word-tag> classification_code` 进入网页主表。分类名称、细粒度 tags、置信度、理由和检查日期均为内部特征，按 Stage 8 的 hidden-by-default 规则使用无 `<>` 的普通列名。细粒度 tags 仍可保存 `cryptic-exon`、`cryptic-splice-site`、`aberrant-splicing`、`poison-exon`、`trans-splicing`、`spliced-leader`、`readthrough`、`gene-fusion`、`chimeric-rna`、`back-splice-junction`、`NMD`、`APA`、`RNA-editing`、`RNA-modification`、`TE-exonization`、`minor-intron` 和 `group-II-intron`，但不得因此自动获得网页展示标签。

- `main` and `t-word-tag` values must be supported independently by the database page or database paper.
- A t-word tag never creates a major/subclass membership by itself.
- Use English only and separate multiple values with `;`.
- Formal subclass codes must use the fixed order `I_1;I_2;II_1;II_2;III_1;III_2;IV_1;IV_2;IV_3;IV_4;IV_5`.
- Major classes are derived automatically: `I_1/I_2 → I`, `II_1/II_2 → II`, `III_1/III_2 → III`, and `IV_1–IV_5 → IV`.

##### 旧代码迁移

| Old code | New handling |
|---|---|
| `F1` | `I_1` |
| `F2` | `I_2` |
| `C1` | `II_1` |
| `C2` | `II_2` |
| `R1` | `III_1` |
| `R2` | `III_2` |
| `O1` | `IV_1` |
| `O2` | `IV_2` when RNA chimeric/fusion products are present |
| `O3` | Remove as a subclass; retain `trans-splicing` as a t-word tag and assign `IV_2` only for chimeric/fusion products |
| `O4` | `IV_3` after confirming a dedicated searchable lncRNA catalog/module |
| `O5` | NMD-coupled AS → `IV_5`; cryptic/aberrant event → `II_1`; cryptic/de novo site or junction → `II_2`; variant-induced case → also `III_2` |
| `O6` | APA/3′-end evidence → `IV_4`; eligible specialized splicing/cleavage residual → `IV_5`; pure modification/editing and general portals lose the IV code |
| current `IV_4` | Only explicit NMD-coupled AS moves to new `IV_5`; other NMD/general transcript resources lose the IV code |
| current `IV_5` | APA/3′-end resources move to new `IV_4` |
| current `IV_6` | Eligible exon skipping/intron retention/branchpoint/TE exonization/cryptic-tandem resources move to new `IV_5`; all other residual themes lose the IV code |

现有 `qualification_basis` 与新小类的基础映射只用于产生候选，Agent 仍必须检查实际页面：

| qualification basis | Candidate subclass |
|---|---|
| `transcript_or_isoform_model` | `I_1` |
| `transcript_level_abundance` | `I_2` |
| `splicing_event` | `II_1` |
| `splice_site_or_junction` | `II_2` |
| `splicing_regulation_or_sqtl` | 必须进一步拆分为 `III_1`、`III_2` 或两者；不得机械地同时赋值 |

每个 `database × subclass` 单独记录置信度：

| Confidence | Rule |
|---|---|
| `high` | 官方数据库/官方文档直接展示该可检索能力；dead 数据库可由对应数据库论文明确证明历史功能。证据对象与小类边界完全一致。 |
| `medium` | 有正向证据，但页面受限、只有论文摘要、查询对象不够明确，或 model/event、regulation/variant 等边界仍需核验。 |
| `low` | 主要依赖名称或间接推断，证据冲突，或无法确认数据库实际承载该能力。 |

任一候选小类不是 `high` 时，按**数据库**而不是按标签重复派发 Agent：Agent 一次访问官网和论文，复核该数据库的全部候选标签，并允许增加遗漏标签或删除误判标签。最终分类仍由主 Agent 裁决。官网为 live 时必须检查实际 search/browse/query 页面；官网 dead 时必须检查合理迁移地址并使用数据库论文。搜索结果摘要、数据库名称本身、论文背景中的偶然术语不能作为最终分类证据。

下一次 Stage 8 将分类数据直接写入合并表，而不是生成独立分类审计表；除标准表中的 `classification_code` 外，其余分类特征一律先 hidden。迁移 schema 时统一为：

| Column | Format |
|---|---|
| `classification_major` | hidden；按 `I`→`IV` 顺序写英文大类名称，多个值用 `;` |
| `classification_sub` | hidden；与 `classification_code` 一一对应的英文小类名称，多个值用 `;` |
| `<main,t-word-tag> classification_code` | 唯一进入网页主表的正式分类字段；按固定顺序写已确认小类代码 |
| `classification_t_word_tags` | hidden；细粒度英文 t-word tags，多个值用 `;`，不用于推导正式小类 |
| `classification_confidence_by_subclass` | hidden；例如 `I_2=high;II_1=high;III_2=medium` |
| `classification_reason_by_subclass` | hidden；例如 `I_2: transcript-level TPM query | III_2: searchable sQTL records`；每个理由必须指向实际查询对象 |
| `classification_checked_date` | hidden；`YYYY-MM-DD` |

`db_type_confirmation=yes` 必须至少有一个小类；`db_type_confirmation=no` 的分类字段统一写 `not_applicable`，但仍记录实际 `classification_checked_date`。大类由 `classification_code` 自动推导；分类小类、置信度和理由必须包含相同代码集合。分类计数是重叠计数，总和可以大于数据库总数。

校准案例：

- CattleGTEx 同时提供 isoform expression、AS events、junction-level data 和 sQTL，应记录 `I_2;II_1;II_2;III_2`。
- circBase 提供 back-splice-junction records，应至少记录 `II_2;IV_1`。
- ChiTaRS 提供 chimeric/fusion transcripts 和 junctions，应记录适用的 `I_1;II_2;IV_2`，并使用 `trans-splicing` t-word tag；不得恢复独立的 trans-splicing 小类。
- NMD AS database 应记录 `II_1;II_2;IV_5`；NMD 与 AS 两种能力都不能丢失。
- SpliceVault 的 cryptic-site/mis-splicing and variant evidence 应记录 `II_1;II_2;III_2` 和相关 t-word tags，不得仅因出现 cryptic splicing 而记录 `IV_5`。
- APAatlas 提供 APA/3′-end processing records，应记录 `IV_4`，并按其 transcript models/expression 能力增加 `I_1` 和/或 `I_2`。
- LncAS2Cancer 同时提供 dedicated lncRNA catalog 和 lncRNA-specific AS records，应记录 `IV_3;IV_5`，并保留适用的 `II` 标签。
- ExonSkipAD 提供 AD 脑组织 exon-skipping、致病关联及 ES-inducing SNP annotations，应记录 `II_1;III_1;III_2;IV_5`。

#### Stage 8 finalization: database-level deduplication, evidence and accessibility audit

普通 chunk 全部通过后，还必须执行统一的 Stage 8 finalization。该步骤把原始行级合并暂存为 `intermediate/08_raw_merged.xlsx`；只有数据库级去重、证据整改、URL 复检、Agent 终审以及 main/sub/hidden 表头整理全部通过，才发布 `outputs/08_agent_merged.xlsx`。最终列数不再硬编码为 30：标准表中的字段按规定展示，其他仍需保留的特征一律使用无 `<>` 的 hidden 列。

对现有 08 单独运行 finalization：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py `
  --run-id <new_finalize_run_id> `
  --stage8-finalize-only `
  --stage8-finalize-input <path_to_08_agent_merged.xlsx> `
  --stage8-url-workers 32 `
  --stage8-url-timeout 120
```

数据库级去重规则：

- 只合并 `db_type_confirmation=yes` 的重复数据库；`no` 行不因同名或同 URL 自动合并。
- 候选重复组由规范化 database name、可靠别名、规范化 URL 和经确认的最终 redirect URL 共同发现。NCBI、EBI、UCSC、GitHub、Zenodo、Dryad 等共享宿主不能仅凭域名判为同一数据库。
- Agent 必须逐组给出 `canonical_id`。相同 `canonical_id` 的行合并；错误聚类必须拆成不同 canonical ID。`canonical_id` 只写入 duplicate audit，不进入最终发布表。
- 同一数据库出现在多篇论文中时，Agent 选择一篇代表论文：优先当前版本/正式更新论文，其次是最完整证明数据库功能和目标 transcript/splicing 内容的数据库论文，再按 DOI/PMID 完整度、较新年份、较小 ID 决定。最终 ID、title、DOI、PMID 和 year 完整继承该代表行，不拼造混合论文身份。
- `original_48_database` 不参与代表行优先级，也不得进入最终发布表；旧输入中的该列在 finalization 时直接删除。Agent 可以统一 canonical database name、当前直达 URL、分类、metadata 和 evidence。
- finalization 结束前再次检测所有 `yes`；若复核升级产生新的重复组，继续增量去重，直到不存在未处理的 yes duplicate。
- 跨 chunk 新重复写入 `08_incremental_duplicate_input.xlsx`，Agent 在对应 result 中逐组选择 `merge` 或 `split`、canonical ID 和代表论文。缺少有效增量决策时管线保持 pending，不得发布最终 08；合并后若又产生新重复，继续生成下一轮增量任务。

URL 与 accessibility 复检规则：

- 调用 `Programmes/scripts/check_url_accessibility.py`，默认 32 workers、每个 URL 120 秒总加载预算；同一规范 URL 只请求一次，再映射回相关 ID。
- 纯 URL 可访问性、redirect、TLS、Continue/Proceed 路径确认等简单网页核查任务使用 `gpt-5.6-terra`、`medium`。涉及数据库资格、transcript/splicing 内容判断、重复数据库拆组/合并或代表论文选择的任务继续使用主筛选模型，不得把复杂判定降级为简单 URL 检查。
- 自动状态包括 `reachable`、`restricted`、`continue_required`、`unreachable` 和 `missing`。401/403/429、验证码、机构跳转、免责声明以及 Continue/Proceed/Enter site/I understand/继续访问等中间页不得自动判为 dead。
- 自动检查记录原始 URL、redirect 链、最终 URL、HTTP 状态、耗时、错误类别、TLS 警告和检查日期。自动结论只用于初筛，不能替代 Agent 对 canonical yes 数据库的浏览器访问。
- 每一个去重后的 canonical `db_type_confirmation=yes` 数据库都必须由 Agent 亲自进入数据库页面。出现验证页、免责声明或继续访问页时，Agent 必须点击合理的 Continue/Proceed/Enter site/I understand/继续访问动作，直到看到实际数据库内容；中间警告页不能充当 evidence。
- 进入稳定的官方数据库页面后，将 `database_url` 更新为最终直达 URL。若中间入口无法绕过且属于数据库的必要入口，可保留入口 URL，但必须在 accessibility audit 中记录完整点击路径。跨域 redirect、停放域名、恶意软件或钓鱼警告必须由 Agent 单独判断。
- `live` 的 `evidence_url` 必须是 Agent 实际访问的官方数据库页面或官方文档，并分别标记 `official_database` 或 `official_documentation`；不得用 publication、Web Archive、搜索结果或中间警告页替代。
- `dead` 只有在合理 URL 变体、迁移搜索和中间页处理后仍无法进入官方数据库时才成立，其最终 evidence 必须是对应数据库的 publication，并标记 `publication`。

finalization 输出：

```text
outputs/08_agent_merged.xlsx
outputs/08_agent_merged.summary.json
outputs/08_accessibility_audit.xlsx
outputs/08_duplicate_merge_audit.xlsx
```

`08_accessibility_audit.xlsx` 保存全部自动检查、不可达记录、continue-required 点击路径及 Agent 最终结论；`08_duplicate_merge_audit.xlsx` 保存来源 ID、canonical ID、代表论文和合并理由。最终 08 必须按本节标准依次排列 main、sub、hidden；不在批准表中的业务或审核特征去掉 `<>` 后作为 hidden，`original_48_database` 删除，`canonical_id`、旧/新值等纯审计辅助列只留在审计文件。所有有效 `database_url` 和 `evidence_url` 均写成保留原显示文本的 Excel 原生可点击超链接，并清除筛选、Excel 隐藏行和 Excel 隐藏列；这里的 hidden 指无 `<>` 的网页语义隐藏列，不是把 Excel 列设为隐藏状态。

#### 独立 URL accessibility Skill

项目级 Skill 位于 `.agents/skills/url-accessibility-audit/`。它只判断 URL 是否可访问，不修改数据库资格、证据、名称、canonical ID 或 URL 本身。适用于任何含唯一 ID 和 `database_url` 的 XLSX、CSV、TSV 或 JSONL 表格，不限于 Stage 8 文件。

自然语言调用示例：

```text
检查这份表的 database_url，输出完整 audit 和 accessibility 增强副本。
```

首次运行固定交付：

```text
<input_stem>_url_accessibility_audit.xlsx
<input_stem>_accessibility_updated.xlsx
```

原文件保持不变。增强副本逐工作表、逐行、逐字段继承当前输入，只允许更新现有 accessibility 列；若原表没有该列，则在目标数据表末尾新增普通 `accessibility` 列。无法确定的记录保留原值；原表没有旧值时保持空白。

自动检查继续复用 `Programmes/scripts/check_url_accessibility.py`，显式使用 32 workers、每 URL 120 秒总预算和跨进程同 host 每秒 1 个请求。干净的 `reachable` 可自动判 `live`；`restricted`、`continue_required`、`unreachable`、`missing`、TLS warning 和跨 host redirect 进入最多 3 个并发浏览 Agent 的风险队列。简单 URL 浏览优先使用当时确实可用的 Terra medium；若不可用，选择当时的高性价比浏览模型和 medium 推理，并逐行记录实际模型，不得把计划模型写成实际模型。

增量调用示例：

```text
使用上次 audit 增量复查当前表，只让 Agent 检查发生变化的数据库。
```

此模式输入“当前表 + 上次完整 audit”，按唯一 ID 对齐。风险指纹只包含自动状态、规范化最终 URL、HTTP 状态类别、TLS warning 和跨 host redirect；耗时与错误文字变化不触发复核。URL 改变、新 ID、当前表缺失的旧 ID、风险指纹变化，以及上次没有最终 Agent 结论的风险记录进入复核；指纹未变化时复用上次结论。当前表缺失的旧 ID 继续保留在新 audit 中，但不会进入增强副本。

底层数据命令统一通过 `uv run` 执行；XLSX 的读取、样式/超链接/隐藏状态保留、两个最终工作簿生成、公式错误扫描和视觉检查由 `spreadsheets:Spreadsheets` 完成，验证页、Continue/Proceed、跨域跳转与疑似失效页面由 `browser:control-in-app-browser` 实际访问。CLI、字段 schema 和 Agent 受控输出分别记录在 Skill 的 `SKILL.md`、`references/audit_schema.md` 与 `references/agent_review_prompt.md`。

## 5. 恢复机制

Pipeline 可以在中断后继续运行。

核心文件：

```text
runs/<run_id>/state.json
```

它记录：

- 当前阶段
- 已完成阶段
- 已完成的 year / PMID / reference id / AI row
- 每个阶段的输出路径
- 每个阶段的行数
- 失败记录

长循环会持续写 checkpoint：

```text
checkpoints/01_seed_articles.csv
checkpoints/02_reference_list_full.csv
checkpoints/03_reference_details.csv
checkpoints/05_ai_check.csv
checkpoints/06_stage2_ai_check.csv
checkpoints/07_agent_input_rows.csv
checkpoints/08_agent_chunks.csv
```

恢复时：

- 已完成的 stage 会直接跳过。
- 未完成 stage 会读取 checkpoint。
- 已处理的 year / PMID / reference / AI row 会跳过。
- OpenAI 判断失败的 row 不会被标记完成，下次 resume 时可以重试。
- Stage2 使用 `state.completed_keys.stage2_ai_rows` 跟踪已经完成的 expert check row。
- Stage7 使用 `state.completed_keys.agent_prep_rows` 跟踪已经准备好的 agent input row；全量完成前只保留 checkpoint，不写最终总表和 chunk 文件。
- Stage8 使用 `state.completed_keys.agent_curation_chunks` 跟踪已经校验通过的小代理结果 chunk；全量完成前只保留 `08_chunk/`、checkpoint 和 summary，不写最终合并总表。

## 6. 运行时监测

当前监测界面是终端进度条。

运行时会看到：

- 当前阶段
- tqdm 进度条
- 每个阶段完成后的行数摘要
- 最终 run 目录

同时日志会写入：

```text
runs/<run_id>/logs/pipeline.log
```

如果程序失败，先看：

1. 终端报错。
2. `logs/pipeline.log`
3. `state.json`
4. 对应 stage 的 checkpoint CSV。

## 7. 环境要求

基础依赖在：

```text
Programmes/requirement.txt
```

需要：

- requests
- pandas
- tqdm
- openpyxl
- openai

如果启用 AI screening，需要设置环境变量：

```powershell
$env:OPENAI_API_KEY="你的 API key"
```

不想调用 OpenAI 时使用：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --no-ai
```

## 8. 运行前参数确认

每次用户说要执行一轮 search、paper search、screening、或“跑到 AIcheck 前”时，agent 必须先一次性列出所有需要确认的参数，并且每个参数都要先给出建议值和简短理由。不能只确认 run id、AI 开关或年份，也不能让用户自己从零补全参数。

agent 给出的确认信息必须至少包含以下项目：

1. 搜索主题 / 查询词。
   - 建议值示例：`("alternative splicing" OR splicing)`。
   - 简短理由示例：这是当前 pipeline 的默认主题，覆盖 splicing 和 alternative splicing。
   - 必须询问用户是否保持默认，还是加入 `isoform`、`database`、`resource`、`atlas`、`portal` 等限制词。

2. 年份范围。
   - 建议值示例：`2015-最新年份`。
   - 如果当前年份是 2026，命令参数应建议为 `--years 2026-2015`。
   - 必须明确说明起止年份，不能只说“最新”。

3. 每年取多少 seed articles。
   - 建议值示例：正式检索每年取 `300` 篇；快速测试每年取 `20` 或 `50` 篇。
   - 必须询问用户要每年取多少篇，或者是否全量不限制。
   - 注意：当前命令行的 `--limit` 是全局限制，不是每年限制。如果用户要求“每年 N 篇”，agent 应先检查代码是否已有 per-year limit；没有时应先修改 pipeline，再运行。

4. 排序和保留规则。
   - 建议值示例：按 `citedByCount` 降序优先保留高引用论文。
   - 必须询问用户是否确认这个排序规则。

5. 运行终点。
   - 建议值示例：如果用户说“到 AIcheck 之前”，建议只跑到 `outputs/04_keyword_screened.xlsx`，不调用 OpenAI，也不生成正式 AI 判断。
   - 注意：`--no-ai` 的含义是进入 Stage 5 并把 `AI_check` 标为 `Skipped`，不等同于严格停在 AIcheck 前。

6. AI 设置。
   - 如果运行终点包含 AI screening，必须确认是否启用 AI、使用哪个模型、是否已有 `OPENAI_API_KEY`。
   - 如果运行终点在 AIcheck 前，必须明确说明不会调用 OpenAI。

7. run id 和是否 resume。
   - 建议值示例：新检索建议使用类似 `search_pre_aicheck_2015_2026` 的 run id，并设置为新 run，不 resume。
   - 如果用户指定已有 run id 或要求恢复中断任务，再使用 `--resume`。

8. Stage 3 并发和批量设置。
   - 建议值示例：正式大规模检索使用 `--detail-workers 8 --detail-batch-size 50`。
   - 简短理由示例：worker threads 并发等待网络响应，batch search 减少 HTTP request 数；漏掉的 ID 会 fallback 到单篇 article API。
   - 必须明确说明限速是全局 HTTP request/s，不是每个线程各自限速。
   - 如果担心 Europe PMC 限流，可以把 `DETAIL_RATE_LIMIT_PER_SEC` 调低到 `8`，仍然保留 batch size 50。

9. 是否测试运行。
   - 建议值示例：正式检索使用全量；如果只是验证流程，先用小样本测试。
   - 必须区分全局 `--limit` 和“每年取 N 篇”。

推荐的确认格式：

```text
我建议这一轮使用以下参数，请确认或直接改：

1. 查询词：...
2. 年份范围：...
3. 每年 seed articles 数：...
4. 排序规则：...
5. 运行终点：...
6. AI 设置：...
7. run id / resume：...
8. Stage 3 并发 / 批量：...
9. 测试或正式运行：...
```

只有以上参数全部被用户确认后，agent 才能开始修改代码或执行 pipeline。

## 9. 推荐工作流

### 第一次测试

先用小样本、不调用 AI：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id test_001 --limit 5 --no-ai
```

确认生成：

```text
runs/test_001/outputs/
```

### 正式检索

打开 `searchscreening_pipeline.py`，确认：

- `SEARCH_QUERY`
- `SEARCH_YEARS`
- `ENABLE_AI`
- `OPENAI_MODEL`
- `MAX_ROWS = None`

然后运行：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <your_run_id>
```

如果目标是“2015 到最新、每年 2000 篇、按 Europe PMC 相关性、跑到 AI check 前、使用批量 + 多线程”，推荐命令：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <your_run_id> --years 2026-2015 --seed-limit-per-year 2000 --seed-dedupe-order relevance --stop-before-ai --reference-workers 8 --detail-workers 8 --detail-batch-size 50
```

### 中断后恢复

如果中途断网、API 报错、电脑重启，重新运行：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <your_run_id> --resume
```

### 后续人工整理

Pipeline 的最终候选文件通常是：

```text
runs/<run_id>/outputs/05_ai_yes_only.xlsx
```

这个文件可以作为人工检查和整理数据库候选条目的起点。

整理完成后，是否进入 `Programmes/database/data.xlsx` 是另一个人工/数据库维护步骤，不属于 datacollection pipeline 自动处理范围。

## 10. 测试

单元测试：

```powershell
python -m unittest discover Programmes/datacollection/tests
```

有限真实运行：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --limit 5 --no-ai
```

恢复验证：

```powershell
python Programmes\datacollection\searchscreening_pipeline.py --run-id <existing_run_id> --resume --limit 5 --no-ai
```
