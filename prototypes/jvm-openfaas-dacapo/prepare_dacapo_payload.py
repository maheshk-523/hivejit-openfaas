#!/usr/bin/env python3
"""Prepare the minimal DaCapo payload needed by the OpenFaaS JVM image."""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


DEFAULT_BENCHMARKS = ("h2", "lusearch", "eclipse", "fop", "jython")
DEFAULT_LIB_DIRS = (
    "batik",
    "commons-beanutils",
    "commons-codec",
    "commons-collections",
    "commons-httpclient",
    "commons-lang",
    "commons-logging",
    "daytrader",
    "derby",
    "ezmorph",
    "h2",
    "janino",
    "json",
    "junit",
    "lucene",
    "xerces",
)


def copy_dacapo_jar(src: Path, out: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"DaCapo jar not found: {src}")
    shutil.copy2(src, out / "dacapo.jar")


def should_extract(name: str, root: str, benchmarks: set[str], lib_dirs: set[str]) -> bool:
    if not name.startswith(root) or name.endswith("/"):
        return False
    rel = name[len(root) :]
    parts = rel.split("/")
    if len(parts) < 2:
        return False
    if parts[0] == "dat":
        return parts[1] in benchmarks or parts[1] == "logging.properties"
    if parts[0] == "jar":
        if parts[1] in benchmarks:
            return True
        return len(parts) >= 3 and parts[1] == "lib" and parts[2] in lib_dirs
    return False


def extract_payload(zip_path: Path, out: Path, benchmarks: set[str], lib_dirs: set[str]) -> int:
    if not zip_path.exists():
        raise FileNotFoundError(f"DaCapo release zip not found: {zip_path}")
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        roots = [name for name in zf.namelist() if name.endswith("/") and name.count("/") == 1]
        if not roots:
            raise RuntimeError(f"could not find top-level release directory in {zip_path}")
        root = roots[0]
        for info in zf.infolist():
            if not should_extract(info.filename, root, benchmarks, lib_dirs):
                continue
            rel = info.filename[len(root) :]
            target = out / "dacapo" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def write_manifest(out: Path, zip_path: Path, jar_path: Path, benchmarks: set[str], extracted: int) -> None:
    manifest = out / "MANIFEST.txt"
    manifest.write_text(
        "\n".join(
            [
                "DaCapo OpenFaaS payload",
                f"zip={zip_path}",
                f"jar={jar_path}",
                f"benchmarks={','.join(sorted(benchmarks))}",
                f"extracted_files={extracted}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dacapo-zip", default="/Users/maheshk/dacapo/dacapo-23.11-MR2-chopin.zip", type=Path)
    parser.add_argument("--dacapo-jar", default="/Users/maheshk/dacapo/dacapo.jar", type=Path)
    parser.add_argument("--out", default=Path(__file__).resolve().parent / ".cache" / "dacapo-payload", type=Path)
    parser.add_argument("--benchmarks", default=",".join(DEFAULT_BENCHMARKS))
    parser.add_argument("--lib-dirs", default=",".join(DEFAULT_LIB_DIRS))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    benchmarks = {item.strip() for item in args.benchmarks.split(",") if item.strip()}
    lib_dirs = {item.strip() for item in args.lib_dirs.split(",") if item.strip()}
    if not benchmarks:
        raise SystemExit("--benchmarks must include at least one benchmark")

    if args.out.exists():
        if not args.force:
            raise SystemExit(f"{args.out} already exists; pass --force to replace it")
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)

    copy_dacapo_jar(args.dacapo_jar, args.out)
    extracted = extract_payload(args.dacapo_zip, args.out, benchmarks, lib_dirs)
    write_manifest(args.out, args.dacapo_zip, args.dacapo_jar, benchmarks, extracted)
    print(f"prepared {args.out} with {extracted} extracted files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
