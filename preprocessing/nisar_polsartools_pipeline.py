"""Pipeline NISAR RSLC (.h5) com polsartools.

Fontes de referencia para estrutura e fluxo:
- NISAR L1 RSLC Product Specification (NASA SDS)
- Tutorial oficial: NISAR_RSLC_Full_pol.ipynb (polsartools-tutorials)
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import rasterio


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = ROOT_DIR / "downloads"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "datasets" / "processed"


@dataclass
class SceneTask:
    sensor: str
    input_path: Path
    output_dir: Path


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else ROOT_DIR / path


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Arquivo de configuracao nao encontrado: {config_path}")
    with config_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("A configuracao precisa ser um objeto JSON.")
    return data


def _match_sensor(file_path: Path, sensor_patterns: dict[str, list[str]]) -> str | None:
    haystack = file_path.as_posix().lower()
    for sensor, patterns in sensor_patterns.items():
        for pattern in patterns:
            if pattern.lower() in haystack:
                return sensor
    return None


def discover_inputs(config: dict[str, Any]) -> list[SceneTask]:
    input_dir = _resolve_path(config.get("input_dir", str(DEFAULT_INPUT_DIR)))
    output_dir = _resolve_path(config.get("output_dir", str(DEFAULT_OUTPUT_DIR)))

    search_extensions = config.get("search_extensions", [".h5", ".hdf5"])
    if not isinstance(search_extensions, list) or not search_extensions:
        raise ValueError("search_extensions precisa ser uma lista nao vazia.")

    sensor_patterns = config.get("sensor_patterns", {"nisar": ["nisar"]})
    if not isinstance(sensor_patterns, dict) or not sensor_patterns:
        raise ValueError("sensor_patterns precisa ser um objeto com listas por sensor.")

    recursive = bool(config.get("recursive", True))
    iterator = input_dir.rglob("*") if recursive else input_dir.glob("*")

    tasks: list[SceneTask] = []
    for file_path in iterator:
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in {ext.lower() for ext in search_extensions}:
            continue

        sensor = _match_sensor(file_path, sensor_patterns)
        if sensor is None:
            continue

        scene_output_dir = output_dir / sensor / file_path.stem
        tasks.append(SceneTask(sensor=sensor, input_path=file_path, output_dir=scene_output_dir))

    return sorted(tasks, key=lambda task: task.input_path.as_posix())


def _import_polsartools() -> Any:
    try:
        import polsartools  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "Biblioteca polsartools nao encontrada. Instale com: pip install polsartools"
        ) from exc
    return polsartools


def _resolve_operation_callable(operation_name: str) -> Any:
    polsartools = _import_polsartools()

    # Tenta o nome diretamente na raiz do pacote.
    if hasattr(polsartools, operation_name):
        return getattr(polsartools, operation_name)

    # Tenta modulos comuns.
    candidate_modules = [
        "preprocessing",
        "processing",
        "filters",
        "decomposition",
        "classification",
        "io",
    ]

    for module_name in candidate_modules:
        module = getattr(polsartools, module_name, None)
        if module is not None and hasattr(module, operation_name):
            return getattr(module, operation_name)

    raise AttributeError(
        f"Operacao '{operation_name}' nao encontrada no polsartools. "
        "Revise o nome da operacao na configuracao."
    )


def _multilook_mean(data: np.ndarray, row_looks: int, col_looks: int) -> np.ndarray:
    if row_looks <= 0 or col_looks <= 0:
        raise ValueError("row_looks e col_looks precisam ser inteiros positivos.")
    rows, cols = data.shape
    out_rows = rows // row_looks
    out_cols = cols // col_looks
    if out_rows == 0 or out_cols == 0:
        raise ValueError("Fatores de multilook maiores que o tamanho da imagem.")
    trimmed = data[: out_rows * row_looks, : out_cols * col_looks]
    reshaped = trimmed.reshape(out_rows, row_looks, out_cols, col_looks)
    return reshaped.mean(axis=(1, 3))


def _find_pol_file(input_path: Path, patterns: list[str]) -> Path | None:
    if input_path.is_file():
        name = input_path.name.lower()
        if any(token.lower() in name for token in patterns):
            return input_path
        return None

    candidates: list[Path] = []
    for file_path in input_path.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in {".tif", ".tiff", ".img", ".vrt"}:
            continue
        name = file_path.name.lower()
        if any(token.lower() in name for token in patterns):
            candidates.append(file_path)

    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.as_posix())[0]


def _run_nisar_multilook_4pol_tif(step_kwargs: dict[str, Any]) -> None:
    input_path = Path(str(step_kwargs.get("input_path")))
    output_path = Path(str(step_kwargs.get("output_path")))
    row_looks = int(step_kwargs.get("row_looks", 4))
    col_looks = int(step_kwargs.get("col_looks", 4))
    complex_to_power = bool(step_kwargs.get("complex_to_power", True))

    pol_patterns = step_kwargs.get(
        "pol_patterns",
        {
            "HH": ["_hh", "hh."],
            "HV": ["_hv", "hv."],
            "VV": ["_vv", "vv."],
        },
    )
    if not isinstance(pol_patterns, dict):
        raise ValueError("pol_patterns precisa ser um objeto com HH/HV/VH/VV.")

    required_pols = ["HH", "HV", "VV"]
    pol_files: dict[str, Path] = {}
    for pol in required_pols:
        patterns = pol_patterns.get(pol)
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(f"pol_patterns['{pol}'] precisa ser uma lista nao vazia.")
        found = _find_pol_file(input_path, [str(item) for item in patterns])
        if found is None:
            raise FileNotFoundError(f"Arquivo da polarizacao {pol} nao encontrado em: {input_path}")
        pol_files[pol] = found

    ml_bands: list[np.ndarray] = []
    profile: dict[str, Any] | None = None
    transform = None

    for pol in required_pols:
        file_path = pol_files[pol]
        with rasterio.open(file_path) as src:
            band = src.read(1)
            if np.iscomplexobj(band) and complex_to_power:
                band = np.abs(band) ** 2
            band = band.astype(np.float32, copy=False)

            ml_band = _multilook_mean(band, row_looks=row_looks, col_looks=col_looks)
            ml_bands.append(ml_band)

            if profile is None:
                profile = src.profile.copy()
                transform = src.transform

    if profile is None or transform is None:
        raise RuntimeError("Falha ao montar perfil de saida para GeoTIFF.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_transform = transform * rasterio.Affine.scale(col_looks, row_looks)

    profile.update(
        {
            "driver": "GTiff",
            "dtype": "float32",
            "count": len(required_pols),
            "height": ml_bands[0].shape[0],
            "width": ml_bands[0].shape[1],
            "transform": out_transform,
            "compress": "deflate",
            "predictor": 2,
            "tiled": True,
        }
    )

    with rasterio.open(output_path, "w", **profile) as dst:
        for idx, pol in enumerate(required_pols, start=1):
            dst.write(ml_bands[idx - 1], idx)
            dst.set_band_description(idx, pol)

    print(f"    GeoTIFF multilook salvo: {output_path}")


def _ensure_path_params(kwargs: dict[str, Any], scene_input: Path, scene_output_dir: Path) -> dict[str, Any]:
    resolved = dict(kwargs)

    # Convencao da pipeline: placeholders para facilitar reuso de etapas.
    for key, value in list(resolved.items()):
        if isinstance(value, str):
            replaced = (
                value.replace("{input}", str(scene_input))
                .replace("{output_dir}", str(scene_output_dir))
                .replace("{stem}", scene_input.stem)
            )
            resolved[key] = replaced

    # Se a etapa nao declarar explicitamente, injeta argumentos padrao mais comuns.
    if "input_path" not in resolved and "in_path" not in resolved and "input" not in resolved:
        resolved["input_path"] = str(scene_input)

    if "output_dir" not in resolved and "out_dir" not in resolved:
        resolved["output_dir"] = str(scene_output_dir)

    return resolved


def _run_nisar_extract(
    task: SceneTask,
    config: dict[str, Any],
    dry_run: bool,
) -> Path | None:
    extract_kwargs = config.get("nisar_extract_kwargs", {})
    if not isinstance(extract_kwargs, dict):
        raise ValueError("nisar_extract_kwargs precisa ser um objeto JSON.")

    extract_output_dir = task.output_dir / "00_extracted"
    resolved_kwargs = _ensure_path_params(extract_kwargs, task.input_path, extract_output_dir)

    # Normaliza aliases para a assinatura nativa do polsartools.
    if "inFile" not in resolved_kwargs and "input_path" in resolved_kwargs:
        resolved_kwargs["inFile"] = resolved_kwargs["input_path"]
    if "out_dir" not in resolved_kwargs and "output_dir" in resolved_kwargs:
        resolved_kwargs["out_dir"] = resolved_kwargs["output_dir"]

    # Remove aliases para evitar erro: unexpected keyword argument 'input_path'/'output_dir'.
    resolved_kwargs.pop("input_path", None)
    resolved_kwargs.pop("output_dir", None)

    # Padroes para gerar GeoTIFF multilook na importacao nativa do NISAR.
    resolved_kwargs.setdefault("mat", "C2")
    resolved_kwargs.setdefault("azlks", 2)
    resolved_kwargs.setdefault("rglks", 2)
    resolved_kwargs.setdefault("fmt", "tif")

    mat_name = str(resolved_kwargs.get("mat", "")).upper()
    available_pols = _read_nisar_polarizations(task.input_path)
    if _should_skip_dual_pol_for_full_pol_mat(available_pols, mat_name):
        pols_label = ", ".join(available_pols) if available_pols else "desconhecidas"
        print("  - Etapa 0: import_nisar_gslc")
        print(f"    PULADO: cena dual-pol ({pols_label}) nao compativel com mat={mat_name}.")
        return None

    print("  - Etapa 0: import_nisar_gslc")
    if dry_run:
        print(f"    kwargs: {resolved_kwargs}")
        return extract_output_dir

    pst = _import_polsartools()
    pst.import_nisar_gslc(**resolved_kwargs)
    return extract_output_dir


def _read_nisar_polarizations(h5_path: Path) -> list[str]:
    candidate_paths = [
        "/science/LSAR/RSLC/swaths/frequencyA/listOfPolarizations",
        "/science/SSAR/RSLC/swaths/frequencyA/listOfPolarizations",
        "/science/LSAR/SLC/swaths/frequencyA/listOfPolarizations",
        "/science/SSAR/SLC/swaths/frequencyA/listOfPolarizations",
    ]

    with h5py.File(h5_path, "r") as h5f:
        for path in candidate_paths:
            node = h5f.get(path)
            if node is None:
                continue
            values = node[()]
            pols: list[str] = []
            for item in values.tolist():
                if isinstance(item, bytes):
                    pols.append(item.decode("utf-8").upper())
                else:
                    pols.append(str(item).upper())
            return pols

    return []


def _should_skip_dual_pol_for_full_pol_mat(available_pols: list[str], mat: str) -> bool:
    if not mat:
        return False

    full_pol_mats = {"S2", "C4", "C3", "T4", "T3"}
    if mat not in full_pol_mats:
        return False

    if not available_pols:
        return False

    available_set = set(available_pols)
    has_hh_hv_vv = {"HH", "HV", "VV"}.issubset(available_set)
    has_hh_hv_vh_vv = {"HH", "HV", "VH", "VV"}.issubset(available_set)
    return not (has_hh_hv_vv or has_hh_hv_vh_vv)


def run_scene_pipeline(
    task: SceneTask,
    steps: list[dict[str, Any]],
    config: dict[str, Any],
    dry_run: bool,
) -> None:
    task.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[{task.sensor}] Cena: {task.input_path}")

    current_input = task.input_path
    if task.input_path.suffix.lower() in {".h5", ".hdf5"}:
        extracted_dir = _run_nisar_extract(task=task, config=config, dry_run=dry_run)
        if extracted_dir is None:
            return
        matrix_subdir = str(config.get("matrix_input_subdir", "C3")).strip("/\\")
        current_input = extracted_dir / matrix_subdir if matrix_subdir else extracted_dir

    for index, step in enumerate(steps, start=1):
        operation_name = step.get("operation")
        if not operation_name or not isinstance(operation_name, str):
            raise ValueError(f"Etapa {index} sem campo 'operation' valido.")

        kwargs = step.get("kwargs", {})
        if not isinstance(kwargs, dict):
            raise ValueError(f"Etapa {index} ('{operation_name}') com kwargs invalido.")

        call_kwargs = _ensure_path_params(kwargs, current_input, task.output_dir)

        print(f"  - Etapa {index}: {operation_name}")
        if dry_run:
            print(f"    kwargs: {call_kwargs}")
            continue

        if operation_name in {"nisar_multilook_4pol_tif", "nisar_multilook_3pol_tif"}:
            _run_nisar_multilook_4pol_tif(call_kwargs)
            continue

        op_callable = _resolve_operation_callable(operation_name)
        op_callable(**call_kwargs)


def run_pipeline(config: dict[str, Any], dry_run: bool) -> int:
    tasks = discover_inputs(config)
    if not tasks:
        print("Nenhuma cena encontrada para processar.")
        return 0

    sensor_filter = config.get("sensor_filter")
    if sensor_filter:
        allowed = {str(item).lower() for item in sensor_filter}
        tasks = [task for task in tasks if task.sensor.lower() in allowed]

    if not tasks:
        print("Nenhuma cena restante apos filtro de sensor.")
        return 0

    steps = config.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("A configuracao precisa conter 'steps' como lista.")

    print(f"Cenas encontradas: {len(tasks)}")
    for task in tasks:
        run_scene_pipeline(task=task, steps=steps, config=config, dry_run=dry_run)

    print("Pipeline finalizada.")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pipeline de processamento NISAR com polsartools (import_nisar_gslc).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT_DIR / "analysis" / "nisar_polsartools_pipeline_config.json",
        help="Caminho para o arquivo JSON de configuracao da pipeline.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra as etapas e parametros sem executar o polsartools.",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    config_path = _resolve_path(args.config)
    config = load_config(config_path)
    return run_pipeline(config=config, dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
