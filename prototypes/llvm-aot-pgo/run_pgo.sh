#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$ROOT/build"
SRC="$ROOT/handler.c"
CLANG=(xcrun clang)
PROFDATA=(xcrun llvm-profdata)

mkdir -p "$BUILD"

BASE="$BUILD/handler-base"
INSTR="$BUILD/handler-instrumented"
PGO="$BUILD/handler-pgo"
RAW="$BUILD/train.profraw"
DATA="$BUILD/train.profdata"

echo "== Build baseline"
"${CLANG[@]}" -O3 "$SRC" -o "$BASE"

echo "== Build instrumented"
"${CLANG[@]}" -O3 -fprofile-instr-generate "$SRC" -o "$INSTR"

echo "== Train and export raw profile"
LLVM_PROFILE_FILE="$RAW" "$INSTR" train 1200000 || true

echo "== Merge profile"
"${PROFDATA[@]}" merge -output="$DATA" "$RAW"

echo "== Build PGO binary"
"${CLANG[@]}" -O3 -fprofile-instr-use="$DATA" "$SRC" -o "$PGO"

echo "== Compare serve-hot"
"$BASE" serve-hot 2200000 || true
"$PGO" serve-hot 2200000 || true

echo "== Compare serve-mixed"
"$BASE" serve-mixed 2200000 || true
"$PGO" serve-mixed 2200000 || true

echo "== Artifacts"
ls -lh "$BUILD"
