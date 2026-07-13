# DbCAS Excel 列规则

本文档定义 DbCAS Excel 数据源的标准表头格式，以及加载器和网页必须遵守的列处理规则。

## 1. 标准表头语法

需要进入网页展示体系的列使用以下格式：

```text
<位置标签,数据类型标签> column_name
```

例如：

```text
<main,t-word> database_name
<sub,t-word-doi> doi
<main,t-bool-access> accessibility
```

约束：

- `位置标签`只能是 `main` 或 `sub`。
- `数据类型标签`必须使用本文档第 3 节列出的类型之一。
- 每个带标签的表头必须且只能包含一个位置标签和一个数据类型标签。
- `column_name` 使用小写 `snake_case`，不得为空。
- 不需要展示、也不参与搜索的内部列不写 `<>` 标签，只保留普通列名。
- 去除标签后，所有 `column_name` 必须唯一。

## 2. 网页位置标签

| 标签 | 加载器内部值 | 网页位置 |
|---|---|---|
| `main` | `main` | 搜索结果主表 |
| `sub` | `expand` | 点击 **Expand** 后的详情表 |
| 无 `<>` 的格式 | `hidden` | 不显示，也不进入搜索 |

网页标题由 `column_name` 自动生成：下划线替换为空格，并将每个词的首字母大写。

示例：

```text
database_name -> Database Name
gene_expression_available -> Gene Expression Available
```

## 3. 数据类型标签

| 标签 | 数据处理 | 网页行为 |
|---|---|---|
| `t-word` | 普通文本 | 原样显示 |
| `t-word-tag` | 以 `;` 分隔多个标签 | 每项显示为标签；生成筛选下拉框 |
| `t-word-url` | 文本 URL | 渲染为可点击链接 |
| `t-word-doi` | DOI、DOI URL，或以 `;` 分隔的多个 DOI | 每个 DOI 独立渲染；有协议则直接链接，否则拼接 `https://doi.org/` |
| `t-numeric` | `pd.to_numeric(errors="coerce")` | 数值显示，支持升降序排序 |
| `t-numeric-cite` | 数值类型，同时标记为引用次数列 | 根据 `t-word-doi` 列重新请求并覆盖引用次数 |
| `t-bool` | 转换成布尔值 | Yes/No 筛选 |
| `t-bool-access` | 布尔值，同时标记为网址可访问性列 | URL 检查结果覆盖原值，并显示状态灯 |

## 4. 多值字段规则

### 4.1 通用标签字段

- `t-word-tag` 使用半角分号 `;` 分隔。
- 加载前对每一项执行首尾空白清理。
- 忽略空项。
- 同一单元格内的重复项只保留一次。
- 不使用逗号、中文分号或竖线代替分隔符。

### 4.2 多 DOI 与引用次数

- `t-word-doi` 可以包含一个或多个 DOI。
- 多个 DOI 使用半角分号 `;` 分隔。
- 每个 DOI 必须独立清理、校验和生成链接。
- 引用次数更新时，先对整表中的唯一 DOI 去重，再优先调用 Semantic Scholar 批量接口。
- 批量请求成功但个别 DOI 未返回有效结果时，才对这些 DOI 使用单条请求回退；整批请求失败时不触发大规模单条请求。
- `t-numeric-cite` 最终写入所有成功结果中的最高引用次数。
- 单个 DOI 请求失败不应使其他 DOI 的成功结果失效。
- 如果所有 DOI 都为空或请求全部失败，引用次数保留为空值，不写 `0`。

示例：

```text
10.1186/example-a;10.1093/example-b
```

若两个 DOI 的引用次数分别为 `18` 和 `42`，引用次数列最终写入 `42`。

## 5. URL 与布尔值规则

- `t-word-url` 单元格必须保存实际 URL 文本；不得只依赖无缓存值的 Excel `HYPERLINK()` 公式。
- 空 URL 使用真正的空单元格，不使用 `unknown`、`N/A` 或 `-` 作为链接值。
- `t-bool` 和 `t-bool-access` 的标准输入值为 `yes`/`no`；加载器也可以兼容 `true`/`false` 和 `1`/`0`。
- 无法识别的布尔值转换为空值，不默认为 `False`。
- `t-bool-access` 的源值会被 URL 检查结果覆盖，因此不得用该列保存不能重建的人工审核说明。

