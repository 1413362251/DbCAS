"""Generate the DbCAS database landscape figures from the curated workbooks.

The script treats each workbook row as one database, counts multi-value fields
as database coverage (not mutually exclusive composition), and exports every
figure as both SVG and 600 dpi PNG.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


DEFAULT_DATA = Path(
    r"D:\DbCAS\Programmes\datacollection\core_data\full_collection_neural.xlsx"
)
DEFAULT_CLASSIFICATION = Path(
    r"D:\DbCAS\Programmes\datacollection\core_data\classification_standard.xlsx"
)
DEFAULT_OUTPUT = Path(r"D:\DbCAS\Programmes\paperstatistic")

COL = {
    "id": "id",
    "name": "<main,t-word> database_name",
    "year": "<main,t-numeric> year",
    "accessibility": "<main,t-bool-access> accessibility",
    "species": "<main,t-word-tag> species",
    "disease": "<sub,t-word-tag> disease_association",
    "development": "<sub,t-word-tag> developmental_association",
    "region": "<main,t-word-tag> tissue_or_brain_region",
    "resolution": "<main,t-word-tag> sequencing_resolution",
    "read": "<main,t-word-tag> read_technology",
    "classification": "<main,t-word-tag> classification_code",
}

UNKNOWN = "Unknown / not specified"

# Shared visual system.
INK = "#262626"
GRID = "#E4E7EA"
AXIS = "#6B7075"
BLUE = "#3E6D8E"
LIGHT_BLUE = "#8FB7CF"
GOLD = "#D6A64B"
ORANGE = "#D97941"
OLIVE = "#7A8F55"
GREY = "#B8BEC5"
EDGE = "#4B535A"

MAJOR_COLORS = {
    "I": BLUE,
    "II": GOLD,
    "III": ORANGE,
    "IV": OLIVE,
}


SPECIES_MAP = {
    "human": "Human",
    "homo sapiens": "Human",
    "mouse": "Mouse",
    "mus musculus": "Mouse",
    "rat": "Rat",
    "rattus norvegicus": "Rat",
    "chicken": "Chicken",
    "gallus gallus": "Chicken",
    "drosophila melanogaster": "Fruit fly",
    "drosophila": "Fruit fly",
    "fruit fly": "Fruit fly",
    "danio rerio": "Zebrafish",
    "zebrafish": "Zebrafish",
    "caenorhabditis elegans": "C. elegans",
    "c. elegans": "C. elegans",
    "pig": "Pig",
    "sus scrofa": "Pig",
    "rhesus macaque": "Rhesus macaque",
    "macaca mulatta": "Rhesus macaque",
    "cattle": "Cattle",
    "cow": "Cattle",
    "bos taurus": "Cattle",
    "dog": "Dog",
    "canis lupus familiaris": "Dog",
    "arabidopsis thaliana": "Arabidopsis thaliana",
    "arabidopsis": "Arabidopsis thaliana",
    "unknown": UNKNOWN,
}

BROAD_TAXA = {
    "all organisms",
    "animals",
    "eukaryota",
    "eukaryotic pathogens",
    "invertebrates",
    "mammalia",
    "metazoa",
    "non-human primates",
    "other eukaryotes",
    "other mammals",
    "other species",
    "other vertebrates",
    "plants",
    "platyhelminthes",
    "tunicates",
    "vertebrata",
    "vertebrates",
    "viridiplantae",
    "viruses",
}

DISEASE_MAP = {
    "unknown": UNKNOWN,
    "alzheimer disease": "Alzheimer's disease",
    "alzheimer's disease": "Alzheimer's disease",
    "parkinson disease": "Parkinson's disease",
    "parkinson's disease": "Parkinson's disease",
    "parkinson's disease association": "Parkinson's disease",
    "glioblastoma": "Glioblastoma",
    "glioblastoma multiforme": "Glioblastoma",
    "central nervous system astrocytoma grade iv": "Glioblastoma",
    "brain lower grade glioma": "Lower-grade glioma",
    "lower-grade glioma": "Lower-grade glioma",
    "autism": "Autism spectrum disorder",
    "autism spectrum disorder": "Autism spectrum disorder",
    "neurological disease": "Neurological disease",
    "neurological disorders": "Neurological disease",
    "inherited disease": "Inherited disease",
    "inherited disorders": "Inherited disease",
    "human inherited disease": "Inherited disease",
    "pediatric brain tumor": "Pediatric brain tumor",
    "pediatric brain tumors": "Pediatric brain tumor",
    "neuropsychiatric disease": "Neuropsychiatric disorder",
    "neuropsychiatric disorder": "Neuropsychiatric disorder",
    "other neuropsychiatric disease": "Neuropsychiatric disorder",
    "brain neoplasms": "Brain tumor",
    "brain tumor": "Brain tumor",
    "cancer": "Cancer",
    "cancer relevance": "Cancer",
}

REGION_MAP = {
    "unknown": UNKNOWN,
    "brain": "Brain (unspecified)",
    "mouse brain": "Brain (unspecified)",
    "normal brain": "Brain (unspecified)",
    "whole brain": "Whole brain",
    "cortex": "Cerebral cortex",
    "brain cortex": "Cerebral cortex",
    "human cortex": "Cerebral cortex",
    "mouse cerebellum": "Cerebellum",
    "mouse hippocampus": "Hippocampus",
    "caudate nucleus": "Caudate",
    "anterior cingulate": "Anterior cingulate cortex",
    "mouse prefrontal cortex": "Prefrontal cortex",
    "mesencephalon": "Midbrain",
    "ipsilateral midbrain": "Midbrain",
    "midbrain contralateral": "Midbrain",
    "brain stem": "Brainstem",
    "dorsal forebrain": "Forebrain",
    "forebrain hippocampus": "Hippocampus",
    "neocortical layers 1-6b": "Neocortex",
    "dorsolateral frontal cortex": "Dorsolateral prefrontal cortex",
}

# Only anatomical brain/CNS structures are retained. Cell populations, tumors,
# disease cohorts, and non-neural tissues are deliberately excluded.
BRAIN_CNS_REGIONS = {
    UNKNOWN,
    "Amygdala",
    "Anterior cingulate cortex",
    "Back left brain",
    "Brain (unspecified)",
    "Brainstem",
    "Caudate",
    "Central nervous system",
    "Cerebellar cortex",
    "Cerebellar hemisphere",
    "Cerebellum",
    "Cerebral cortex",
    "Cerebrum",
    "Choroid plexus",
    "Cortical plate",
    "Dorsal brain",
    "Dorsolateral prefrontal cortex",
    "Forebrain",
    "Frontal cortex",
    "Frontal pole",
    "Germinal zone",
    "Globus pallidus",
    "Hemibrain",
    "Hindbrain",
    "Hippocampal formation",
    "Hippocampus",
    "Hypothalamus",
    "Inferior frontal gyrus",
    "Infratentorial brain tissue",
    "Locus coeruleus",
    "Medial frontal gyrus",
    "Medial prefrontal cortex",
    "Medulla oblongata",
    "Midbrain",
    "Neocortex",
    "Nucleus accumbens",
    "Occipital cortex",
    "Olfactory bulb",
    "Optic nerve",
    "Optic nerve head",
    "Orbital frontal cortex",
    "Parahippocampal gyrus",
    "Pons",
    "Prefrontal cortex",
    "Putamen",
    "Retina",
    "Somatosensory cortex",
    "Spinal cord",
    "Striatum",
    "Substantia nigra",
    "Subventricular zone",
    "Superior temporal gyrus",
    "Temporal cortex",
    "Thalamus",
    "White matter",
    "Whole brain",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the DbCAS database landscape figures."
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-n", type=int, default=15)
    return parser.parse_args()


def configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "Arial",
            "font.size": 8.5,
            "axes.labelsize": 9,
            "axes.labelcolor": INK,
            "axes.edgecolor": AXIS,
            "axes.linewidth": 0.75,
            "xtick.color": INK,
            "ytick.color": INK,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "text.color": INK,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "svg.fonttype": "none",
        }
    )


def normalize_space(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip())


def sentence_case(value: str) -> str:
    value = normalize_space(value)
    return value[:1].upper() + value[1:] if value else value


def split_tags(value: object) -> list[str]:
    if pd.isna(value):
        return []
    return [
        normalize_space(tag)
        for tag in str(value).split(";")
        if normalize_space(tag)
    ]


def load_and_validate_data(data_path: Path) -> pd.DataFrame:
    if not data_path.is_file():
        raise FileNotFoundError(f"Data workbook not found: {data_path}")

    data = pd.read_excel(data_path, sheet_name="Sheet1", engine="openpyxl")
    missing = [column for column in COL.values() if column not in data.columns]
    if missing:
        raise ValueError(f"Required columns missing from data workbook: {missing}")

    if data[COL["id"]].isna().any():
        raise ValueError("Database IDs must not be missing.")
    if data[COL["id"]].duplicated().any():
        duplicates = data.loc[data[COL["id"]].duplicated(False), COL["id"]].tolist()
        raise ValueError(f"Duplicate database IDs found: {duplicates}")
    if data[COL["name"]].isna().any() or data[COL["name"]].duplicated().any():
        raise ValueError("Database names must be present and unique.")

    years = pd.to_numeric(data[COL["year"]], errors="coerce")
    observed = years.dropna()
    if not np.allclose(observed, observed.round()):
        raise ValueError("Publication years must be whole numbers.")
    if not observed.between(1800, datetime.now().year).all():
        raise ValueError("Publication years fall outside the accepted range.")

    validate_tag_tokens(data, COL["resolution"], {"bulk", "single_cell"})
    validate_tag_tokens(data, COL["read"], {"short", "long"})

    accessibility = {
        normalize_space(value).lower() for value in data[COL["accessibility"]]
    }
    invalid_accessibility = accessibility - {"yes", "no", "live", "dead"}
    if invalid_accessibility:
        raise ValueError(
            f"Unexpected accessibility values: {sorted(invalid_accessibility)}"
        )

    return data


def validate_tag_tokens(data: pd.DataFrame, column: str, allowed: set[str]) -> None:
    observed: set[str] = set()
    for value in data[column]:
        observed.update(tag.lower() for tag in split_tags(value))
    invalid = observed - allowed
    if invalid:
        raise ValueError(f"Unexpected values in {column}: {sorted(invalid)}")


def load_classification_standard(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(f"Classification workbook not found: {path}")

    raw = pd.read_excel(
        path, sheet_name="Classification standard", engine="openpyxl"
    )
    legacy_columns = {
        "<main,t-word-tag> major_code",
        "<main,t-word-tag> classification_major",
        "<main,t-word-tag> code",
        "<main,t-word-tag> classification_sub",
    }
    current_columns = {
        "Classification_major",
        "Unnamed: 1",
        "Classification_sub",
        "Unnamed: 3",
    }
    if legacy_columns.issubset(raw.columns):
        rename_map = {
            "<main,t-word-tag> major_code": "major_code",
            "<main,t-word-tag> classification_major": "major_label",
            "<main,t-word-tag> code": "code",
            "<main,t-word-tag> classification_sub": "sub_label",
        }
    elif current_columns.issubset(raw.columns):
        rename_map = {
            "Classification_major": "major_code",
            "Unnamed: 1": "major_label",
            "Classification_sub": "code",
            "Unnamed: 3": "sub_label",
        }
    else:
        raise ValueError(
            "Classification workbook does not match the current or legacy layout."
        )

    standard = raw.rename(columns=rename_map)[
        ["major_code", "major_label", "code", "sub_label"]
    ]
    standard[["major_code", "major_label"]] = standard[
        ["major_code", "major_label"]
    ].ffill()
    standard = standard.dropna(subset=["code", "sub_label"]).copy()

    for column in ["major_code", "major_label", "code", "sub_label"]:
        standard[column] = standard[column].map(normalize_space)
    if standard["code"].duplicated().any():
        raise ValueError("Classification codes must be unique.")
    if not set(standard["major_code"]).issubset(MAJOR_COLORS):
        raise ValueError("Only classification major codes I-IV are supported.")
    return standard


def explode_tags(data: pd.DataFrame, column: str) -> pd.DataFrame:
    exploded = data[[COL["id"], column]].copy()
    exploded["raw"] = exploded[column].map(split_tags)
    exploded = exploded.explode("raw", ignore_index=True)
    exploded = exploded.dropna(subset=["raw"])
    exploded = exploded[exploded["raw"].ne("")]
    return exploded[[COL["id"], "raw"]]


def normalize_labels(
    exploded: pd.DataFrame,
    mapping: dict[str, str],
    broad_taxa: set[str] | None = None,
) -> pd.DataFrame:
    normalized = exploded.copy()
    normalized["key"] = normalized["raw"].str.lower().map(normalize_space)
    normalized["label"] = normalized["key"].map(mapping)

    if broad_taxa is not None:
        broad_mask = normalized["label"].isna() & normalized["key"].isin(broad_taxa)
        normalized.loc[broad_mask, "label"] = "Broad / unspecified taxonomic scope"

    missing_mask = normalized["label"].isna()
    normalized.loc[missing_mask, "label"] = normalized.loc[
        missing_mask, "raw"
    ].map(sentence_case)
    return normalized.drop_duplicates([COL["id"], "label"])


def coverage_counts(normalized: pd.DataFrame, total: int) -> pd.DataFrame:
    counts = (
        normalized.groupby("label")[COL["id"]]
        .nunique()
        .rename("n")
        .reset_index()
    )
    counts["pct"] = counts["n"] / total * 100
    return counts


def select_top_with_unknown(counts: pd.DataFrame, top_n: int) -> pd.DataFrame:
    known = counts[counts["label"].ne(UNKNOWN)].sort_values(
        ["n", "label"], ascending=[False, True]
    )
    selected = known.head(top_n).copy()
    unknown = counts[counts["label"].eq(UNKNOWN)]
    if not unknown.empty:
        selected = pd.concat([selected, unknown], ignore_index=True)
    return selected


def exclusive_summary(labels: pd.Series, total: int) -> pd.DataFrame:
    counts = labels.value_counts().rename("n")
    summary = counts.rename_axis("label").reset_index()
    summary["pct"] = summary["n"] / total * 100
    if int(summary["n"].sum()) != total:
        raise AssertionError("Mutually exclusive categories do not sum to total.")
    return summary


def lighten(color: str, amount: float = 0.60) -> str:
    rgb = np.array(mcolors.to_rgb(color))
    return mcolors.to_hex(rgb + (1 - rgb) * amount)


def style_axis(ax: plt.Axes, grid_axis: str) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(AXIS)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(axis="both", width=0.7, length=3, color=AXIS)
    ax.set_axisbelow(True)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.65)


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Keep the requested 7.2-inch canvas width identical across every export.
    # Subplot margins are set by each chart to prevent clipping within the canvas.
    common = {"facecolor": "white"}
    fig.savefig(output_dir / f"{stem}.svg", format="svg", **common)
    fig.savefig(output_dir / f"{stem}.png", format="png", dpi=600, **common)
    plt.close(fig)


def add_vertical_labels(ax: plt.Axes, bars, summary: pd.DataFrame) -> None:
    ymax = max(summary["n"].max() * 1.23, 1)
    ax.set_ylim(0, ymax)
    for bar, row in zip(bars, summary.itertuples(index=False)):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + ymax * 0.025,
            f"{row.n} ({row.pct:.1f}%)",
            ha="center",
            va="bottom",
            fontsize=8,
            color=INK,
        )


def plot_simple_vertical(
    summary: pd.DataFrame,
    order: list[str],
    colors: list[str],
    stem: str,
    output_dir: Path,
) -> None:
    indexed = summary.set_index("label")
    missing = set(order) - set(indexed.index)
    if missing:
        raise AssertionError(f"Expected categories missing for {stem}: {sorted(missing)}")
    plotted = indexed.loc[order].reset_index()

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bars = ax.bar(
        plotted["label"],
        plotted["n"],
        color=colors,
        edgecolor=EDGE,
        linewidth=0.65,
        width=0.62,
    )
    add_vertical_labels(ax, bars, plotted)
    ax.set_ylabel("Databases (n)")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    style_axis(ax, "y")
    fig.subplots_adjust(left=0.11, right=0.98, bottom=0.19, top=0.96)
    save_figure(fig, output_dir, stem)


def plot_creation_year(data: pd.DataFrame, total: int, output_dir: Path) -> dict:
    years = pd.to_numeric(data[COL["year"]], errors="coerce")
    observed = years.dropna().astype(int)
    year_range = list(range(int(observed.min()), int(observed.max()) + 1))
    counts = observed.value_counts().reindex(year_range, fill_value=0)
    missing_n = int(years.isna().sum())

    labels = [str(year) for year in year_range] + ["Unknown"]
    values = counts.tolist() + [missing_n]
    colors = [BLUE] * len(year_range) + [GREY]
    x = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    bars = ax.bar(
        x,
        values,
        color=colors,
        edgecolor=EDGE,
        linewidth=0.45,
        width=0.78,
    )
    ymax = max(values) * 1.22
    ax.set_ylim(0, ymax)
    for bar, value in zip(bars, values):
        if value > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + ymax * 0.018,
                str(value),
                ha="center",
                va="bottom",
                fontsize=6.4,
            )
    ax.set_xticks(x, labels, rotation=90, ha="center")
    ax.tick_params(axis="x", labelsize=6.6, pad=2)
    ax.set_xlabel("Publication year")
    ax.set_ylabel("Databases (n)")
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    style_axis(ax, "y")
    fig.subplots_adjust(left=0.10, right=0.995, bottom=0.26, top=0.95)
    save_figure(fig, output_dir, "01_database_creation_year")

    peak_year = int(counts.idxmax())
    peak_n = int(counts.max())
    return {
        "peak_year": peak_year,
        "peak_n": peak_n,
        "peak_pct": peak_n / total * 100,
        "missing_year": missing_n,
        "year_min": int(observed.min()),
        "year_max": int(observed.max()),
    }


def developmental_summary(data: pd.DataFrame, total: int) -> pd.DataFrame:
    def classify(value: object) -> str:
        tags = [tag for tag in split_tags(value) if tag.lower() != "unknown"]
        if not tags:
            return UNKNOWN
        return "Multiple stages" if len(set(tags)) >= 2 else "Single stage"

    return exclusive_summary(data[COL["development"]].map(classify), total)


def token_set_summary(
    data: pd.DataFrame,
    column: str,
    display: dict[frozenset[str], str],
    total: int,
) -> pd.DataFrame:
    labels = data[column].map(
        lambda value: display.get(
            frozenset(tag.lower() for tag in split_tags(value)), "__INVALID__"
        )
    )
    if labels.eq("__INVALID__").any():
        invalid = data.loc[labels.eq("__INVALID__"), column].unique().tolist()
        raise ValueError(f"Unrecognized combinations in {column}: {invalid}")
    return exclusive_summary(labels, total)


def plot_ranked_coverage(
    selected: pd.DataFrame,
    stem: str,
    output_dir: Path,
) -> None:
    known = selected[selected["label"].ne(UNKNOWN)].sort_values(
        ["n", "label"], ascending=[False, True]
    )
    unknown = selected[selected["label"].eq(UNKNOWN)]
    plotted = pd.concat([known, unknown], ignore_index=True)

    y = np.arange(len(plotted), dtype=float)
    if not unknown.empty:
        y[-1] += 0.45
    colors = [GREY if label == UNKNOWN else BLUE for label in plotted["label"]]

    fig_height = max(5.6, 0.35 * len(plotted) + 1.35)
    fig, ax = plt.subplots(figsize=(7.2, fig_height))
    bars = ax.barh(
        y,
        plotted["n"],
        color=colors,
        edgecolor=EDGE,
        linewidth=0.6,
        height=0.62,
    )
    xmax = max(plotted["n"].max() * 1.28, 1)
    ax.set_xlim(0, xmax)
    for bar, row in zip(bars, plotted.itertuples(index=False)):
        ax.text(
            bar.get_width() + xmax * 0.015,
            bar.get_y() + bar.get_height() / 2,
            f"{row.n} ({row.pct:.1f}%)",
            ha="left",
            va="center",
            fontsize=7.7,
        )
    ax.set_yticks(y, plotted["label"])
    ax.invert_yaxis()
    ax.set_xlabel("Databases (n)")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.tick_params(axis="y", length=0)
    style_axis(ax, "x")
    ax.spines["left"].set_visible(False)
    fig.subplots_adjust(left=0.39, right=0.98, bottom=0.10, top=0.98)
    save_figure(fig, output_dir, stem)


def disease_coverage(data: pd.DataFrame, total: int, top_n: int) -> pd.DataFrame:
    exploded = explode_tags(data, COL["disease"])
    normalized = normalize_labels(exploded, DISEASE_MAP)
    return select_top_with_unknown(coverage_counts(normalized, total), top_n)


def region_coverage(data: pd.DataFrame, total: int, top_n: int) -> pd.DataFrame:
    exploded = explode_tags(data, COL["region"])
    normalized = normalize_labels(exploded, REGION_MAP)
    normalized = normalized[normalized["label"].isin(BRAIN_CNS_REGIONS)]
    return select_top_with_unknown(coverage_counts(normalized, total), top_n)


def species_coverage(data: pd.DataFrame, total: int, top_n: int) -> pd.DataFrame:
    exploded = explode_tags(data, COL["species"])
    normalized = normalize_labels(exploded, SPECIES_MAP, BROAD_TAXA)
    return select_top_with_unknown(coverage_counts(normalized, total), top_n)


def classification_rows(
    data: pd.DataFrame, standard: pd.DataFrame, total: int
) -> pd.DataFrame:
    exploded = explode_tags(data, COL["classification"])
    exploded["code"] = exploded["raw"].map(normalize_space)
    observed_codes = set(exploded["code"])
    valid_codes = set(standard["code"])
    invalid = observed_codes - valid_codes
    if invalid:
        raise ValueError(f"Classification codes missing from standard: {sorted(invalid)}")

    exploded = exploded.drop_duplicates([COL["id"], "code"])
    exploded["major_code"] = exploded["code"].str.split("_", n=1).str[0]
    sub_counts = exploded.groupby("code")[COL["id"]].nunique().to_dict()
    major_counts = exploded.groupby("major_code")[COL["id"]].nunique().to_dict()

    rows: list[dict] = []
    for major_code in standard["major_code"].drop_duplicates():
        major_label = standard.loc[
            standard["major_code"].eq(major_code), "major_label"
        ].iloc[0]
        major_n = int(major_counts.get(major_code, 0))
        rows.append(
            {
                "label": major_label,
                "n": major_n,
                "pct": major_n / total * 100,
                "major_code": major_code,
                "level": "major",
            }
        )
        for row in standard[standard["major_code"].eq(major_code)].itertuples():
            sub_n = int(sub_counts.get(row.code, 0))
            rows.append(
                {
                    "label": f"    {row.code}  {row.sub_label}",
                    "n": sub_n,
                    "pct": sub_n / total * 100,
                    "major_code": major_code,
                    "level": "sub",
                }
            )
    return pd.DataFrame(rows)


def plot_classification(rows: pd.DataFrame, output_dir: Path) -> None:
    y = np.arange(len(rows))
    colors = [
        MAJOR_COLORS[row.major_code]
        if row.level == "major"
        else lighten(MAJOR_COLORS[row.major_code])
        for row in rows.itertuples(index=False)
    ]

    fig, ax = plt.subplots(figsize=(7.2, 7.2))
    bars = ax.barh(
        y,
        rows["n"],
        color=colors,
        edgecolor=EDGE,
        linewidth=0.6,
        height=0.64,
    )
    xmax = rows["n"].max() * 1.26
    ax.set_xlim(0, xmax)
    for bar, row in zip(bars, rows.itertuples(index=False)):
        ax.text(
            bar.get_width() + xmax * 0.012,
            bar.get_y() + bar.get_height() / 2,
            f"{row.n} ({row.pct:.1f}%)",
            ha="left",
            va="center",
            fontsize=7.5,
            fontweight="bold" if row.level == "major" else "normal",
        )
    ax.set_yticks(y, rows["label"])
    for tick, level in zip(ax.get_yticklabels(), rows["level"]):
        tick.set_fontweight("bold" if level == "major" else "normal")
    ax.invert_yaxis()
    ax.set_xlabel("Databases (n)")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.tick_params(axis="y", length=0, labelsize=7.2)
    style_axis(ax, "x")
    ax.spines["left"].set_visible(False)
    fig.subplots_adjust(left=0.48, right=0.98, bottom=0.08, top=0.985)
    save_figure(fig, output_dir, "08_database_classification")


def summary_records(frame: pd.DataFrame) -> list[dict]:
    return [
        {
            "label": str(row.label),
            "n": int(row.n),
            "pct": round(float(row.pct), 1),
        }
        for row in frame.itertuples(index=False)
    ]


def main() -> None:
    args = parse_args()
    if args.top_n < 1:
        raise ValueError("--top-n must be at least 1.")

    configure_style()
    data = load_and_validate_data(args.data)
    standard = load_classification_standard(args.classification)
    total = int(data[COL["id"]].nunique())

    year_stats = plot_creation_year(data, total, args.output)

    development = developmental_summary(data, total)
    plot_simple_vertical(
        development,
        ["Single stage", "Multiple stages", UNKNOWN],
        [BLUE, GOLD, GREY],
        "02_developmental_stage_breadth",
        args.output,
    )

    resolution = token_set_summary(
        data,
        COL["resolution"],
        {
            frozenset({"bulk"}): "Bulk only",
            frozenset({"single_cell"}): "Single-cell only",
            frozenset({"bulk", "single_cell"}): "Bulk + single-cell",
        },
        total,
    )
    plot_simple_vertical(
        resolution,
        ["Bulk only", "Single-cell only", "Bulk + single-cell"],
        [BLUE, LIGHT_BLUE, GOLD],
        "03_sequencing_resolution",
        args.output,
    )

    read_technology = token_set_summary(
        data,
        COL["read"],
        {
            frozenset({"short"}): "Short-read only",
            frozenset({"long"}): "Contains long-read",
            frozenset({"short", "long"}): "Contains long-read",
        },
        total,
    )
    plot_simple_vertical(
        read_technology,
        ["Short-read only", "Contains long-read"],
        [BLUE, GOLD],
        "04_read_technology",
        args.output,
    )

    disease = disease_coverage(data, total, args.top_n)
    plot_ranked_coverage(disease, "05_disease_context_top15", args.output)

    regions = region_coverage(data, total, args.top_n)
    plot_ranked_coverage(regions, "06_brain_cns_region_top15", args.output)

    species = species_coverage(data, total, args.top_n)
    plot_ranked_coverage(species, "07_species_coverage_top15", args.output)

    class_rows = classification_rows(data, standard, total)
    plot_classification(class_rows, args.output)

    accessibility = exclusive_summary(
        data[COL["accessibility"]].map(
            lambda value: (
                "Live"
                if normalize_space(value).lower() in {"yes", "live"}
                else "Dead"
            )
        ),
        total,
    )
    plot_simple_vertical(
        accessibility,
        ["Live", "Dead"],
        [BLUE, GREY],
        "09_database_accessibility",
        args.output,
    )

    report = {
        "database_total": total,
        "year": year_stats,
        "development": summary_records(development),
        "resolution": summary_records(resolution),
        "read_technology": summary_records(read_technology),
        "disease": summary_records(disease),
        "brain_cns_region": summary_records(regions),
        "species": summary_records(species),
        "classification": summary_records(class_rows),
        "accessibility": summary_records(accessibility),
    }
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
