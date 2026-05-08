#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$ROOT/ProfileCacheDotNet.csproj"
BUILD="$ROOT/build"
RESULTS="$ROOT/results"
DOTNET_BIN="${DOTNET_BIN:-dotnet}"

if ! command -v "$DOTNET_BIN" >/dev/null 2>&1; then
  echo "SKIP dotnet-readytorun-pgo: .NET SDK is not installed or DOTNET_BIN is not on PATH" >&2
  exit 2
fi

rid() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) echo "osx-arm64" ;;
    Darwin-x86_64) echo "osx-x64" ;;
    Linux-aarch64) echo "linux-arm64" ;;
    Linux-arm64) echo "linux-arm64" ;;
    Linux-x86_64) echo "linux-x64" ;;
    *) echo "" ;;
  esac
}

RID="${RID:-$(rid)}"
if [[ -z "$RID" ]]; then
  echo "Unable to infer RID; set RID explicitly, for example RID=osx-arm64" >&2
  exit 2
fi

IL_DIR="$BUILD/il"
R2R_DIR="$BUILD/r2r"
LAST="$RESULTS/last.jsonl"

mkdir -p "$IL_DIR" "$R2R_DIR" "$RESULTS"
: > "$LAST"

run_json() {
  local label="$1"
  local dll="$2"
  shift 2
  echo "== $label"
  "$DOTNET_BIN" "$dll" "$@" --json | tee "$RESULTS/$label.json" | tee -a "$LAST"
}

run_json_env() {
  local label="$1"
  local dll="$2"
  shift 2
  echo "== $label"
  env DOTNET_TieredPGO=1 DOTNET_TC_QuickJitForLoops=1 "$DOTNET_BIN" "$dll" "$@" --json \
    | tee "$RESULTS/$label.json" \
    | tee -a "$LAST"
}

echo "== Publish IL/JIT baseline"
"$DOTNET_BIN" publish "$PROJECT" -c Release -o "$IL_DIR" \
  -p:PublishReadyToRun=false \
  -p:UseAppHost=false

echo "== Publish ReadyToRun future artifact"
"$DOTNET_BIN" publish "$PROJECT" -c Release -r "$RID" --self-contained false -o "$R2R_DIR" \
  -p:PublishReadyToRun=true \
  -p:UseAppHost=false

IL_DLL="$IL_DIR/ProfileCacheDotNet.dll"
R2R_DLL="$R2R_DIR/ProfileCacheDotNet.dll"

run_json il-hot "$IL_DLL" --scenario serve-hot --invocations 6 --iterations 250000
run_json r2r-hot "$R2R_DLL" --scenario serve-hot --invocations 6 --iterations 250000
run_json_env dynamic-pgo-hot "$IL_DLL" --scenario serve-hot --invocations 10 --iterations 180000
run_json il-mixed "$IL_DLL" --scenario serve-mixed --invocations 6 --iterations 250000
run_json r2r-mixed "$R2R_DLL" --scenario serve-mixed --invocations 6 --iterations 250000

echo "== Artifacts"
ls -lh "$BUILD" "$RESULTS"
