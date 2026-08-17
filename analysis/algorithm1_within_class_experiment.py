"""
Experimento de homogeneidade intra-classe com Algoritmo 1 do artigo.

Regra dos arquivos de entrada:
- Nome: <classe>_<cor>.csv (ex.: Fo_B.csv)
- Cada arquivo representa uma amostra (poligono) da mesma classe.

Para cada classe (Fo, Gl, Sv), o script monta:
- X: todas as observacoes das amostras da classe
- group_indices: indice do poligono/amostra de cada observacao

Em seguida executa o Algoritmo 1 (homogeneity_test), baseado em
U-statistics com p-valor por bootstrap.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from tqdm import tqdm

try:
    from analysis.u_statistics_algorithms import compute_statistic_T_within_group
except ModuleNotFoundError:
    # Permite execucao direta: python analysis/algorithm1_within_class_experiment.py
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from analysis.u_statistics_algorithms import compute_statistic_T_within_group


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
    return float(compute_statistic_T_within_group(
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
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    project_root = config_path.parent.parent
    for key in ("input_dir", "output_csv"):
        path = Path(config[key])
        config[key] = path if path.is_absolute() else project_root / path
    return config


def resolve_gamma_values(config: dict) -> List[float]:
    """Le "gamma" (numero unico) ou "gamma_sweep" ({start, stop, step}) do config."""
    sweep = config.get("gamma_sweep")
    if sweep is None:
        return [float(config["gamma"])]

    start, stop, step = float(sweep["start"]), float(sweep["stop"]), float(sweep["step"])
    n_steps = round((stop - start) / step) + 1
    return [round(start + i * step, 10) for i in range(n_steps)]


def parse_sample_name(file_path: Path) -> Tuple[str, str]:
    """
    Extrai (classe, cor) de arquivos no formato <classe>_<cor>.csv.
    """
    stem = file_path.stem
    if "_" not in stem:
        raise ValueError(f"Nome de arquivo invalido: {file_path.name}")

    class_code, color_code = stem.split("_", 1)
    return class_code, color_code


def load_numeric_columns(csv_path: Path) -> Tuple[List[str], np.ndarray]:
    """
    Carrega colunas numericas, ignorando coluna 'id' (case-insensitive).

    Returns:
        (column_names, values)
        values shape = (n_observacoes, n_colunas)
    """
    data = np.genfromtxt(
        csv_path,
        delimiter=",",
        names=True,
        dtype=float,
        encoding="utf-8",
    )

    if data.size == 0:
        raise ValueError(f"Arquivo sem dados: {csv_path}")

    all_columns = list(data.dtype.names or [])
    numeric_columns = [c for c in all_columns if c.lower() != "id"]

    if not numeric_columns:
        raise ValueError(f"Nenhuma coluna numerica util em: {csv_path}")

    if data.ndim == 0:
        values = np.array([[float(data[col]) for col in numeric_columns]], dtype=float)
    else:
        values = np.column_stack([data[col].astype(float) for col in numeric_columns])

    return numeric_columns, values


def group_files_by_class(input_dir: Path) -> Dict[str, List[Path]]:
    grouped: Dict[str, List[Path]] = {}

    for csv_file in sorted(input_dir.glob("*.csv")):
        class_code, _ = parse_sample_name(csv_file)
        grouped.setdefault(class_code, []).append(csv_file)

    logger.info("Arquivos CSV agrupados em %d classe(s)", len(grouped))
    return grouped


def build_X_and_groups(class_files: List[Path], show_progress: bool = False) -> Tuple[List[np.ndarray], List[int], List[str]]:
    """
    Concatena observacoes de varias amostras em X e cria group_indices.

    Cada arquivo CSV vira um grupo distinto no teste.
    """
    X: List[np.ndarray] = []
    group_indices: List[int] = []
    sample_names: List[str] = []

    ref_columns: List[str] | None = None

    iterator = list(enumerate(sorted(class_files)))
    if show_progress:
        iterator = tqdm(iterator, desc="Lendo amostras", leave=False)

    for group_id, sample_file in iterator:
        cols, values = load_numeric_columns(sample_file)

        if ref_columns is None:
            ref_columns = cols
        elif cols != ref_columns:
            raise ValueError(
                "Colunas incompativeis entre amostras da mesma classe: "
                f"{sample_file.name} possui {cols}, esperado {ref_columns}"
            )

        for row in values:
            X.append(row.astype(float))
            group_indices.append(group_id)

        sample_names.append(sample_file.stem)

    logger.debug(
        "Montagem de X concluida: %d observações, %d grupos",
        len(X),
        len(sample_names),
    )

    return X, group_indices, sample_names


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
    redistribuidos entre todos os grupos mantendo os tamanhos originais. Isso
    gera a referencia nula da estatistica T para o teste de homogeneidade entre
    as amostras de uma mesma classe.
    """
    if B < 1:
        raise ValueError("B deve ser maior que zero")

    X_array = np.asarray(X, dtype=float)
    labels = np.asarray(group_indices, dtype=int)
    group_indices_by_id = {
        group_id: np.flatnonzero(labels == group_id)
        for group_id in np.unique(labels)
    }
    if len(group_indices_by_id) < 2:
        raise ValueError("O bootstrap exige ao menos dois grupos nao vazios")

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


