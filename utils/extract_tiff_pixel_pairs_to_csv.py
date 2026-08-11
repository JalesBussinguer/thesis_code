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


# ======================= #
# CONFIGURACAO DO USUARIO #
# ======================= #
CONFIG_PATH = Path(__file__).with_suffix(".config.json")
LOGGER = logging.getLogger(__name__)


def _resolve_path(base_dir: Path, value: str) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


def load_config(config_path: Path) -> dict:
    LOGGER.info("Lendo config: %s", config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Arquivo de config nao encontrado: {config_path}")

    config = json.loads(config_path.read_text(encoding="utf-8"))

    required_keys = [
        "root_dir",
        "tiff_c11_path",
        "tiff_c22_path",
        "samples_geojson_path",
        "output_txt_path",
        "class_column",
        "sample_column",
        "keep_nodata",
    ]
    missing = [k for k in required_keys if k not in config]
    if missing:
        raise ValueError(f"Chaves ausentes na config: {missing}")

    root_dir = _resolve_path(config_path.parent, str(config["root_dir"]))
    if not root_dir.exists():
        raise FileNotFoundError(f"Diretorio root_dir nao encontrado: {root_dir}")

    resolved = {
        "tiff_c11_path": _resolve_path(root_dir, str(config["tiff_c11_path"])),
        "tiff_c22_path": _resolve_path(root_dir, str(config["tiff_c22_path"])),
        "samples_geojson_path": _resolve_path(root_dir, str(config["samples_geojson_path"])),
        "output_txt_path": _resolve_path(root_dir, str(config["output_txt_path"])),
        "class_column": str(config["class_column"]),
        "sample_column": str(config["sample_column"]),
        "keep_nodata": bool(config["keep_nodata"]),
    }
    LOGGER.info("Config carregada com sucesso")
    return resolved


def load_samples(samples_path: Path, class_column: str, sample_column: str) -> gpd.GeoDataFrame:
    LOGGER.info("Lendo amostras: %s", samples_path)
    if not samples_path.exists():
        raise FileNotFoundError(f"Arquivo de amostras nao encontrado: {samples_path}")

    gdf = gpd.read_file(samples_path)
    if gdf.empty:
        raise ValueError("GeoJSON de amostras nao contem feicoes.")

    missing_cols = [col for col in (class_column, sample_column, "geometry") if col not in gdf.columns]
    if missing_cols:
        raise ValueError(f"Colunas ausentes no GeoJSON: {missing_cols}")

    if gdf.crs is None:
        raise ValueError("GeoJSON de amostras precisa ter CRS definido.")

    gdf = gdf[[class_column, sample_column, "geometry"]].copy()
    gdf = gdf.loc[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = gdf.geometry.apply(make_valid)
    gdf = gdf.loc[gdf.geometry.notnull() & ~gdf.geometry.is_empty].copy()

    if gdf.empty:
        raise ValueError("Nenhuma geometria valida encontrada no GeoJSON de amostras.")

    LOGGER.info("Amostras validas carregadas: %d", len(gdf))
    return gdf.reset_index(drop=True)


def extract_pixels(
    tiff_c11_path: Path,
    tiff_c22_path: Path,
    samples_gdf: gpd.GeoDataFrame,
    class_column: str,
    sample_column: str,
    keep_nodata: bool,
) -> dict[str, list[str]]:
    LOGGER.info("Iniciando extracao de pixels")
    LOGGER.info("Raster C11: %s", tiff_c11_path)
    LOGGER.info("Raster C22: %s", tiff_c22_path)
    if not tiff_c11_path.exists():
        raise FileNotFoundError(f"TIFF C11 nao encontrado: {tiff_c11_path}")
    if not tiff_c22_path.exists():
        raise FileNotFoundError(f"TIFF C22 nao encontrado: {tiff_c22_path}")

    rows_by_file: dict[str, list[str]] = {}

    with rasterio.open(tiff_c11_path) as src_c11, rasterio.open(tiff_c22_path) as src_c22:
        if src_c11.crs is None or src_c22.crs is None:
            raise ValueError("TIFF sem CRS definido.")

        if src_c11.crs != src_c22.crs:
            raise ValueError("C11 e C22 precisam ter o mesmo CRS.")
        if src_c11.width != src_c22.width or src_c11.height != src_c22.height:
            raise ValueError("C11 e C22 precisam ter mesmas dimensoes.")
        if src_c11.transform != src_c22.transform:
            raise ValueError("C11 e C22 precisam ter a mesma grade espacial (transform).")

        if src_c11.count < 1 or src_c22.count < 1:
            raise ValueError("Cada raster precisa ter ao menos 1 banda.")

        raster_c11 = src_c11.read(1)
        raster_c22 = src_c22.read(1)
        nodata_c11 = src_c11.nodata
        nodata_c22 = src_c22.nodata

        samples = samples_gdf.to_crs(src_c11.crs)

        header = "id,IHH,IHV"
        for feature_id, feature in tqdm(
            samples.iterrows(),
            total=len(samples),
            desc="Processando poligonos",
            unit="poligono",
        ):
            geom = feature.geometry
            if geom.is_empty:
                continue

            class_value = str(feature[class_column])
            sample_value = str(feature[sample_column])
            file_stem = f"{class_value}_{sample_value}"
            safe_file_stem = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in file_stem)
            if not safe_file_stem:
                safe_file_stem = f"feature_{feature_id}"

            base_name = safe_file_stem
            suffix_index = 1
            while f"{safe_file_stem}.csv" in rows_by_file:
                suffix_index += 1
                safe_file_stem = f"{base_name}_{suffix_index}"

            feature_rows = [header]
            pair_id = 1

            mask = geometry_mask(
                [geom],
                transform=src_c11.transform,
                invert=True,
                out_shape=(src_c11.height, src_c11.width),
                all_touched=False,
            )

            selected_rows, selected_cols = np.where(mask)
            if selected_rows.size == 0:
                continue

            for row, col in zip(selected_rows.tolist(), selected_cols.tolist()):
                c11_value = raster_c11[row, col]
                c22_value = raster_c22[row, col]

                if not keep_nodata:
                    c11_is_nodata = nodata_c11 is not None and c11_value == nodata_c11
                    c22_is_nodata = nodata_c22 is not None and c22_value == nodata_c22
                    if c11_is_nodata or c22_is_nodata:
                        continue

                line = f"{pair_id},{c11_value},{c22_value}"
                feature_rows.append(line)
                pair_id += 1

            rows_by_file[f"{safe_file_stem}.csv"] = feature_rows

            LOGGER.info("Extração finalizada. Arquivos prontos: %d", len(rows_by_file))
    return rows_by_file


def get_output_dir(output_path: Path) -> Path:
    if output_path.suffix.lower() in {".txt", ".csv"}:
        return output_path.parent
    return output_path


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    LOGGER.info("Execucao iniciada")

    cfg = load_config(CONFIG_PATH)

    samples_gdf = load_samples(
        cfg["samples_geojson_path"],
        cfg["class_column"],
        cfg["sample_column"],
    )
    rows_out = extract_pixels(
        tiff_c11_path=cfg["tiff_c11_path"],
        tiff_c22_path=cfg["tiff_c22_path"],
        samples_gdf=samples_gdf,
        class_column=cfg["class_column"],
        sample_column=cfg["sample_column"],
        keep_nodata=cfg["keep_nodata"],
    )

    output_dir = get_output_dir(cfg["output_txt_path"])
    output_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Diretorio de saida: %s", output_dir)

    total_files = 0
    total_lines = 0
    for file_name, rows in tqdm(rows_out.items(), total=len(rows_out), desc="Escrevendo CSV", unit="arquivo"):
        output_file = output_dir / file_name
        output_file.write_text("\n".join(rows) + "\n", encoding="utf-8")
        total_files += 1
        total_lines += len(rows)
        LOGGER.debug("Arquivo escrito: %s", output_file)

    print(f"Arquivos CSV gerados: {total_files}")
    print(f"Diretorio de saida: {output_dir}")
    print(f"Linhas escritas totais (inclui cabecalhos): {total_lines}")
    LOGGER.info("Execucao finalizada")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
