"use strict";

const crypto = require("node:crypto");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const vm = require("node:vm");
const { performance } = require("node:perf_hooks");
const { buildPayload, buildWorkloadSource, canonicalWorkload } = require("./workload");

const startedAt = performance.now();
let requestInPod = 0;
let cachedArtifact = null;
let cachedImportMeta = null;

function env(name, fallback = "") {
  return process.env[name] || fallback;
}

function intEnv(name, fallback) {
  const value = Number(env(name, ""));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function mode() {
  return env("V8_CACHE_MODE", "baseline").toLowerCase();
}

function buildLabel() {
  return env("BUILD_LABEL", mode());
}

function artifactKey() {
  return env("V8_CACHE_KEY", `node-v8-cache:${buildLabel()}`);
}

function artifactName(source) {
  return crypto
    .createHash("sha256")
    .update(source)
    .update(process.versions.v8)
    .update(process.platform)
    .update(process.arch)
    .digest("hex")
    .slice(0, 20);
}

function encodeRedisCommand(parts) {
  const chunks = [Buffer.from(`*${parts.length}\r\n`)];
  for (const part of parts) {
    const payload = Buffer.isBuffer(part) ? part : Buffer.from(String(part));
    chunks.push(Buffer.from(`$${payload.length}\r\n`), payload, Buffer.from("\r\n"));
  }
  return Buffer.concat(chunks);
}

function parseResp(buffer, offset = 0) {
  if (offset >= buffer.length) return null;
  const type = String.fromCharCode(buffer[offset]);
  const lineEnd = buffer.indexOf("\r\n", offset);
  if (lineEnd === -1) return null;
  const header = buffer.slice(offset + 1, lineEnd).toString("utf8");

  if (type === "+" || type === "-") {
    return { value: header, error: type === "-", offset: lineEnd + 2 };
  }
  if (type === ":") {
    return { value: Number(header), offset: lineEnd + 2 };
  }
  if (type === "$") {
    const length = Number(header);
    if (length < 0) return { value: null, offset: lineEnd + 2 };
    const start = lineEnd + 2;
    const end = start + length;
    if (buffer.length < end + 2) return null;
    return { value: buffer.slice(start, end), offset: end + 2 };
  }
  throw new Error(`unsupported Redis response type ${type}`);
}

function redisCommands(commandParts) {
  const addr = env("REDIS_ADDR", "profile-cache-redis.openfaas-fn.svc.cluster.local:6379");
  const [host, rawPort] = addr.split(":");
  const port = Number(rawPort || "6379");
  const password = env("REDIS_PASSWORD", "");
  const db = Number(env("REDIS_DB", "0"));
  const commands = [];
  if (password) commands.push(["AUTH", password]);
  if (db) commands.push(["SELECT", String(db)]);
  commands.push(commandParts);

  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host, port });
    let buffer = Buffer.alloc(0);
    let offset = 0;
    const responses = [];
    const started = performance.now();

    socket.setTimeout(10000);
    socket.on("connect", () => {
      socket.write(Buffer.concat(commands.map(encodeRedisCommand)));
    });
    socket.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      try {
        while (responses.length < commands.length) {
          const parsed = parseResp(buffer, offset);
          if (!parsed) break;
          offset = parsed.offset;
          if (parsed.error) {
            throw new Error(Buffer.isBuffer(parsed.value) ? parsed.value.toString("utf8") : String(parsed.value));
          }
          responses.push(parsed.value);
        }
        if (responses.length === commands.length) {
          socket.destroy();
          resolve({ value: responses[responses.length - 1], ms: performance.now() - started });
        }
      } catch (error) {
        socket.destroy();
        reject(error);
      }
    });
    socket.on("timeout", () => {
      socket.destroy();
      reject(new Error("Redis request timed out"));
    });
    socket.on("error", reject);
  });
}

async function redisSet(key, value) {
  return redisCommands(["SET", key, value]);
}

async function redisGet(key) {
  return redisCommands(["GET", key]);
}

async function redisPing() {
  return redisCommands(["PING"]);
}

function runScript({ cachedData, functionCount, rounds, invocations, seed, workload }) {
  const source = buildWorkloadSource({ functionCount, workload });
  const payload = buildPayload({ rounds, seed, workload });
  const hash = artifactName(source);

  const compileStart = performance.now();
  const scriptOptions = {
    filename: "node-openfaas-v8-cache-handler.js",
  };
  if (cachedData) {
    scriptOptions.cachedData = cachedData;
  }
  const script = new vm.Script(source, scriptOptions);
  const compileMs = performance.now() - compileStart;

  const context = vm.createContext({ Math });
  const initStart = performance.now();
  script.runInContext(context);
  const initMs = performance.now() - initStart;

  const oneTimes = [];
  let checksum = 0;
  const executeStart = performance.now();
  for (let i = 0; i < invocations; i++) {
    const oneStart = performance.now();
    checksum ^= context.handler({
      seed: (payload.seed + i * 17) >>> 0,
      rounds: payload.rounds,
      values: payload.values,
    });
    oneTimes.push(performance.now() - oneStart);
  }
  const executeMs = performance.now() - executeStart;

  return {
    source,
    hash,
    script,
    sourceBytes: Buffer.byteLength(source),
    compileMs,
    initMs,
    executeMs,
    totalMs: compileMs + initMs + executeMs,
    invocationP50Ms: percentile(oneTimes, 50),
    invocationP95Ms: percentile(oneTimes, 95),
    checksum: checksum >>> 0,
    cachedDataRejected: script.cachedDataRejected === undefined ? null : script.cachedDataRejected,
  };
}

function percentile(values, p) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[index];
}