## 6. 隐藏列规则

- 隐藏列使用不带 `<>` 的普通表头，例如 `main_collection`。
- 隐藏列可以写入 SQLite，供内部逻辑使用。
- 隐藏列不得出现在主表、Expand 详情表、筛选选项或全文搜索字段中。

## 7. 加载前最低校验

加载器在写入 SQLite 前必须拒绝以下情况：

- 去标签后的列名为空或重复。
- 表头含未知位置标签或未知数据类型标签。
- 同时存在多个 `t-word-doi`、`t-numeric-cite`、`t-word-url` 或 `t-bool-access` 列，且加载器无法确定对应关系。
- 必需主键为空或重复。
- `t-word-url` 中存在非空但无法解析的 URL。

对于可修复的数据问题，加载器应输出明确的行号、ID、列名和原因，不应静默丢值。

## 8. DbCAS 标准模板表头

模板中的列必须按照以下顺序排列：

1. `main`：搜索结果主表字段。
2. `sub`：Expand 详情表字段。
3. `hidden`：无 `<>` 标签的内部字段。

`original_48_database` 不属于新模板，必须彻底删除，不得作为隐藏列保留。

### 8.1 Main 列

| 顺序 | 标准表头 | 网页标题 |
|---:|---|---|
| 1 | `<main,t-word> database_name` | Database Name |
| 2 | `<main,t-word-url> database_url` | Database Url |
| 3 | `<main,t-bool-access> accessibility` | Accessibility |
| 4 | `<main,t-numeric> year` | Year |
| 5 | `<main,t-numeric-cite> citation` | Citation |
| 6 | `<main,t-word-tag> species` | Species |
| 7 | `<main,t-word-tag> tissue_or_brain_region` | Tissue Or Brain Region |
| 8 | `<main,t-word-tag> sequencing_resolution` | Sequencing Resolution |
| 9 | `<main,t-word-tag> read_technology` | Read Technology |
| 10 | `<main,t-word-tag> classification_code` | Classification Code |

### 8.2 Sub 列

| 顺序 | 标准表头 | 网页标题 |
|---:|---|---|
| 11 | `<sub,t-word> title` | Title |
| 12 | `<sub,t-word-doi> doi` | Doi |
| 13 | `<sub,t-word-tag> disease_association` | Disease Association |
| 14 | `<sub,t-word-tag> developmental_association` | Developmental Association |
| 15 | `<sub,t-word-tag> cell_type` | Cell Type |
| 16 | `<sub,t-word> description` | Description |

### 8.3 Hidden 列

以下列不写 `<>`，不显示，也不进入搜索：

| 顺序 | 标准表头 | 用途 |
|---:|---|---|
| 17 | `id` | 内部记录标识 |
| 18 | `pmid` | 内部文献标识；允许保留分号分隔的原始文本 |
| 19 | `confirmation_reason` | 内部确认依据 |
| 20 | `qualification_basis` | 内部资格判定标签 |
| 21 | `neural_link` | 内部神经关联标记 |
| 22 | `gene_expression_available` | 内部基因表达可用性标记 |
| 23 | `visualization_methods` | 内部可视化方法信息 |
| 24 | `main_collection` | 内部集合归属标记 |

### 8.4 可直接复制的模板表头顺序

```text
<main,t-word> database_name
<main,t-word-url> database_url
<main,t-bool-access> accessibility
<main,t-numeric> year
<main,t-numeric-cite> citation
<main,t-word-tag> species
<main,t-word-tag> tissue_or_brain_region
<main,t-word-tag> sequencing_resolution
<main,t-word-tag> read_technology
<main,t-word-tag> classification_code
<sub,t-word> title
<sub,t-word-doi> doi
<sub,t-word-tag> disease_association
<sub,t-word-tag> developmental_association
<sub,t-word-tag> cell_type
<sub,t-word> description
id
pmid
confirmation_reason
qualification_basis
neural_link
gene_expression_available
visualization_methods
main_collection
```
