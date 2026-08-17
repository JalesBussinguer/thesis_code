"""Compara as distribuicoes de pares de amostras dentro de cada classe."""

from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


CLASS_NAMES = {"Fo": "Floresta", "Gl": "Graminea", "Sv": "Savana"}
COLORS = ("#0072B2", "#D55E00")
COLUMNS = ("IHH", "IHV")


def load_sample(csv_path: Path) -> dict[str, np.ndarray]:
    data = np.genfromtxt(csv_path, delimiter=",", names=True, dtype=float)
    if data.size == 0 or not data.dtype.names:
        raise ValueError(f"Arquivo sem dados: {csv_path}")

    columns = {name.lower(): name for name in data.dtype.names}
    missing = [column for column in COLUMNS if column.lower() not in columns]
    if missing:
        raise ValueError(f"Colunas ausentes em {csv_path.name}: {missing}")

    return {
        column: np.atleast_1d(data[columns[column.lower()]]).astype(float)
        for column in COLUMNS
    }


def group_files_by_class(input_dir: Path) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    for csv_path in sorted(input_dir.glob("*.csv")):
        class_code = csv_path.stem.split("_", 1)[0]
        grouped.setdefault(class_code, []).append(csv_path)
    return grouped


def plot_pair(axis: plt.Axes, values: list[np.ndarray], labels: list[str], bins: int) -> None:
    finite_values = [value[np.isfinite(value)] for value in values]
    non_empty = [value for value in finite_values if value.size]
    if not non_empty:
        axis.text(0.5, 0.5, "Sem dados", ha="center", va="center")
        return

    lower = min(value.min() for value in non_empty)
    upper = max(value.max() for value in non_empty)
    if lower == upper:
        upper = lower + 1e-6
    edges = np.linspace(lower, upper, bins + 1)
    for value, label, color in zip(finite_values, labels, COLORS):
        if value.size:
            axis.hist(value, bins=edges, density=True, histtype="step", linewidth=1.5,
                      color=color, label=f"{label} (n={value.size})")
    axis.grid(axis="y", alpha=0.2)
    axis.legend(frameon=False, fontsize=8)


def create_class_figures(input_dir: Path, output_dir: Path, bins: int, dpi: int) -> list[Path]:
    grouped = group_files_by_class(input_dir)
    if not grouped:
        raise FileNotFoundError(f"Nenhum CSV encontrado em {input_dir}")

    output_paths: list[Path] = []
    for class_code, files in sorted(grouped.items()):
        pairs = list(combinations(files, 2))
        if not pairs:
            continue
        samples = {path: load_sample(path) for path in files}
        figure, axes = plt.subplots(len(pairs), len(COLUMNS), squeeze=False,
                                    figsize=(11, max(3.0, 2.8 * len(pairs))),
                                    constrained_layout=True)
        for row, (file_a, file_b) in enumerate(pairs):
            labels = [file_a.stem, file_b.stem]
            for column_index, column in enumerate(COLUMNS):
                axis = axes[row, column_index]
                plot_pair(axis, [samples[file_a][column], samples[file_b][column]], labels, bins)
                axis.set_title(column, loc="left", fontweight="bold")
                axis.set_xlabel("Intensidade")
                axis.set_ylabel("Densidade")
                if column == "IHH":
                    axis.set_ylabel(f"{file_a.stem} x {file_b.stem}\nDensidade")

        class_name = CLASS_NAMES.get(class_code, class_code)
        figure.suptitle(f"Distribuicoes de pares de amostras - {class_name} ({class_code})",
                        fontsize=15, fontweight="bold")
        output_path = output_dir / f"{class_code}_same_class_distributions.png"
        output_dir.mkdir(parents=True, exist_ok=True)
        figure.savefig(output_path, dpi=dpi, bbox_inches="tight")
        plt.close(figure)
        output_paths.append(output_path)
    return output_paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("datasets/samples_sbsr_bsb"))
    parser.add_argument("--output-dir", type=Path, default=Path("analysis/results/same_class_distributions"))
    parser.add_argument("--bins", type=int, default=35)
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args()
    if args.bins < 1:
        parser.error("--bins deve ser maior que zero")
    for output_path in create_class_figures(args.input_dir, args.output_dir, args.bins, args.dpi):
        print(f"Resultado salvo em: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())