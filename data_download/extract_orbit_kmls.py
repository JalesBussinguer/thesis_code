"""Seleciona um arquivo-exemplo por orbita e extrai o KML de preview/.

Uso padrao:
    python data_download/extract_orbit_kmls.py

O script procura arquivos .zip em H:/biomass_data/, constroi uma chave unica no formato
SCS_T020_F297 a partir do nome do produto, escolhe um arquivo-exemplo por chave
e extrai o arquivo .kml contido em preview/ para datasets/orbits/.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_DIR = Path("H:/biomass_data/")
DEFAULT_OUTPUT_DIR = ROOT_DIR / "datasets" / "orbits"
DEFAULT_MANIFEST_PATH = DEFAULT_OUTPUT_DIR / "orbit_examples.csv"


@dataclass(frozen=True)
class OrbitExample:
	key: str
	zip_path: Path
	kml_member: str
	output_path: Path


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description=(
			"Seleciona um .zip-exemplo por chave de orbita e extrai o KML dentro de preview/."
		)
	)
	parser.add_argument(
		"--input-dir",
		type=Path,
		default=DEFAULT_INPUT_DIR,
		help="Diretorio com os arquivos .zip do BIOMASS.",
	)
	parser.add_argument(
		"--output-dir",
		type=Path,
		default=DEFAULT_OUTPUT_DIR,
		help="Diretorio de saida para os arquivos .kml.",
	)
	parser.add_argument(
		"--manifest",
		type=Path,
		default=DEFAULT_MANIFEST_PATH,
		help="CSV com a lista dos arquivos-exemplo selecionados.",
	)
	parser.add_argument(
		"--overwrite",
		action="store_true",
		help="Sobrescreve arquivos .kml e manifest existentes.",
	)
	return parser.parse_args()


def build_orbit_key(zip_name: str) -> str:
	parts = [part for part in Path(zip_name).stem.split("_") if part]
	if len(parts) <= 11:
		raise ValueError(
			"Nome de arquivo invalido para extrair a chave de orbita: "
			f"{zip_name}"
		)
	return "_".join((parts[2], parts[10], parts[11]))


def find_preview_kml(zip_path: Path) -> str:
	with ZipFile(zip_path) as archive:
		for member in archive.namelist():
			normalized = member.replace("\\", "/")
			if "/preview/" not in f"/{normalized}".lower():
				continue
			if normalized.lower().endswith(".kml"):
				return member
	raise FileNotFoundError(
		f"Nenhum arquivo .kml foi encontrado na pasta preview dentro de {zip_path.name}."
	)


def collect_orbit_examples(input_dir: Path, output_dir: Path) -> tuple[list[OrbitExample], list[str]]:
	if not input_dir.exists():
		raise FileNotFoundError(f"Diretorio de entrada nao encontrado: {input_dir}")

	examples_by_key: dict[str, OrbitExample] = {}
	warnings: list[str] = []

	for zip_path in sorted(input_dir.glob("*.zip")):
		try:
			key = build_orbit_key(zip_path.name)
		except ValueError as exc:
			warnings.append(str(exc))
			continue

		if key in examples_by_key:
			continue

		try:
			kml_member = find_preview_kml(zip_path)
		except FileNotFoundError as exc:
			warnings.append(str(exc))
			continue

		output_path = output_dir / f"{key}.kml"
		examples_by_key[key] = OrbitExample(
			key=key,
			zip_path=zip_path,
			kml_member=kml_member,
			output_path=output_path,
		)

	return list(examples_by_key.values()), warnings


def extract_kml(example: OrbitExample, overwrite: bool) -> None:
	if example.output_path.exists() and not overwrite:
		return

	example.output_path.parent.mkdir(parents=True, exist_ok=True)
	with ZipFile(example.zip_path) as archive:
		with archive.open(example.kml_member) as source:
			with example.output_path.open("wb") as target:
				target.write(source.read())


def write_manifest(examples: list[OrbitExample], manifest_path: Path) -> None:
	manifest_path.parent.mkdir(parents=True, exist_ok=True)
	with manifest_path.open("w", encoding="utf-8", newline="") as csvfile:
		writer = csv.DictWriter(
			csvfile,
			fieldnames=["orbit_key", "zip_file", "preview_kml", "output_kml"],
		)
		writer.writeheader()
		for example in examples:
			writer.writerow(
				{
					"orbit_key": example.key,
					"zip_file": str(example.zip_path),
					"preview_kml": example.kml_member,
					"output_kml": str(example.output_path),
				}
			)


def main() -> int:
	args = parse_args()
	input_dir = args.input_dir.resolve()
	output_dir = args.output_dir.resolve()
	manifest_path = args.manifest.resolve()

	examples, warnings = collect_orbit_examples(input_dir, output_dir)
	for example in examples:
		extract_kml(example, overwrite=args.overwrite)

	write_manifest(examples, manifest_path)

	print(f"Arquivos .zip analisados em: {input_dir}")
	print(f"Orbitas unicas identificadas: {len(examples)}")
	print(f"Manifest salvo em: {manifest_path}")
	print(f"KMLs extraidos em: {output_dir}")

	if warnings:
		print("Avisos:")
		for warning in warnings:
			print(f"- {warning}")

	return 0


if __name__ == "__main__":
	raise SystemExit(main())