def run_within_class_algorithm_1(
    input_dir: Path,
    gamma: float,
    alpha: float,
    B: int,
    seed: int,
    verbose_bootstrap: bool,
    checkpoint_csv: Path | None = None,
    checkpoint_dir: Path | None = None,
) -> Tuple[List[dict], dict[str, List[float]]]:
    results: List[dict] = []
    bootstrap_distributions: dict[str, List[float]] = {}
    grouped_files = group_files_by_class(input_dir)
    np.random.seed(seed)
    logger.info(
        "Iniciando experimento Algoritmo 1 | gamma=%s alpha=%s B=%s seed=%s",
        gamma,
        alpha,
        B,
        seed,
    )

    class_items = sorted(grouped_files.items())

    for class_code, files in class_items:
        if len(files) < 2:
            logger.warning(
                "Classe %s ignorada: possui apenas %d amostra(s)",
                class_code,
                len(files),
            )
            continue

        class_name = CLASS_NAME_MAP.get(class_code, class_code)
        logger.info(
            "Processando classe %s (%s) com %d amostras",
            class_code,
            class_name,
            len(files),
        )

        observed_X, observed_groups, sample_names = build_X_and_groups(files)
        observed_T = compute_statistic_T_within_group(
            observed_X, observed_groups, gamma
        )
        class_seed = seed + len(results)
        p_value, bootstrap_values = pooled_bootstrap_p_value(
            observed_X,
            observed_groups,
            gamma,
            observed_T,
            B,
            class_seed,
            verbose_bootstrap,
        )
        bootstrap_distributions[class_code] = bootstrap_values
        reject_h0 = p_value < alpha
        sample_sizes = np.bincount(observed_groups).tolist()
        results.append(
            {
                "class_code": class_code,
                "class_name": class_name,
                "samples": "|".join(sample_names),
                "sample_sizes": "|".join(str(size) for size in sample_sizes),
                "n_samples": len(sample_names),
                "n_observations_total": len(observed_X),
                "gamma": float(gamma),
                "alpha": float(alpha),
                "B": int(B),
                "seed": int(class_seed),
                "T_observed": float(observed_T),
                "p_value": float(p_value),
                "reject_h0": bool(reject_h0),
            }
        )
        if checkpoint_csv is not None:
            append_result_csv(results[-1], checkpoint_csv)
        if checkpoint_dir is not None:
            append_bootstrap_csv(class_code, bootstrap_values, checkpoint_dir)
        logger.info(
            "%s: %d amostras | T=%.6f | p=%.6f | reject_h0=%s",
            class_code, len(sample_names), observed_T, p_value, reject_h0
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


def append_bootstrap_csv(pair_name: str, values: List[float], output_dir: Path) -> None:
    write_bootstrap_csv({pair_name: values}, output_dir)


def write_results_csv(results: List[dict], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "class_code",
        "class_name",
        "samples",
        "sample_sizes",
        "n_samples",
        "n_observations_total",
        "gamma",
        "T_observed",
        "p_value",
        "alpha",
        "B",
        "seed",
        "reject_h0",
    ]

    with output_csv.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(headers) + "\n")
        for row in results:
            values = [str(row[h]) for h in headers]
            f.write(",".join(values) + "\n")

    logger.info("Resultados salvos em %s", output_csv)


def append_result_csv(result: dict, output_csv: Path) -> None:
    headers = [
        "class_code", "class_name", "samples", "sample_sizes", "n_samples",
        "n_observations_total", "gamma", "T_observed", "p_value",
        "alpha", "B", "seed", "reject_h0",
    ]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not output_csv.exists() or output_csv.stat().st_size == 0
    with output_csv.open("a", encoding="utf-8", newline="") as file:
        if needs_header:
            file.write(",".join(headers) + "\n")
        file.write(",".join(str(result[header]) for header in headers) + "\n")


def print_summary(results: List[dict]) -> None:
    if not results:
        print("Nenhum resultado gerado.")
        return

    print(f"Total de classes avaliadas: {len(results)}")
    for row in results:
        decision = "Rejeita H0" if row["reject_h0"] else "Nao rejeita H0"
        print(
            f"{row['class_code']} | {row['n_samples']} amostras ({row['samples']}): "
            f"T={row['T_observed']:.6f}, p={row['p_value']:.6f}, {decision}"
        )


def build_output_csv_path(output_csv: Path, gamma: float, alpha: float, B: int, seed: int) -> Path:
    """Acrescenta gamma, alpha, B e seed ao nome do arquivo de saida."""
    suffix = f"_gamma{gamma}_alpha{alpha}_B{B}_seed{seed}"
    return output_csv.with_name(f"{output_csv.stem}{suffix}{output_csv.suffix}")


def main() -> None:
    config = load_config()
    setup_logging(bool(config.get("verbose", False)))

    logger.info("Configuracao carregada de %s", CONFIG_PATH)
    logger.info("Diretorio de entrada: %s", config["input_dir"])

    alpha = float(config["alpha"])
    B = int(config["B"])
    seed = int(config["seed"])

    for gamma in resolve_gamma_values(config):
        output_csv = build_output_csv_path(config["output_csv"], gamma, alpha, B, seed)
        logger.info("Arquivo de saida: %s", output_csv)

        results, bootstrap_distributions = run_within_class_algorithm_1(
            input_dir=config["input_dir"],
            gamma=gamma,
            alpha=alpha,
            B=B,
            seed=seed,
            verbose_bootstrap=bool(config.get("verbose_bootstrap", False)),
            checkpoint_csv=output_csv,
            checkpoint_dir=output_csv.parent / f"bootstrap_distributions{output_csv.stem[len(config['output_csv'].stem):]}",
        )
        write_results_csv(results, output_csv)
        write_bootstrap_csv(
            bootstrap_distributions,
            output_csv.parent / f"bootstrap_distributions{output_csv.stem[len(config['output_csv'].stem):]}",
        )
        print_summary(results)

        print(f"Resultados salvos em: {output_csv}")


if __name__ == "__main__":
    main()
