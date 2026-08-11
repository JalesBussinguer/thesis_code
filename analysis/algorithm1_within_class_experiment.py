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
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from tqdm import tqdm

try:
    from analysis.u_statistics_algorithms import homogeneity_test
except ModuleNotFoundError:
    # Permite execucao direta: python analysis/algorithm1_within_class_experiment.py
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from analysis.u_statistics_algorithms import homogeneity_test


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
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    config["input_dir"] = Path(config["input_dir"])
    config["output_csv"] = Path(config["output_csv"])
    return config


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


def run_within_class_algorithm_1(
    input_dir: Path,
    gamma: float,
    alpha: float,
    B: int,
    seed: int,
    verbose_bootstrap: bool,
) -> List[dict]:
    results: List[dict] = []
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
    class_iterator = tqdm(class_items, desc="Classes", leave=True)

    for class_code, files in class_iterator:
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

        X, group_indices, sample_names = build_X_and_groups(files, show_progress=True)

        p_value, reject_h0 = homogeneity_test(
            X=X,
            group_indices=group_indices,
            gamma=gamma,
            alpha=alpha,
            B=B,
            verbose=verbose_bootstrap,
        )

        group_sizes = []
        for gid in range(len(sample_names)):
            group_sizes.append(int(np.sum(np.array(group_indices) == gid)))

        results.append(
            {
                "class_code": class_code,
                "class_name": class_name,
                "n_samples": len(sample_names),
                "sample_names": "|".join(sample_names),
                "group_sizes": "|".join(str(x) for x in group_sizes),
                "n_observations_total": len(X),
                "gamma": float(gamma),
                "alpha": float(alpha),
                "B": int(B),
                "seed": int(seed),
                "p_value": float(p_value),
                "reject_h0": bool(reject_h0),
            }
        )

        logger.info(
            "Classe %s concluida | p=%.6f | reject_h0=%s",
            class_code,
            p_value,
            reject_h0,
        )

    return results


def write_results_csv(results: List[dict], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "class_code",
        "class_name",
        "n_samples",
        "sample_names",
        "group_sizes",
        "n_observations_total",
        "gamma",
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


def print_summary(results: List[dict]) -> None:
    if not results:
        print("Nenhum resultado gerado.")
        return

    print(f"Total de classes avaliadas: {len(results)}")
    for row in sorted(results, key=lambda x: x["class_code"]):
        decision = "Rejeita H0" if row["reject_h0"] else "Nao rejeita H0"
        print(
            f"Classe {row['class_code']} ({row['class_name']}): "
            f"p={row['p_value']:.6f}, {decision}"
        )


def main() -> None:
    config = load_config()
    setup_logging(bool(config.get("verbose", False)))

    logger.info("Configuracao carregada de %s", CONFIG_PATH)
    logger.info("Diretorio de entrada: %s", config["input_dir"])
    logger.info("Arquivo de saida: %s", config["output_csv"])

    results = run_within_class_algorithm_1(
        input_dir=config["input_dir"],
        gamma=float(config["gamma"]),
        alpha=float(config["alpha"]),
        B=int(config["B"]),
        seed=int(config["seed"]),
        verbose_bootstrap=bool(config.get("verbose_bootstrap", False)),
    )
    write_results_csv(results, config["output_csv"])
    print_summary(results)

    print(f"Resultados salvos em: {config['output_csv']}")


if __name__ == "__main__":
    main()
