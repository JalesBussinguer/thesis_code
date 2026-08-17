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
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from tqdm import tqdm

try:
    from analysis.u_statistics_algorithms import (
        compute_statistic_T_between_groups,
    )
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from analysis.u_statistics_algorithms import (
        compute_statistic_T_between_groups,
    )


CLASS_NAME_MAP = {
    "Fo": "Floresta",
    "Gl": "Graminea",
    "Sv": "Savana",
}

CONFIG_PATH = Path(__file__).with_suffix(".config.json")
logger = logging.getLogger(__name__)


def _bootstrap_worker(args: tuple[np.ndarray, np.ndarray, np.ndarray, float, int]) -> float:
    """Gera uma replicata bootstrap sob H0 usando o pool combinado dos dois grupos.

    A ideia e aproximar a distribuicao nula de T reamostrando o conjunto de
    observacoes combinado e depois reaplicando os tamanhos originais dos grupos
    (n1 e n2). Isso preserva a hipotese H0 de que os grupos provem da mesma
    distribuicao, ao mesmo tempo em que mantem a estrutura amostral do teste.
    """
    X_array, group_indices_by_id, group_ids, gamma, seed = args
    rng = np.random.default_rng(seed)
    pooled_indices = np.concatenate(list(group_indices_by_id))
    group_sizes = [len(indices) for indices in group_indices_by_id]
    bootstrap_indices = rng.choice(pooled_indices, size=len(pooled_indices), replace=True)
    bootstrap_labels = np.concatenate([
        np.full(size, group_id)
        for size, group_id in zip(group_sizes, group_ids)
    ])
    return float(compute_statistic_T_between_groups(
        X_array[bootstrap_indices], bootstrap_labels.tolist(), gamma
    ))


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


