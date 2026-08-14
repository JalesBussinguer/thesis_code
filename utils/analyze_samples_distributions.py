"""Monta um painel com as distribuicoes das amostras SBSR-BSB."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


CLASS_NAMES = {
    "Fo": "Floresta",
    "Gl": "Graminea",
    "Sv": "Savana",
}
CLASS_COLORS = {"Fo": "#d95f02", "Gl": "#1b9e77", "Sv": "#7570b3"}
LINE_STYLES = {"IHH": "-", "IHV": "--"}


def load_sample(csv_path: Path) -> dict[str, np.ndarray]:
    data = np.genfromtxt(csv_path, delimiter=",", names=True, dtype=float)
    if data.size == 0 or not data.dtype.names:
        raise ValueError(f"Arquivo sem dados: {csv_path}")

    columns = {name.lower(): name for name in data.dtype.names}
    missing = [column for column in ("ihh", "ihv") if column not in columns]
    if missing:
        raise ValueError(f"Colunas ausentes em {csv_path.name}: {missing}")

    return {
        column.upper(): np.atleast_1d(data[columns[column]])
        for column in ("ihh", "ihv")
    }


def parse_sample_name(csv_path: Path) -> str:
    return csv_path.stem.replace("_", " ")


def plot_distribution(ax: plt.Axes, values: np.ndarray, label: str, color: str, bins: int) -> None:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return

    ax.hist(
        values,
        bins=bins,
        density=True,
        histtype="step",
        color=color,
        alpha=0.7,
        label=label,
    )
    if values.size > 1 and np.ptp(values) > 0:
        bandwidth = 1.06 * np.std(values) * values.size ** (-1 / 5)
        if bandwidth > 0:
            x = np.linspace(values.min(), values.max(), 200)
            density = np.exp(-0.5 * ((x[:, None] - values[None, :]) / bandwidth) ** 2)
            density = density.sum(axis=1) / (values.size * bandwidth * np.sqrt(2 * np.pi))
            ax.plot(x, density, color=color, linewidth=1.6, linestyle=LINE_STYLES[label.split(" - ")[-1]])


def create_panel(input_dir: Path, output_path: Path, bins: int, dpi: int) -> None:
    sample_files = sorted(input_dir.glob("*.csv"))
    if not sample_files:
        raise FileNotFoundError(f"Nenhum CSV encontrado em {input_dir}")

    columns = ("IHH", "IHV")
    samples_by_class: dict[str, dict[str, list[np.ndarray]]] = {}
    for csv_path in sample_files:
        class_code = csv_path.stem.split("_", 1)[0]
        class_samples = samples_by_class.setdefault(class_code, {column: [] for column in columns})
        sample = load_sample(csv_path)
        for column in columns:
            class_samples[column].append(sample[column])

    class_distributions = {
        class_code: {column: np.concatenate(values) for column, values in class_samples.items()}
        for class_code, class_samples in samples_by_class.items()
    }
    class_codes = sorted(class_distributions)
    class_pairs = [(class_codes[i], class_codes[j]) for i in range(len(class_codes)) for j in range(i + 1, len(class_codes))]
    n_columns = 3
    n_rows = int(np.ceil(len(class_pairs) / n_columns))
    figure, axes = plt.subplots(
        n_rows,
        n_columns,
        figsize=(15, 4.5 * n_rows),
        squeeze=False,
        constrained_layout=True,
    )
    axes_flat = axes.ravel()

    for axis, class_pair in zip(axes_flat, class_pairs):
        for class_code in class_pair:
            class_name = CLASS_NAMES.get(class_code, class_code)
            for column in columns:
                label = f"{class_name} - {column}"
                plot_distribution(
                    axis,
                    class_distributions[class_code][column],
                    label,
                    CLASS_COLORS[class_code],
                    bins,
                )
        pair_name = " x ".join(CLASS_NAMES.get(code, code) for code in class_pair)
        axis.set_title(pair_name, loc="left", fontweight="bold")
        axis.set_xlabel("Intensidade")
        axis.set_ylabel("Densidade normalizada")
        axis.set_xlim(0, 1)
        axis.grid(axis="y", alpha=0.22)
        axis.legend(frameon=False)

    for axis in axes_flat[len(class_pairs):]:
        axis.set_visible(False)

    figure.suptitle("Comparacao de histogramas normalizados entre classes", fontsize=18, fontweight="bold")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=dpi, bbox_inches="tight")
    plt.close(figure)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("datasets/samples_sbsr_bsb"))
    parser.add_argument("--output", type=Path, default=Path("analysis/results/samples_sbsr_bsb_distributions.png"))
    parser.add_argument("--bins", type=int, default=35)
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args()
    create_panel(args.input_dir, args.output, args.bins, args.dpi)
    print(f"Painel salvo em: {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())