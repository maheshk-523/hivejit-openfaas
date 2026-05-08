"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { performance } = require("node:perf_hooks");
const { buildPayload, buildWorkloadSource } = require("./workload");

function parseArgs(argv) {
  const args = {
    mode: "none",
    artifactDir: path.join(__dirname, "artifacts"),
    json: false,
    invocations: 6,
    functionCount: 420,
    rounds: 2200,
  };

  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--json") {
      args.json = true;
    } else if (arg.startsWith("--mode=")) {
      args.mode = arg.slice("--mode=".length);
    } else if (arg === "--mode") {
      args.mode = argv[++i];
    } else if (arg.startsWith("--artifact-dir=")) {
      args.artifactDir = arg.slice("--artifact-dir=".length);
    } else if (arg === "--artifact-dir") {
      args.artifactDir = argv[++i];
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

  if (!["none", "export", "import"].includes(args.mode)) {
    throw new Error("--mode must be one of none, export, import");
  }

  return args;
}

function artifactName(source) {
  const hash = crypto
    .createHash("sha256")
    .update(source)
    .update(process.versions.v8)
    .update(process.platform)
    .update(process.arch)
    .digest("hex")
    .slice(0, 20);

  return {
    hash,
    file: `handler-${hash}.v8cache`,
    meta: `handler-${hash}.json`,
  };
}

function percentile(values, p) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[index];
}

function runOnce(args) {
  fs.mkdirSync(args.artifactDir, { recursive: true });

  const source = buildWorkloadSource({ functionCount: args.functionCount });
  const payload = buildPayload({ rounds: args.rounds });
  const artifact = artifactName(source);
  const artifactPath = path.join(args.artifactDir, artifact.file);
  const metaPath = path.join(args.artifactDir, artifact.meta);

  const cachedData =
    args.mode === "import" && fs.existsSync(artifactPath)
      ? fs.readFileSync(artifactPath)
      : undefined;

  const compileStart = performance.now();
  const script = new vm.Script(source, {
    filename: "profile-cache-handler.bundle.js",
    cachedData,
  });
  const compileMs = performance.now() - compileStart;

  const context = vm.createContext({ Math });
  const initStart = performance.now();
  script.runInContext(context);
  const initMs = performance.now() - initStart;

  const invocationTimes = [];
  let checksum = 0;
  const executeStart = performance.now();
  for (let i = 0; i < args.invocations; i++) {
    const oneStart = performance.now();
    checksum ^= context.handler({
      seed: (payload.seed + i * 17) >>> 0,
      rounds: payload.rounds,
      values: payload.values,
    });
    invocationTimes.push(performance.now() - oneStart);
  }
  const executeMs = performance.now() - executeStart;

  let artifactBytes = cachedData ? cachedData.length : 0;
  let exported = false;
  if (args.mode === "export") {
    const exportStart = performance.now();
    const cacheBuffer = script.createCachedData();
    const exportMs = performance.now() - exportStart;
    fs.writeFileSync(artifactPath, cacheBuffer);
    fs.writeFileSync(
      metaPath,
      JSON.stringify(
        {
          hash: artifact.hash,
          sourceBytes: Buffer.byteLength(source),
          artifactBytes: cacheBuffer.length,
          node: process.versions.node,
          v8: process.versions.v8,
          platform: process.platform,
          arch: process.arch,
          exportMs,
        },
        null,
        2,
      ),
    );
    artifactBytes = cacheBuffer.length;
    exported = true;
  }

  return {
    mode: args.mode,
    pid: process.pid,
    node: process.versions.node,
    v8: process.versions.v8,
    platform: process.platform,
    arch: process.arch,
    host: os.hostname(),
    sourceBytes: Buffer.byteLength(source),
    artifactHash: artifact.hash,
    artifactPath,
    artifactBytes,
    artifactExists: fs.existsSync(artifactPath),
    exported,
    cachedDataSupplied: Boolean(cachedData),
    cachedDataRejected: script.cachedDataRejected === undefined ? null : script.cachedDataRejected,
    compileMs,
    initMs,
    executeMs,
    totalMs: compileMs + initMs + executeMs,
    invocationP50Ms: percentile(invocationTimes, 50),
    invocationP95Ms: percentile(invocationTimes, 95),
    invocations: args.invocations,
    checksum: checksum >>> 0,
  };
}

function main() {
  const args = parseArgs(process.argv);
  const result = runOnce(args);
  if (args.json) {
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } else {
    console.log(JSON.stringify(result, null, 2));
  }
}

if (require.main === module) {
  main();
}

module.exports = {
  runOnce,
};
