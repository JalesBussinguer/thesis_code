"""Pipeline NISAR dedicada a decomposicao dual-pol H/alpha."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT_DIR / "preprocessing" / "nisar_h_alpha_pipeline_config.json"


@dataclass
class SceneTask:
    sensor: str
    input_path: Path
    output_dir: Path


def _validate_sensor_patterns(config: dict[str, Any]) -> dict[str, list[str]]:
    sensor_patterns = config.get("sensor_patterns", {"nisar": ["nisar", "gslc"]})
    if not isinstance(sensor_patterns, dict) or not sensor_patterns:
        raise ValueError("sensor_patterns precisa ser um objeto com listas por sensor.")
    for sensor, patterns in sensor_patterns.items():
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(f"sensor_patterns['{sensor}'] precisa ser uma lista nao vazia.")
    return sensor_patterns


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else ROOT_DIR / path


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"Arquivo de configuracao nao encontrado: {config_path}")
    with config_path.open("r", encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not isinstance(config, dict):
        raise ValueError("A configuracao precisa ser um objeto JSON.")
    return config


def _match_sensor(file_path: Path, sensor_patterns: dict[str, list[str]]) -> str | None:
    haystack = file_path.as_posix().lower()
    for sensor, patterns in sensor_patterns.items():
        if any(pattern.lower() in haystack for pattern in patterns):
            return sensor
    return None


def discover_inputs(config: dict[str, Any]) -> list[SceneTask]:
    input_dir = _resolve_path(config.get("input_dir", "downloads"))
    output_dir = _resolve_path(config.get("output_dir", "datasets/processed"))
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Diretorio de entrada nao encontrado: {input_dir}")

    input_mode = str(config.get("input_mode", "hdf5"))
    if input_mode not in {"hdf5", "existing_matrix"}:
        raise ValueError("input_mode precisa ser 'hdf5' ou 'existing_matrix'.")
    sensor_patterns = _validate_sensor_patterns(config)
    recursive = bool(config.get("recursive", True))

    if input_mode == "existing_matrix":
        matrix_subdir = str(config.get("matrix_input_subdir", "C2HX")).strip("/\\")
        if not matrix_subdir:
            raise ValueError("matrix_input_subdir nao pode ser vazio.")
        matrix_name = Path(matrix_subdir).name
        expected_parent = Path(matrix_subdir).parent.name
        candidates = (
            input_dir.rglob(matrix_name) if recursive else input_dir.glob(matrix_name)
        )
        tasks: list[SceneTask] = []
        for matrix_dir in candidates:
            if not matrix_dir.is_dir() or matrix_dir.name.lower() != matrix_name.lower():
                continue
            if expected_parent and matrix_dir.parent.name.lower() != expected_parent.lower():
                continue
            sensor = _match_sensor(matrix_dir, sensor_patterns)
            if sensor is not None:
                tasks.append(SceneTask(sensor, matrix_dir, matrix_dir))
        return sorted(tasks, key=lambda task: task.input_path.as_posix())

    extensions = config.get("search_extensions", [".h5", ".hdf5"])
    if not isinstance(extensions, list) or not extensions:
        raise ValueError("search_extensions precisa ser uma lista nao vazia.")
    normalized_extensions = {str(extension).lower() for extension in extensions}

    candidates = input_dir.rglob("*") if recursive else input_dir.glob("*")
    tasks: list[SceneTask] = []
    for file_path in candidates:
        if not file_path.is_file() or file_path.suffix.lower() not in normalized_extensions:
            continue
        sensor = _match_sensor(file_path, sensor_patterns)
        if sensor is not None:
            tasks.append(
                SceneTask(
                    sensor=sensor,
                    input_path=file_path,
                    output_dir=output_dir / sensor / file_path.stem,
                )
            )
    return sorted(tasks, key=lambda task: task.input_path.as_posix())


def _format_kwargs(
    kwargs: dict[str, Any], input_path: Path, output_dir: Path
) -> dict[str, Any]:
    formatted = dict(kwargs)
    for key, value in formatted.items():
        if isinstance(value, str):
            formatted[key] = (
                value.replace("{input}", str(input_path))
                .replace("{output_dir}", str(output_dir))
                .replace("{stem}", input_path.stem)
            )
    return formatted


def _import_polsartools() -> Any:
    try:
        import polsartools
    except ImportError as exc:
        raise ImportError(
            "Biblioteca polsartools nao encontrada. Instale com: pip install polsartools"
        ) from exc
    return polsartools


def _generate_h_alpha_plot(
    matrix_dir: Path, config: dict[str, Any], polsartools: Any
) -> Path:
    visualization = config.get("visualization", {})
    if not isinstance(visualization, dict):
        raise ValueError("visualization precisa ser um objeto JSON.")

    output_format = str(config.get("output_format", "tif"))
    if output_format not in {"tif", "bin"}:
        raise ValueError("output_format precisa ser 'tif' ou 'bin'.")
    h_path = matrix_dir / f"Hdp.{output_format}"
    alpha_path = matrix_dir / f"alphadp.{output_format}"
    for raster_path in (h_path, alpha_path):
        if not raster_path.is_file():
            raise FileNotFoundError(
                f"Raster de decomposicao H/alpha nao encontrado: {raster_path}"
            )

    filename = str(visualization.get("filename", "halpha_plot_dp.png"))
    if not filename:
        raise ValueError("visualization.filename nao pode ser vazio.")
    plot_path = Path(filename)
    if not plot_path.is_absolute():
        plot_path = matrix_dir / plot_path

    colormap = str(visualization.get("cmap", "viridis"))
    density_norm = str(visualization.get("density_norm", "log"))
    if density_norm not in {"", "log"}:
        raise ValueError("visualization.density_norm precisa ser '' ou 'log'.")
    gridsize = int(visualization.get("gridsize", 300))
    if gridsize <= 0:
        raise ValueError("visualization.gridsize precisa ser um inteiro positivo.")
    max_plot_dimension = int(visualization.get("max_plot_dimension", 1000))
    if max_plot_dimension <= 0:
        raise ValueError("visualization.max_plot_dimension precisa ser positivo.")

    if output_format == "tif":
        import numpy as np
        import rasterio
        from rasterio.enums import Resampling

        with rasterio.open(h_path) as h_source, rasterio.open(
            alpha_path
        ) as alpha_source:
            if (
                h_source.width != alpha_source.width
                or h_source.height != alpha_source.height
                or h_source.transform != alpha_source.transform
                or h_source.crs != alpha_source.crs
            ):
                raise ValueError("Os rasters H e alfa nao estao espacialmente alinhados.")
            scale = min(
                1.0,
                max_plot_dimension / max(h_source.width, h_source.height),
            )
            out_width = max(1, round(h_source.width * scale))
            out_height = max(1, round(h_source.height * scale))
            out_shape = (out_height, out_width)
            h = np.ma.filled(
                h_source.read(
                    1,
                    out_shape=out_shape,
                    masked=True,
                    resampling=Resampling.nearest,
                ),
                np.nan,
            ).astype(np.float32)
            alpha = np.ma.filled(
                alpha_source.read(
                    1,
                    out_shape=out_shape,
                    masked=True,
                    resampling=Resampling.nearest,
                ),
                np.nan,
            ).astype(np.float32)

        valid = (
            np.isfinite(h)
            & np.isfinite(alpha)
            & (h >= 0)
            & (h <= 1)
            & (alpha >= 0)
            & (alpha <= 90)
        )
        if not np.any(valid):
            raise ValueError(f"Nenhum par H/alpha valido encontrado em {matrix_dir}.")
        plot_h, plot_alpha = h[valid], alpha[valid]
    else:
        plot_h, plot_alpha = str(h_path), str(alpha_path)

    import matplotlib.pyplot as plt

    plot_path.parent.mkdir(parents=True, exist_ok=True)
    existing_figures = set(plt.get_fignums())
    try:
        polsartools.plot_h_alpha_dp(
            plot_h,
            plot_alpha,
            ppath=str(plot_path),
            cmap=colormap,
            norm=density_norm,
            gridsize=gridsize,
        )
    finally:
        for figure_number in set(plt.get_fignums()) - existing_figures:
            plt.close(figure_number)

    print(f"    Diagrama H-alpha salvo: {plot_path}")
    return plot_path


# Tabela I de Verma et al. (2026): cores aproximadas e descricoes das zonas.
ZONE_COLORS = {
    1: "#fff200",
    2: "#d4e157",
    3: "#ffffff",
    4: "#ffb62e",
    5: "#9ccc3c",
    6: "#3fa9e0",
    7: "#ff1a1a",
    8: "#7cc242",
    9: "#1f3fb0",
}
ZONE_DESCRIPTIONS = {
    1: "Alta entropia, tipo diedro",
    2: "Alta entropia, vegetacao",
    3: "Regiao nao viavel",
    4: "Media entropia, tipo diedro",
    5: "Media entropia, vegetacao",
    6: "Media entropia, tipo superficie",
    7: "Baixa entropia, tipo diedro",
    8: "Baixa entropia, mistura diedro-superficie",
    9: "Baixa entropia, tipo superficie",
}


def classify_zones(h: Any, alpha: Any) -> Any:
    """Classifica pixels em zonas 1-9 (Tabela I); 0 e reservado a invalidos."""
    import numpy as np

    high, medium = h > 0.95, (h > 0.60) & (h <= 0.95)
    low = h <= 0.60
    conditions = [
        high & (alpha > 46),
        high & (alpha > 34) & (alpha <= 46),
        high & (alpha <= 34),
        medium & (alpha >= 46),
        medium & (alpha > 34) & (alpha < 46),
        medium & (alpha <= 34),
        low & (alpha > 46),
        low & (alpha > 40) & (alpha <= 46),
        low & (alpha <= 40),
    ]
    zones = np.select(conditions, [1, 2, 3, 4, 5, 6, 7, 8, 9], default=0)
    return zones.astype(np.uint8)


def compute_h_alpha_dp(
    matrix_dir: Path, win: int, block_rows: int, compress: bool
) -> None:
    """Calcula H e alpha dual-pol em faixas de linhas, sem LAPACK.

    Autovalores/autovetores da matriz hermitiana 2x2 C2 em forma fechada.
    (np.linalg.eig em lote derruba o processo neste ambiente Windows.)
    """
    import numpy as np
    import rasterio
    from rasterio.windows import Window
    from scipy.ndimage import uniform_filter

    names = ["C11", "C12_real", "C12_imag", "C22"]
    paths = [matrix_dir / f"{name}.tif" for name in names]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Arquivos C2 ausentes: {missing}")

    nodata = -9999.0
    pad = win // 2
    srcs = [rasterio.open(path) for path in paths]
    try:
        height, width = srcs[0].height, srcs[0].width
        profile = srcs[0].profile.copy()
        profile.update(
            dtype="float32",
            count=1,
            nodata=nodata,
            tiled=True,
            blockxsize=512,
            blockysize=512,
            compress="lzw" if compress else None,
            BIGTIFF="YES",
        )
        zone_profile = profile.copy()
        zone_profile.update(dtype="uint8", nodata=0)
        zone_counts = np.zeros(10, dtype=np.int64)
        with rasterio.open(matrix_dir / "Hdp.tif", "w", **profile) as dst_h, \
                rasterio.open(matrix_dir / "alphadp.tif", "w", **profile) as dst_a, \
                rasterio.open(matrix_dir / "zones_dp.tif", "w", **zone_profile) as dst_z:
            for y0 in range(0, height, block_rows):
                y1 = min(y0 + block_rows, height)
                r0, r1 = max(y0 - pad, 0), min(y1 + pad, height)
                window = Window(0, r0, width, r1 - r0)
                c11, c12r, c12i, c22 = (
                    np.nan_to_num(src.read(1, window=window).astype(np.float32))
                    for src in srcs
                )
                valid = (c11 != 0) | (c22 != 0)
                if win > 1:
                    c11, c12r, c12i, c22 = (
                        uniform_filter(a, size=win, mode="reflect")
                        for a in (c11, c12r, c12i, c22)
                    )
                sl = slice(y0 - r0, y0 - r0 + (y1 - y0))
                a_, d_, br, bi = (x[sl].astype(np.float64) for x in (c11, c22, c12r, c12i))
                valid = valid[sl]
                b2 = br * br + bi * bi
                mean = (a_ + d_) / 2
                rad = np.sqrt(((a_ - d_) / 2) ** 2 + b2)
                lam = (np.maximum(mean + rad, 0), np.maximum(mean - rad, 0))
                total = lam[0] + lam[1]
                with np.errstate(divide="ignore", invalid="ignore"):
                    ent = np.zeros_like(total)
                    alpha = np.zeros_like(total)
                    for lk in lam:
                        p = lk / total
                        den = b2 + (lk - a_) ** 2
                        v1sq = np.where(den > 0, b2 / den, 1.0)
                        ang = np.degrees(np.arccos(np.sqrt(np.clip(v1sq, 0, 1))))
                        ent -= np.where(p > 0, p * np.log2(p), 0.0)
                        alpha += p * ang
                good = valid & (total > 0) & np.isfinite(ent) & np.isfinite(alpha)
                out = Window(0, y0, width, y1 - y0)
                dst_h.write(np.where(good, ent, nodata).astype(np.float32), 1, window=out)
                dst_a.write(np.where(good, alpha, nodata).astype(np.float32), 1, window=out)
                zones = np.where(good, classify_zones(ent, alpha), 0).astype(np.uint8)
                dst_z.write(zones, 1, window=out)
                zone_counts += np.bincount(zones.ravel(), minlength=10)
                print(f"    linhas {y1}/{height}", end="\r", flush=True)
            dst_z.write_colormap(
                1,
                {
                    zone: tuple(int(color[i : i + 2], 16) for i in (1, 3, 5)) + (255,)
                    for zone, color in ZONE_COLORS.items()
                },
            )
        print()
    finally:
        for src in srcs:
            src.close()

    total_valid = int(zone_counts[1:].sum())
    print("    Distribuicao das zonas (pixels validos):")
    for zone in range(1, 10):
        share = 100 * zone_counts[zone] / total_valid if total_valid else 0.0
        print(f"      Zona {zone}: {share:6.2f}%  {ZONE_DESCRIPTIONS[zone]}")


def generate_zone_figures(
    matrix_dir: Path, config: dict[str, Any], polsartools: Any
) -> None:
    import matplotlib.pyplot as plt
    import numpy as np
    import rasterio
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch, Rectangle
    from rasterio.enums import Resampling

    options = config.get("visualization", {})
    max_dimension = int(options.get("max_plot_dimension", 1000))
    gridsize = int(options.get("gridsize", 300))
    if max_dimension <= 0 or gridsize <= 0:
        raise ValueError("max_plot_dimension e gridsize precisam ser positivos.")

    def read_downsampled(name: str) -> Any:
        with rasterio.open(matrix_dir / name) as src:
            scale = min(1.0, max_dimension / max(src.width, src.height))
            shape = (max(1, round(src.height * scale)), max(1, round(src.width * scale)))
            return src.read(1, out_shape=shape, resampling=Resampling.nearest)

    h, alpha, zones = (
        read_downsampled(name) for name in ("Hdp.tif", "alphadp.tif", "zones_dp.tif")
    )
    valid = zones > 0
    if not np.any(valid):
        raise ValueError(f"Nenhum pixel classificado em {matrix_dir}.")

    legend = [
        Patch(facecolor=ZONE_COLORS[z], edgecolor="k", linewidth=0.4,
              label=f"Zona {z}: {ZONE_DESCRIPTIONS[z]}")
        for z in range(1, 10)
    ]

    cmap = ListedColormap(["#00000000"] + [ZONE_COLORS[z] for z in range(1, 10)])
    fig, ax = plt.subplots(figsize=(8, 8), dpi=200)
    ax.imshow(zones, cmap=cmap, vmin=0, vmax=9, interpolation="nearest")
    ax.set_axis_off()
    ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=7)
    map_path = matrix_dir / "zones_map_dp.png"
    fig.savefig(map_path, bbox_inches="tight")
    plt.close(fig)
    print(f"    Mapa de zonas salvo: {map_path}")

    fig, ax = plt.subplots(figsize=(7, 5), dpi=200)
    hexes = ax.hexbin(h[valid], alpha[valid], gridsize=gridsize, cmap="Greys",
                      mincnt=1, bins="log", extent=(0, 1, 0, 90), zorder=2.5)
    import importlib

    bounds = importlib.import_module(
        "polsartools.analysis.plot_h_alpha_dp"
    ).get_feas_bounds()
    ax.plot(bounds[:, 0], bounds[:, 1], "k-", linewidth=0.5, zorder=3)
    ax.plot(bounds[:, 0], bounds[:, 2], "k-", linewidth=0.5, zorder=3)
    boxes = {
        1: (0.95, 1.0, 46, 90), 2: (0.95, 1.0, 34, 46), 3: (0.95, 1.0, 0, 34),
        4: (0.60, 0.95, 46, 90), 5: (0.60, 0.95, 34, 46), 6: (0.60, 0.95, 0, 34),
        7: (0.0, 0.60, 46, 90), 8: (0.0, 0.60, 40, 46), 9: (0.0, 0.60, 0, 40),
    }
    for zone, (x0, x1, y0, y1) in boxes.items():
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=ZONE_COLORS[zone],
                               edgecolor="k", linewidth=0.6, alpha=0.35, zorder=2))
        ax.text((x0 + x1) / 2, (y0 + y1) / 2, str(zone), ha="center", va="center",
                fontsize=9, fontweight="bold", zorder=4)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 90)
    ax.set_xlabel(r"Entropia, $H_w$")
    ax.set_ylabel(r"$\bar{\alpha}$ (graus)")
    fig.colorbar(hexes, ax=ax, label="#amostras")
    zones_plot_path = matrix_dir / "halpha_zones_plot_dp.png"
    fig.savefig(zones_plot_path, bbox_inches="tight")
    plt.close(fig)
    print(f"    Diagrama H-alpha com zonas salvo: {zones_plot_path}")


def run_scene(task: SceneTask, config: dict[str, Any], dry_run: bool) -> None:
    input_mode = str(config.get("input_mode", "hdf5"))
    if input_mode == "existing_matrix":
        extract_kwargs: dict[str, Any] = {}
        matrix_dir = task.input_path
    elif input_mode == "hdf5":
        extract_kwargs = config.get("nisar_extract_kwargs", {})
        if not isinstance(extract_kwargs, dict):
            raise ValueError("nisar_extract_kwargs precisa ser um objeto JSON.")
        extract_kwargs = _format_kwargs(extract_kwargs, task.input_path, task.output_dir)
        extract_kwargs.setdefault("inFile", str(task.input_path))
        extract_kwargs.setdefault("out_dir", str(task.output_dir))
        extract_kwargs.setdefault("mat", "C2")
        extract_kwargs.setdefault("fmt", "tif")

        matrix_subdir = str(config.get("matrix_input_subdir", "C2")).strip("/\\")
        if not matrix_subdir:
            raise ValueError("matrix_input_subdir nao pode ser vazio.")
        matrix_dir = Path(extract_kwargs["out_dir"]) / matrix_subdir
    else:
        raise ValueError("input_mode precisa ser 'hdf5' ou 'existing_matrix'.")

    h_alpha_kwargs = {
        "win": int(config.get("win", 5)),
        "block_rows": int(config.get("block_rows", 512)),
        "compress": bool(config.get("compress", True)),
    }
    if h_alpha_kwargs["win"] <= 0:
        raise ValueError("win precisa ser um inteiro positivo.")
    if h_alpha_kwargs["block_rows"] <= 0:
        raise ValueError("block_rows precisa ser um inteiro positivo.")
    visualization = config.get("visualization", {})
    if not isinstance(visualization, dict):
        raise ValueError("visualization precisa ser um objeto JSON.")
    visualization_enabled = visualization.get("enabled", True)
    if not isinstance(visualization_enabled, bool):
        raise ValueError("visualization.enabled precisa ser true ou false.")
    plot_filename = str(visualization.get("filename", "halpha_plot_dp.png"))
    if not plot_filename:
        raise ValueError("visualization.filename nao pode ser vazio.")
    plot_path = Path(plot_filename)
    if not plot_path.is_absolute():
        plot_path = matrix_dir / plot_path

    print(f"[{task.sensor}] Cena: {task.input_path}")
    if input_mode == "hdf5":
        print("  - Etapa 0: import_nisar_gslc")
        print(f"    kwargs: {extract_kwargs}")
    else:
        print("  - Etapa 0: usando matriz C2 existente (sem nova extracao)")
    print(f"  - Etapa 1: H/alpha dual-pol (entrada: {matrix_dir})")
    print(f"    kwargs: {h_alpha_kwargs}")
    if visualization_enabled:
        print(f"  - Visualizacao H-alpha: {plot_path}")
    if dry_run:
        return

    task.output_dir.mkdir(parents=True, exist_ok=True)
    polsartools = _import_polsartools()
    if input_mode == "hdf5":
        polsartools.import_nisar_gslc(**extract_kwargs)
    if not matrix_dir.is_dir():
        raise FileNotFoundError(
            f"Diretorio da matriz de entrada nao encontrado: {matrix_dir}"
        )
    compute_h_alpha_dp(matrix_dir, **h_alpha_kwargs)
    if visualization_enabled:
        _generate_h_alpha_plot(matrix_dir, config, polsartools)
        generate_zone_figures(matrix_dir, config, polsartools)

def run_pipeline(config: dict[str, Any], dry_run: bool) -> int:
    tasks = discover_inputs(config)
    sensor_filter = config.get("sensor_filter")
    if sensor_filter:
        allowed_sensors = {str(sensor).lower() for sensor in sensor_filter}
        tasks = [task for task in tasks if task.sensor.lower() in allowed_sensors]

    if not tasks:
        print("Nenhuma cena NISAR encontrada para processar.")
        return 0

    print(f"Cenas encontradas: {len(tasks)}")
    for task in tasks:
        run_scene(task, config, dry_run)
    print("Pipeline H/alpha finalizada.")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extrai entropia e angulo alfa de matrizes C2 NISAR."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Caminho para o arquivo JSON de configuracao.",
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
    config = load_config(_resolve_path(args.config))
    return run_pipeline(config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
