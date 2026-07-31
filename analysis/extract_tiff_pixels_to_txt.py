from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from shapely.validation import make_valid


# ======================= #
# CONFIGURACAO DO USUARIO #
# ======================= #
ROOT_DIR = Path(__file__).resolve().parent.parent
TIFF_PATH = ROOT_DIR / "datasets" / "imagem.tif"
SAMPLES_GEOJSON_PATH = ROOT_DIR / "datasets" / "homogeneity_samples_sbsr.geojson"
OUTPUT_TXT_PATH = ROOT_DIR / "datasets" / "pixels_extraidos.txt"
CLASS_COLUMN = "Class"
SAMPLE_COLUMN = "Sample"
KEEP_NODATA = False


def load_samples(samples_path: Path, class_column: str, sample_column: str) -> gpd.GeoDataFrame:
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

    return gdf.reset_index(drop=True)


def extract_pixels(
    tiff_path: Path,
    samples_gdf: gpd.GeoDataFrame,
    class_column: str,
    sample_column: str,
    keep_nodata: bool,
) -> list[str]:
    if not tiff_path.exists():
        raise FileNotFoundError(f"TIFF nao encontrado: {tiff_path}")

    rows_out: list[str] = []

    with rasterio.open(tiff_path) as src:
        if src.crs is None:
            raise ValueError("TIFF sem CRS definido.")

        raster_data = src.read()  # shape: (bands, rows, cols)
        nodata = src.nodata

        samples = samples_gdf.to_crs(src.crs)

        header = "\t".join(
            [
                "feature_id",
                class_column,
                sample_column,
                "row",
                "col",
                "x",
                "y",
                "band_values",
            ]
        )
        rows_out.append(header)

        for feature_id, feature in samples.iterrows():
            geom = feature.geometry
            if geom.is_empty:
                continue

            mask = geometry_mask(
                [geom],
                transform=src.transform,
                invert=True,
                out_shape=(src.height, src.width),
                all_touched=False,
            )

            selected_rows, selected_cols = np.where(mask)
            if selected_rows.size == 0:
                continue

            for row, col in zip(selected_rows.tolist(), selected_cols.tolist()):
                pixel_values = raster_data[:, row, col]

                if not keep_nodata and nodata is not None:
                    if np.all(pixel_values == nodata):
                        continue

                x, y = rasterio.transform.xy(src.transform, row, col, offset="center")
                values_text = ",".join(str(v) for v in pixel_values.tolist())

                line = "\t".join(
                    [
                        str(feature_id),
                        str(feature[class_column]),
                        str(feature[sample_column]),
                        str(row),
                        str(col),
                        str(x),
                        str(y),
                        values_text,
                    ]
                )
                rows_out.append(line)

    return rows_out


def main() -> int:
    samples_gdf = load_samples(SAMPLES_GEOJSON_PATH, CLASS_COLUMN, SAMPLE_COLUMN)
    rows_out = extract_pixels(
        tiff_path=TIFF_PATH,
        samples_gdf=samples_gdf,
        class_column=CLASS_COLUMN,
        sample_column=SAMPLE_COLUMN,
        keep_nodata=KEEP_NODATA,
    )

    OUTPUT_TXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_TXT_PATH.write_text("\n".join(rows_out) + "\n", encoding="utf-8")

    print(f"TXT gerado: {OUTPUT_TXT_PATH}")
    print(f"Linhas escritas (inclui cabecalho): {len(rows_out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