async function loadArtifact() {
  if (cachedImportMeta && cachedArtifact) return { artifact: cachedArtifact, meta: cachedImportMeta };
  const key = artifactKey();
  const result = await redisGet(key);
  const artifact = Buffer.isBuffer(result.value) ? result.value : null;
  cachedArtifact = artifact;
  cachedImportMeta = {
    redis_key: key,
    import_ms: result.ms,
    artifact_found: Boolean(artifact),
    artifact_bytes: artifact ? artifact.length : 0,
  };
  return { artifact, meta: cachedImportMeta };
}

function requestParams(req, body) {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  const params = Object.fromEntries(url.searchParams.entries());
  if (body) {
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed === "object") Object.assign(params, parsed);
    } catch {
      // Keep query-string parameters if body is not JSON.
    }
  }
  return params;
}

function common() {
  return {
    mode: mode(),
    build: buildLabel(),
    hostname: os.hostname(),
    pod_uid: env("POD_UID", ""),
    process_uptime_ms: performance.now() - startedAt,
    node: process.versions.node,
    v8: process.versions.v8,
    redis_key: artifactKey(),
  };
}

async function handleWork(params) {
  requestInPod += 1;
  const currentMode = mode();
  const workload = canonicalWorkload(params.workload || params.scenario || env("WORKLOAD", "lusearch"));
  const functionCount = Number(params.functionCount || params.function_count || env("FUNCTION_COUNT", "3000"));
  const rounds = Number(params.rounds || env("ROUNDS", "10000"));
  const invocations = Number(params.invocations || env("REQUEST_INVOCATIONS", "8"));
  const seed = Number(params.seed || 0xdecafbad);

  let artifact = null;
  let importMeta = { redis_key: artifactKey(), import_ms: 0, artifact_found: false, artifact_bytes: 0 };
  if (currentMode === "redis") {
    const loaded = await loadArtifact();
    artifact = loaded.artifact;
    importMeta = loaded.meta;
    if (!artifact && env("V8_CACHE_REQUIRE_ARTIFACT", "1") === "1") {
      throw new Error(`missing Redis V8 cache artifact for key ${artifactKey()}`);
    }
  }

  const started = performance.now();
  const result = runScript({ cachedData: artifact, functionCount, rounds, invocations, seed, workload });
  return {
    ok: true,
    ...common(),
    workload,
    request_in_pod: requestInPod,
    first_work: requestInPod === 1,
    work_ms: performance.now() - started,
    function_count: functionCount,
    rounds,
    request_invocations: invocations,
    source_bytes: result.sourceBytes,
    artifact_hash: result.hash,
    artifact_bytes: artifact ? artifact.length : 0,
    cache_imported: Boolean(artifact),
    ...importMeta,
    compile_ms: result.compileMs,
    init_ms: result.initMs,
    execute_ms: result.executeMs,
    total_ms: result.totalMs,
    invocation_p50_ms: result.invocationP50Ms,
    invocation_p95_ms: result.invocationP95Ms,
    cached_data_rejected: result.cachedDataRejected,
    checksum: String(result.checksum),
  };
}

async function handlePopulate(params) {
  const workload = canonicalWorkload(params.workload || params.scenario || env("WORKLOAD", "lusearch"));
  const functionCount = Number(params.functionCount || params.function_count || env("FUNCTION_COUNT", "3000"));
  const rounds = Number(params.rounds || env("ROUNDS", "10000"));
  const invocations = Number(params.invocations || env("REQUEST_INVOCATIONS", "8"));
  const seed = Number(params.seed || 0xdecafbad);
  const started = performance.now();
  const result = runScript({ functionCount, rounds, invocations, seed, workload });
  const exportStart = performance.now();
  const artifact = result.script.createCachedData();
  const exportMs = performance.now() - exportStart;
  const setResult = await redisSet(artifactKey(), artifact);
  cachedArtifact = artifact;
  cachedImportMeta = {
    redis_key: artifactKey(),
    import_ms: 0,
    artifact_found: true,
    artifact_bytes: artifact.length,
  };

  return {
    ok: true,
    ...common(),
    workload,
    handler_ms: performance.now() - started,
    function_count: functionCount,
    rounds,
    request_invocations: invocations,
    source_bytes: result.sourceBytes,
    artifact_hash: result.hash,
    artifact_bytes: artifact.length,
    export_ms: exportMs,
    redis_set_ms: setResult.ms,
    compile_ms: result.compileMs,
    init_ms: result.initMs,
    execute_ms: result.executeMs,
    total_ms: result.totalMs,
    checksum: String(result.checksum),
  };
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > 1 << 20) {
        reject(new Error("request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function send(res, status, payload) {
  const body = Buffer.from(JSON.stringify(payload, null, 2));
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": body.length });
  res.end(body);
}

async function route(req, res) {
  try {
    const body = req.method === "POST" ? await readBody(req) : "";
    const params = requestParams(req, body);
    const path = new URL(req.url, `http://${req.headers.host || "localhost"}`).pathname;
    if (path === "/" || path === "/work") {
      send(res, 200, await handleWork(params));
    } else if (path === "/cache/populate") {
      send(res, 200, await handlePopulate(params));
    } else if (path === "/cache/ping") {
      const reply = await redisPing();
      send(res, 200, { ok: true, reply: Buffer.isBuffer(reply.value) ? reply.value.toString("utf8") : reply.value, ...common() });
    } else if (path === "/healthz" || path === "/_/health") {
      send(res, 200, { ok: true, ...common() });
    } else {
      send(res, 404, { ok: false, error: "not found", path, ...common() });
    }
  } catch (error) {
    send(res, 500, { ok: false, error: error.message, ...common() });
  }
}

const port = Number(env("PORT", env("http_port", "8080")));
http.createServer(route).listen(port, "0.0.0.0", () => {
  console.log(JSON.stringify({ event: "started", port, ...common() }));
});