def pooled_bootstrap_p_value(
    X: List[np.ndarray],
    group_indices: List[int],
    gamma: float,
    observed_T: float,
    B: int,
    seed: int,
    verbose: bool = False,
) -> Tuple[float, List[float]]:
    """Calcula o p-valor bootstrap sob H0 usando o pool combinado dos grupos.

    Os dados sao reunidos em um unico pool, reamostrados com reposicao, e depois
    redistribuidos entre os dois grupos mantendo os tamanhos originais. Isso
    gera a referencia nula da estatistica T para o teste de igualdade entre
    grupos.
    """
    if B < 1:
        raise ValueError("B deve ser maior que zero")

    X_array = np.asarray(X, dtype=float)
    labels = np.asarray(group_indices, dtype=int)
    group_indices_by_id = {
        group_id: np.flatnonzero(labels == group_id)
        for group_id in np.unique(labels)
    }
    if len(group_indices_by_id) != 2:
        raise ValueError("O bootstrap exige dois grupos nao vazios")

    group_ids = np.array(list(group_indices_by_id), dtype=int)
    group_indices_array = np.array(list(group_indices_by_id.values()), dtype=object)
    seeds = np.random.SeedSequence(seed).spawn(B)
    tasks = [
        (X_array, group_indices_array, group_ids, gamma, int(child.generate_state(1)[0]))
        for child in seeds
    ]
    workers = max(1, (os.cpu_count() or 1) - 2)
    if workers == 1 or B < 2:
        bootstrap_values = [_bootstrap_worker(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            iterator = executor.map(_bootstrap_worker, tasks)
            bootstrap_values = list(tqdm(iterator, total=B, desc="Bootstrap", leave=False)) if verbose else list(iterator)

    extreme_count = sum(value >= observed_T for value in bootstrap_values)
    return extreme_count / B, bootstrap_values


def run_between_class_algorithm_1(
    input_dir: Path,
    gamma: float,
    alpha: float,
    B: int,
    seed: int,
    verbose_bootstrap: bool,
    checkpoint_csv: Path | None = None,
    checkpoint_dir: Path | None = None,
) -> Tuple[List[dict], dict[str, List[float]]]:
    grouped_files = group_files_by_class(input_dir)
    results: List[dict] = []
    bootstrap_distributions: dict[str, List[float]] = {}
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

        observations_a, groups_a, names_a, sizes_a = build_class_group(files_a, 0, True)
        observations_b, groups_b, names_b, sizes_b = build_class_group(files_b, 1, True)

        # Primeiro calcula T com os dois grupos originais, ainda separados.
        observed_X = observations_a + observations_b
        observed_groups = groups_a + groups_b
        observed_T = compute_statistic_T_between_groups(
            observed_X,
            observed_groups,
            gamma,
        )

        # Somente depois une os datasets na bacia X usada pelo bootstrap.
        X = observed_X
        group_indices = observed_groups
        sample_names = names_a + names_b
        sample_sizes = sizes_a + sizes_b
        p_value, bootstrap_values = pooled_bootstrap_p_value(
            X=X,
            group_indices=group_indices,
            gamma=gamma,
            observed_T=observed_T,
            B=B,
            seed=seed,
            verbose=verbose_bootstrap,
        )
        pair_name = f"{class_a}_x_{class_b}"
        bootstrap_distributions[pair_name] = bootstrap_values
        reject_h0 = p_value < alpha

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
        if checkpoint_csv is not None:
            append_result_csv(results[-1], checkpoint_csv)
        if checkpoint_dir is not None:
            write_bootstrap_csv({pair_name: bootstrap_values}, checkpoint_dir)

        logger.info(
            "%s x %s concluido | T=%.6f | p=%.6f | reject_h0=%s",
            class_a,
            class_b,
            observed_T,
            p_value,
            reject_h0,
        )

    return results, bootstrap_distributions


def write_bootstrap_csv(distributions: dict[str, List[float]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for pair_name, values in distributions.items():
        output_csv = output_dir / f"{pair_name}.csv"
        with output_csv.open("w", encoding="utf-8", newline="") as file:
            file.write("bootstrap_id,T_bootstrap\n")
            for bootstrap_id, value in enumerate(values, start=1):
                file.write(f"{bootstrap_id},{value:.17g}\n")
        logger.info("Distribuicao bootstrap salva em %s", output_csv)


def append_result_csv(result: dict, output_csv: Path) -> None:
    headers = [
        "class_a_code", "class_a_name", "class_b_code", "class_b_name",
        "class_a_samples", "class_b_samples", "class_a_group_size",
        "class_b_group_size", "n_observations_total", "gamma", "T_observed",
        "p_value", "alpha", "B", "seed", "reject_h0",
    ]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not output_csv.exists() or output_csv.stat().st_size == 0
    with output_csv.open("a", encoding="utf-8", newline="") as file:
        if needs_header:
            file.write(",".join(headers) + "\n")
        file.write(",".join(str(result[header]) for header in headers) + "\n")


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


def build_output_csv_path(output_csv: Path, gamma: float, alpha: float, B: int, seed: int) -> Path:
    """Acrescenta gamma, alpha, B e seed ao nome do arquivo de saida."""
    suffix = f"_gamma{gamma}_alpha{alpha}_B{B}_seed{seed}"
    return output_csv.with_name(f"{output_csv.stem}{suffix}{output_csv.suffix}")


def main() -> None:
    config = load_config()
    setup_logging(bool(config.get("verbose", False)))
    logger.info("Configuracao carregada de %s", CONFIG_PATH)

    gamma = float(config["gamma"])
    alpha = float(config["alpha"])
    B = int(config["B"])
    seed = int(config["seed"])
    output_csv = build_output_csv_path(config["output_csv"], gamma, alpha, B, seed)

    results, bootstrap_distributions = run_between_class_algorithm_1(
        input_dir=config["input_dir"],
        gamma=gamma,
        alpha=alpha,
        B=B,
        seed=seed,
        verbose_bootstrap=bool(config.get("verbose_bootstrap", False)),
        checkpoint_csv=output_csv,
        checkpoint_dir=output_csv.parent / f"bootstrap_distributions_between{output_csv.stem[len(config['output_csv'].stem):]}",
    )
    write_results_csv(results, output_csv)
    write_bootstrap_csv(
        bootstrap_distributions,
        output_csv.parent / f"bootstrap_distributions_between{output_csv.stem[len(config['output_csv'].stem):]}",
    )
    print(f"Resultados salvos em: {output_csv}")


if __name__ == "__main__":
    main()
