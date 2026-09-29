"""Extract paired C11/C22 samples and estimate ENL as mean squared over sample variance.

The scene-level ENL is the pixel-count-weighted mean of the per-sample ENLs.
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import Window
from shapely.geometry import mapping
from shapely.validation import make_valid


ROOT_DIR = Path(__file__).resolve().parent.parent
SCENE_DIR = Path(
    r"E:\nisar_data\output\nisar\NISAR_L2_PR_GSLC_023_146_A_170_4005_DHDH_A_20260624T090126_20260624T090159_P05023_N_F_J_001\00_extracted\C2HX"
)
DEFAULT_SAMPLES = ROOT_DIR / "datasets" / "ENL_samples" / "ENL_samples_sbsr.geojson"
LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extrai pixels C11/C22 por poligono e estima o ENL amostral."
    )
    parser.add_argument("--c11", type=Path, default=SCENE_DIR / "C11.tif")
    parser.add_argument("--c22", type=Path, default=SCENE_DIR / "C22.tif")
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--output-dir", type=Path, default=SCENE_DIR / "ENL_samples")
    return parser.parse_args()


def load_samples(path: Path) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(f"GeoJSON de amostras nao encontrado: {path}")

    samples = gpd.read_file(path)
    if samples.empty:
        raise ValueError("GeoJSON de amostras nao contem feicoes.")
    if samples.crs is None:
        raise ValueError("GeoJSON de amostras precisa ter CRS definido.")

    samples = samples[["geometry"]].copy()
    samples["geometry"] = samples.geometry.apply(
        lambda geometry: make_valid(geometry) if geometry is not None else None
    )
    samples = samples.loc[
        samples.geometry.notnull() & ~samples.geometry.is_empty
    ].reset_index(drop=True)
    if samples.empty:
        raise ValueError("Nenhuma geometria valida encontrada no GeoJSON de amostras.")

    samples.insert(0, "sample_id", [f"sample_{i:02d}" for i in range(1, len(samples) + 1)])
    return samples


def get_sample_statistics(values: np.ndarray) -> tuple[int, float, float, float | None]:
    count = int(values.size)
    if count == 0:
        return 0, math.nan, math.nan, None

    mean = float(np.mean(values))
    variance = float(np.var(values, ddof=1)) if count > 1 else math.nan
    if count < 2 or not math.isfinite(variance):
        enl = None
    elif variance == 0:
        enl = math.inf
    else:
        enl = mean**2 / variance
    return count, mean, variance, enl


def _clipped_window(geometry, src: rasterio.io.DatasetReader) -> Window | None:
    min_x, min_y, max_x, max_y = geometry.bounds
    inverse_transform = ~src.transform
    pixel_corners = [
        inverse_transform * (x, y)
        for x, y in (
            (min_x, min_y),
            (min_x, max_y),
            (max_x, min_y),
            (max_x, max_y),
        )
    ]
    col_start = max(0, math.floor(min(point[0] for point in pixel_corners)))
    row_start = max(0, math.floor(min(point[1] for point in pixel_corners)))
    col_stop = min(src.width, math.ceil(max(point[0] for point in pixel_corners)))
    row_stop = min(src.height, math.ceil(max(point[1] for point in pixel_corners)))
    if col_stop <= col_start or row_stop <= row_start:
        return None
    return Window(col_start, row_start, col_stop - col_start, row_stop - row_start)


def _weighted_enl(records: list[dict], enl_column: str) -> tuple[int, int, float]:
    eligible = [
        row for row in records
        if row["n_pixels"] >= 2 and row[enl_column] is not None
    ]
    total_pixels = sum(row["n_pixels"] for row in eligible)
    if total_pixels == 0:
        return 0, 0, math.nan

    weighted_value = math.fsum(
        row[enl_column] * row["n_pixels"] for row in eligible
    ) / total_pixels
    return len(eligible), total_pixels, weighted_value


def extract(
    c11_path: Path,
    c22_path: Path,
    samples_path: Path,
    output_dir: Path,
) -> tuple[Path, Path, Path]:
    samples = load_samples(samples_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    pixels_path = output_dir / "pixels.csv"
    sample_summary_path = output_dir / "enl_by_sample.csv"
    weighted_summary_path = output_dir / "enl_weighted_summary.csv"
    records: list[dict] = []

    with rasterio.open(c11_path) as c11, rasterio.open(c22_path) as c22:
        if c11.crs is None or c22.crs is None:
            raise ValueError("C11 e C22 precisam ter CRS definido.")
        if (
            c11.crs != c22.crs
            or c11.width != c22.width
            or c11.height != c22.height
            or c11.transform != c22.transform
        ):
            raise ValueError("C11 e C22 precisam compartilhar CRS e grade espacial.")
        if c11.count < 1 or c22.count < 1:
            raise ValueError("Cada raster precisa ter ao menos uma banda.")

        projected_samples = samples.to_crs(c11.crs)
        with pixels_path.open("w", newline="", encoding="utf-8") as pixel_file:
            pixel_writer = csv.writer(pixel_file)
            pixel_writer.writerow(
                ["sample_id", "row", "column", "x", "y", "C11", "C22"]
            )

            for _, sample in projected_samples.iterrows():
                geometry = sample.geometry
                record = {
                    "sample_id": sample["sample_id"],
                    "n_pixels": 0,
                    "mean_C11": math.nan,
                    "sample_variance_C11": math.nan,
                    "ENL_C11": None,
                    "mean_C22": math.nan,
                    "sample_variance_C22": math.nan,
                    "ENL_C22": None,
                }
                window = _clipped_window(geometry, c11)
                if window is not None:
                    c11_data = c11.read(1, window=window, masked=True)
                    c22_data = c22.read(1, window=window, masked=True)
                    inside = geometry_mask(
                        [mapping(geometry)],
                        transform=c11.window_transform(window),
                        invert=True,
                        out_shape=c11_data.shape,
                        all_touched=False,
                    )
                    c11_values = np.asarray(c11_data.data)
                    c22_values = np.asarray(c22_data.data)
                    valid = (
                        inside
                        & ~np.ma.getmaskarray(c11_data)
                        & ~np.ma.getmaskarray(c22_data)
                        & np.isfinite(c11_values)
                        & np.isfinite(c22_values)
                    )
                    local_rows, local_cols = np.where(valid)
                    values_c11 = c11_values[valid].astype(np.float64, copy=False)
                    values_c22 = c22_values[valid].astype(np.float64, copy=False)
                    full_rows = local_rows + int(window.row_off)
                    full_cols = local_cols + int(window.col_off)
                    transform = c11.transform
                    xs = (
                        transform.a * (full_cols + 0.5)
                        + transform.b * (full_rows + 0.5)
                        + transform.c
                    )
                    ys = (
                        transform.d * (full_cols + 0.5)
                        + transform.e * (full_rows + 0.5)
                        + transform.f
                    )
                    pixel_writer.writerows(
                        zip(
                            [sample["sample_id"]] * len(values_c11),
                            full_rows.tolist(),
                            full_cols.tolist(),
                            xs,
                            ys,
                            values_c11.tolist(),
                            values_c22.tolist(),
                        )
                    )

                    count, mean, variance, enl = get_sample_statistics(values_c11)
                    record.update(
                        n_pixels=count,
                        mean_C11=mean,
                        sample_variance_C11=variance,
                        ENL_C11=enl,
                    )
                    _, mean, variance, enl = get_sample_statistics(values_c22)
                    record.update(
                        mean_C22=mean,
                        sample_variance_C22=variance,
                        ENL_C22=enl,
                    )

                records.append(record)
                LOGGER.info(
                    "%s: %d pixels validos",
                    record["sample_id"],
                    record["n_pixels"],
                )

    with sample_summary_path.open("w", newline="", encoding="utf-8") as summary_file:
        fieldnames = list(records[0].keys())
        writer = csv.DictWriter(summary_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    weighted_rows = []
    for polarization, column in (("C11", "ENL_C11"), ("C22", "ENL_C22")):
        sample_count, total_pixels, enl = _weighted_enl(records, column)
        weighted_rows.append(
            {
                "polarization": polarization,
                "sample_count": sample_count,
                "total_valid_pixels": total_pixels,
                "weighted_ENL": enl,
                "weighting": "number_of_valid_pixels_per_sample",
            }
        )

    with weighted_summary_path.open("w", newline="", encoding="utf-8") as summary_file:
        writer = csv.DictWriter(summary_file, fieldnames=list(weighted_rows[0].keys()))
        writer.writeheader()
        writer.writerows(weighted_rows)

    return pixels_path, sample_summary_path, weighted_summary_path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()
    for path in (args.c11, args.c22, args.samples):
        if not path.exists():
            raise FileNotFoundError(f"Arquivo nao encontrado: {path}")

    pixels_path, sample_summary_path, weighted_summary_path = extract(
        args.c11,
        args.c22,
        args.samples,
        args.output_dir,
    )
    print(f"Pixels extraidos: {pixels_path}")
    print(f"ENL por amostra: {sample_summary_path}")
    print(f"ENL ponderado: {weighted_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
