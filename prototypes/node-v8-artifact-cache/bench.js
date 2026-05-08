"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

function parseArgs(argv) {
  const args = {
    runs: 8,
    invocations: 6,
    functionCount: 420,
    rounds: 2200,
    artifactDir: path.join(__dirname, "artifacts"),
    resultsDir: path.join(__dirname, "results"),
  };

  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    if (arg.startsWith("--runs=")) {
      args.runs = Number(arg.slice("--runs=".length));
    } else if (arg === "--runs") {
      args.runs = Number(argv[++i]);
    } else if (arg.startsWith("--invocations=")) {
      args.invocations = Number(arg.slice("--invocations=".length));
    } else if (arg === "--invocations") {
      args.invocations = Number(argv[++i]);
    } else if (arg.startsWith("--function-count=")) {
      args.functionCount = Number(arg.slice("--function-count=".length));
    } else if (arg === "--function-count") {
      args.functionCount = Number(argv[++i]);
    } else if (arg.startsWith("--rounds=")) {
      args.rounds = Number(arg.slice("--rounds=".length));
    } else if (arg === "--rounds") {
      args.rounds = Number(argv[++i]);
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }

  return args;
}

function runProcess(mode, args) {
  const child = spawnSync(
    process.execPath,
    [
      path.join(__dirname, "runtime.js"),
      "--json",
      "--mode",
      mode,
      "--artifact-dir",
      args.artifactDir,
      "--invocations",
      String(args.invocations),
      "--function-count",
      String(args.functionCount),
      "--rounds",
      String(args.rounds),
    ],
    {
      cwd: __dirname,
      encoding: "utf8",
    },
  );

  if (child.status !== 0) {
    throw new Error(`runtime failed for mode=${mode}\n${child.stderr}\n${child.stdout}`);
  }

  return JSON.parse(child.stdout);
}

function mean(values) {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function median(values) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

function summarize(results) {
  return {
    runs: results.length,
    compileMeanMs: mean(results.map((r) => r.compileMs)),
    compileMedianMs: median(results.map((r) => r.compileMs)),
    totalMeanMs: mean(results.map((r) => r.totalMs)),
    totalMedianMs: median(results.map((r) => r.totalMs)),
    executeMeanMs: mean(results.map((r) => r.executeMs)),
    rejected: results.filter((r) => r.cachedDataRejected === true).length,
    artifactBytes: results.find((r) => r.artifactBytes > 0)?.artifactBytes || 0,
  };
}

function printSummary(label, summary) {
  console.log(
    [
      label.padEnd(8),
      `runs=${String(summary.runs).padStart(2)}`,
      `compile_mean=${summary.compileMeanMs.toFixed(3)}ms`,
      `compile_median=${summary.compileMedianMs.toFixed(3)}ms`,
      `total_mean=${summary.totalMeanMs.toFixed(3)}ms`,
      `total_median=${summary.totalMedianMs.toFixed(3)}ms`,
      `execute_mean=${summary.executeMeanMs.toFixed(3)}ms`,
      `rejected=${summary.rejected}`,
      `artifact_bytes=${summary.artifactBytes}`,
    ].join("  "),
  );
}

function main() {
  const args = parseArgs(process.argv);
  fs.mkdirSync(args.artifactDir, { recursive: true });
  fs.mkdirSync(args.resultsDir, { recursive: true });

  const exportResult = runProcess("export", args);
  const none = [];
  const imported = [];

  for (let i = 0; i < args.runs; i++) {
    none.push(runProcess("none", args));
  }

  for (let i = 0; i < args.runs; i++) {
    imported.push(runProcess("import", args));
  }

  const output = {
    generatedAt: new Date().toISOString(),
    config: args,
    export: exportResult,
    none,
    import: imported,
    summary: {
      none: summarize(none),
      import: summarize(imported),
    },
  };

  fs.writeFileSync(path.join(args.resultsDir, "last.json"), JSON.stringify(output, null, 2));

  console.log("Node/V8 artifact cache benchmark");
  console.log(`artifact=${exportResult.artifactPath}`);
  printSummary("none", output.summary.none);
  printSummary("import", output.summary.import);
}

if (require.main === module) {
  main();
}
