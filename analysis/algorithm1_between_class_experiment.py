"""
Experimento entre pares de classes com o Algoritmo 1 do artigo.

Diferenca em relacao ao experimento intra-classe:
- cada classe inteira e um grupo do teste;
- todos os poligonos da classe sao reunidos no mesmo grupo;
- cada comparacao possui exatamente dois grupos: classe A e classe B.

Para cada par de classes, o Algoritmo 1 calcula a estatistica T usando
as observacoes vetoriais [IHH, IHV] e estima o p-valor por bootstrap.
"""

from __future__ import annotations

import json
import logging
import sys
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from tqdm import tqdm

try:
    from analysis.u_statistics_algorithms import (
        compute_statistic_T_between_groups,
        homogeneity_test,
    )
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from analysis.u_statistics_algorithms import (
        compute_statistic_T_between_groups,
        homogeneity_test,
    )


CLASS_NAME_MAP = {
    "Fo": "Floresta",
    "Gl": "Graminea",
    "Sv": "Savana",
}

CONFIG_PATH = Path(__file__).with_suffix(".config.json")
logger = logging.getLogger(__name__)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(config_path: Path = CONFIG_PATH) -> dict:
    with config_path.open("r", encoding="utf-8") as file:
        config = json.load(file)

    config["input_dir"] = Path(config["input_dir"])
    config["output_csv"] = Path(config["output_csv"])
    return config


def parse_sample_name(file_path: Path) -> Tuple[str, str]:
    if "_" not in file_path.stem:
        raise ValueError(f"Nome de arquivo invalido: {file_path.name}")
    return tuple(file_path.stem.split("_", 1))


def load_numeric_columns(csv_path: Path) -> Tuple[List[str], np.ndarray]:
    data = np.genfromtxt(
        csv_path,
        delimiter=",",
        names=True,
        dtype=float,
        encoding="utf-8",
    )

    if data.size == 0:
        raise ValueError(f"Arquivo sem dados: {csv_path}")

    columns = [column for column in data.dtype.names or [] if column.lower() != "id"]
    if not columns:
        raise ValueError(f"Nenhuma coluna numerica util em: {csv_path}")

    if data.ndim == 0:
        values = np.array([[float(data[column]) for column in columns]], dtype=float)
    else:
        values = np.column_stack([data[column].astype(float) for column in columns])

    return columns, values


def group_files_by_class(input_dir: Path) -> Dict[str, List[Path]]:
    grouped: Dict[str, List[Path]] = {}
    for csv_file in sorted(input_dir.glob("*.csv")):
        class_code, _ = parse_sample_name(csv_file)
        grouped.setdefault(class_code, []).append(csv_file)
    return grouped


def build_class_group(
    class_files: List[Path],
    group_id: int,
    show_progress: bool = False,
) -> Tuple[List[np.ndarray], List[int], List[str], List[int]]:
    observations: List[np.ndarray] = []
    group_indices: List[int] = []
    sample_names: List[str] = []
    sample_sizes: List[int] = []
    reference_columns: List[str] | None = None

    iterator = sorted(class_files)
    if show_progress:
        iterator = tqdm(iterator, desc="Lendo amostras", leave=False)

    for sample_file in iterator:
        columns, values = load_numeric_columns(sample_file)
        if reference_columns is None:
            reference_columns = columns
        elif columns != reference_columns:
            raise ValueError(
                f"Colunas incompativeis: {sample_file.name} possui {columns}, "
                f"esperado {reference_columns}"
            )

        observations.extend(row.astype(float) for row in values)
        group_indices.extend([group_id] * len(values))
        sample_names.append(sample_file.stem)
        sample_sizes.append(len(values))

    return observations, group_indices, sample_names, sample_sizes


def build_pair_data(
    class_a: str,
    files_a: List[Path],
    class_b: str,
    files_b: List[Path],
) -> Tuple[List[np.ndarray], List[int], List[str], List[int]]:
    observations_a, groups_a, names_a, sizes_a = build_class_group(files_a, 0, True)
    observations_b, groups_b, names_b, sizes_b = build_class_group(files_b, 1, True)

    return (
        observations_a + observations_b,
        groups_a + groups_b,
        names_a + names_b,
        sizes_a + sizes_b,
    )


def run_between_class_algorithm_1(
    input_dir: Path,
    gamma: float,
    alpha: float,
    B: int,
    seed: int,
    verbose_bootstrap: bool,
) -> List[dict]:
    grouped_files = group_files_by_class(input_dir)
    results: List[dict] = []
    np.random.seed(seed)

    class_items = sorted(grouped_files.items())
    for (class_a, files_a), (class_b, files_b) in combinations(class_items, 2):
        logger.info(
            "Comparando %s (%s) x %s (%s)",
            class_a,
            CLASS_NAME_MAP.get(class_a, class_a),
            class_b,
            CLASS_NAME_MAP.get(class_b, class_b),
        )

        X, group_indices, sample_names, sample_sizes = build_pair_data(
            class_a, files_a, class_b, files_b
        )

        # Neste experimento, os dois grupos sao as classes inteiras.
        observed_T = compute_statistic_T_between_groups(X, group_indices, gamma)
        p_value, reject_h0 = homogeneity_test(
            X=X,
            group_indices=group_indices,
            gamma=gamma,
            alpha=alpha,
            B=B,
            verbose=verbose_bootstrap,
        )

        results.append(
            {
                "class_a_code": class_a,
                "class_a_name": CLASS_NAME_MAP.get(class_a, class_a),
                "class_b_code": class_b,
                "class_b_name": CLASS_NAME_MAP.get(class_b, class_b),
                "class_a_samples": "|".join(sample_names[:len(files_a)]),
                "class_b_samples": "|".join(sample_names[len(files_a):]),
                "class_a_group_size": int(sum(sample_sizes[:len(files_a)])),
                "class_b_group_size": int(sum(sample_sizes[len(files_a):])),
                "n_observations_total": len(X),
                "gamma": float(gamma),
                "T_observed": float(observed_T),
                "p_value": float(p_value),
                "alpha": float(alpha),
                "B": int(B),
                "seed": int(seed),
                "reject_h0": bool(reject_h0),
            }
        )

        logger.info(
            "%s x %s concluido | T=%.6f | p=%.6f | reject_h0=%s",
            class_a,
            class_b,
            observed_T,
            p_value,
            reject_h0,
        )

    return results


def write_results_csv(results: List[dict], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "class_a_code", "class_a_name", "class_b_code", "class_b_name",
        "class_a_samples", "class_b_samples", "class_a_group_size",
        "class_b_group_size", "n_observations_total", "gamma", "T_observed",
        "p_value", "alpha", "B", "seed", "reject_h0",
    ]

    with output_csv.open("w", encoding="utf-8", newline="") as file:
        file.write(",".join(headers) + "\n")
        for result in results:
            file.write(",".join(str(result[header]) for header in headers) + "\n")


def main() -> None:
    config = load_config()
    setup_logging(bool(config.get("verbose", False)))
    logger.info("Configuracao carregada de %s", CONFIG_PATH)

    results = run_between_class_algorithm_1(
        input_dir=config["input_dir"],
        gamma=float(config["gamma"]),
        alpha=float(config["alpha"]),
        B=int(config["B"]),
        seed=int(config["seed"]),
        verbose_bootstrap=bool(config.get("verbose_bootstrap", False)),
    )
    write_results_csv(results, config["output_csv"])
    print(f"Resultados salvos em: {config['output_csv']}")


if __name__ == "__main__":
    main()
