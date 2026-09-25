"""Extrai pares VV/VH de um GeoTIFF Sentinel-1 para cada poligono de amostra."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from shapely.validation import make_valid
from tqdm import tqdm


CONFIG_PATH = Path(__file__).with_suffix(".config.json")
LOGGER = logging.getLogger(__name__)


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def load_config(config_path: Path) -> dict[str, object]:
    LOGGER.info("Lendo config: %s", config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Arquivo de config nao encontrado: {config_path}")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    required_keys = (
        "root_dir",
        "tiff_path",
        "samples_geojson_path",
        "output_dir",
        "class_column",
        "sample_column",
        "keep_nodata",
    )
    missing = [key for key in required_keys if key not in config]
    if missing:
        raise ValueError(f"Chaves ausentes na config: {missing}")

    root_dir = _resolve_path(config_path.parent, str(config["root_dir"]))
    if not root_dir.exists():
        raise FileNotFoundError(f"Diretorio root_dir nao encontrado: {root_dir}")

    vv_band = int(config.get("vv_band", 1))
    vh_band = int(config.get("vh_band", 2))
    if vv_band < 1 or vh_band < 1 or vv_band == vh_band:
        raise ValueError("vv_band e vh_band devem ser indices distintos e maiores que zero.")

    return {
        "tiff_path": _resolve_path(root_dir, str(config["tiff_path"])),
        "samples_geojson_path": _resolve_path(root_dir, str(config["samples_geojson_path"])),
        "output_dir": _resolve_path(root_dir, str(config["output_dir"])),
        "class_column": str(config["class_column"]),
        "sample_column": str(config["sample_column"]),
        "keep_nodata": bool(config["keep_nodata"]),
        "vv_band": vv_band,
        "vh_band": vh_band,
    }


def load_samples(samples_path: Path, class_column: str, sample_column: str) -> gpd.GeoDataFrame:
    LOGGER.info("Lendo amostras: %s", samples_path)
    if not samples_path.exists():
        raise FileNotFoundError(f"Arquivo de amostras nao encontrado: {samples_path}")

    samples = gpd.read_file(samples_path)
    if samples.empty:
        raise ValueError("GeoJSON de amostras nao contem feicoes.")

    missing_columns = [column for column in (class_column, sample_column, "geometry") if column not in samples]
    if missing_columns:
        raise ValueError(f"Colunas ausentes no GeoJSON: {missing_columns}")
    if samples.crs is None:
        raise ValueError("GeoJSON de amostras precisa ter CRS definido.")

    samples = samples[[class_column, sample_column, "geometry"]].copy()
    samples = samples.loc[samples.geometry.notnull() & ~samples.geometry.is_empty].copy()
    samples["geometry"] = samples.geometry.apply(make_valid)
    samples = samples.loc[samples.geometry.notnull() & ~samples.geometry.is_empty].copy()
    if samples.empty:
        raise ValueError("Nenhuma geometria valida encontrada no GeoJSON de amostras.")

    LOGGER.info("Amostras validas carregadas: %d", len(samples))
    return samples.reset_index(drop=True)


def _validate_band_descriptions(src: rasterio.io.DatasetReader, vv_band: int, vh_band: int) -> None:
    descriptions = src.descriptions
    for expected_name, band_index in (("VV", vv_band), ("VH", vh_band)):
        description = descriptions[band_index - 1]
        if description and description.strip().upper() != expected_name:
            raise ValueError(
                f"A banda {band_index} foi configurada como {expected_name}, "
                f"mas a descricao do GeoTIFF e {description!r}."
            )


def _is_nodata(value: np.generic, nodata: float | int | None) -> bool:
    if np.issubdtype(np.asarray(value).dtype, np.floating) and not np.isfinite(value):
        return True
    return nodata is not None and value == nodata


def extract_pixels(
    tiff_path: Path,
    samples_gdf: gpd.GeoDataFrame,
    class_column: str,
    sample_column: str,
    vv_band: int,
    vh_band: int,
    keep_nodata: bool,
) -> dict[str, list[str]]:
    LOGGER.info("Iniciando extracao do GeoTIFF Sentinel-1: %s", tiff_path)
    if not tiff_path.exists():
        raise FileNotFoundError(f"GeoTIFF Sentinel-1 nao encontrado: {tiff_path}")

    rows_by_file: dict[str, list[str]] = {}
    with rasterio.open(tiff_path) as src:
        if src.crs is None:
            raise ValueError("GeoTIFF Sentinel-1 sem CRS definido.")
        if src.count < max(vv_band, vh_band):
            raise ValueError(
                f"GeoTIFF possui {src.count} banda(s), mas a configuracao requer "
                f"as bandas {vv_band} e {vh_band}."
            )
        _validate_band_descriptions(src, vv_band, vh_band)

        vv_data = src.read(vv_band)
        vh_data = src.read(vh_band)
        nodata_vv = src.nodatavals[vv_band - 1]
        nodata_vh = src.nodatavals[vh_band - 1]
        samples = samples_gdf.to_crs(src.crs)

        for feature_id, feature in tqdm(
            samples.iterrows(),
            total=len(samples),
            desc="Processando poligonos",
            unit="poligono",
        ):
            mask = geometry_mask(
                [feature.geometry],
                transform=src.transform,
                invert=True,
                out_shape=(src.height, src.width),
                all_touched=False,
            )
            selected_rows, selected_columns = np.where(mask)
            if selected_rows.size == 0:
                LOGGER.warning("A amostra %s nao contem pixels na grade do GeoTIFF.", feature_id)
                continue

            file_stem = f"{feature[class_column]}_{feature[sample_column]}"
            safe_file_stem = "".join(
                character if character.isalnum() or character in ("-", "_") else "_"
                for character in file_stem
            )
            if not safe_file_stem:
                safe_file_stem = f"feature_{feature_id}"

            base_name = safe_file_stem
            suffix_index = 1
            while f"{safe_file_stem}.csv" in rows_by_file:
                suffix_index += 1
                safe_file_stem = f"{base_name}_{suffix_index}"

            feature_rows = ["id,IVV,IVH"]
            pair_id = 1
            for row, column in zip(selected_rows, selected_columns):
                vv_value = vv_data[row, column]
                vh_value = vh_data[row, column]
                if not keep_nodata and (
                    _is_nodata(vv_value, nodata_vv) or _is_nodata(vh_value, nodata_vh)
                ):
                    continue
                feature_rows.append(f"{pair_id},{vv_value},{vh_value}")
                pair_id += 1

            rows_by_file[f"{safe_file_stem}.csv"] = feature_rows

    LOGGER.info("Extracao finalizada. Arquivos prontos: %d", len(rows_by_file))
    return rows_by_file


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    config = load_config(CONFIG_PATH)
    samples = load_samples(
        config["samples_geojson_path"],
        config["class_column"],
        config["sample_column"],
    )
    rows_by_file = extract_pixels(
        tiff_path=config["tiff_path"],
        samples_gdf=samples,
        class_column=config["class_column"],
        sample_column=config["sample_column"],
        vv_band=config["vv_band"],
        vh_band=config["vh_band"],
        keep_nodata=config["keep_nodata"],
    )

    output_dir = config["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    for file_name, rows in tqdm(rows_by_file.items(), desc="Escrevendo CSV", unit="arquivo"):
        (output_dir / file_name).write_text("\n".join(rows) + "\n", encoding="utf-8")

    print(f"Arquivos CSV gerados: {len(rows_by_file)}")
    print(f"Diretorio de saida: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
