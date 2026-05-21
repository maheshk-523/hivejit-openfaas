"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const WORKLOADS = new Set([
  "router-dispatch",
  "json-codec",
  "regex-tokenizer",
  "template-render",
  "query-aggregate",
]);

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 2; i < argv.length; i++) {
    const item = argv[i];
    if (!item.startsWith("--")) {
      args._.push(item);
      continue;
    }
    const key = item.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

function asInt(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function hrMs(start) {
  return Number(process.hrtime.bigint() - start) / 1e6;
}

function canonicalWorkload(value) {
  const workload = String(value || "router-dispatch").trim().toLowerCase();
  return WORKLOADS.has(workload) ? workload : "router-dispatch";
}

function buildGeneratedRules(functionCount) {
  const lines = [];
  lines.push("function coldMix(v, salt) {");
  lines.push("  v = (v ^ Math.imul(salt, 2654435761)) >>> 0;");
  lines.push("  for (let i = 0; i < 5; i++) {");
  lines.push("    v = Math.imul(v ^ (v >>> 13), 2246822519) >>> 0;");
  lines.push("  }");
  lines.push("  return v >>> 0;");
  lines.push("}");

  for (let i = 0; i < functionCount; i++) {
    const mul = 1664525 + i * 101;
    const add = (1013904223 ^ Math.imul(i + 17, 2654435761)) >>> 0;
    const shift = 5 + (i % 19);
    const branchMask = 31 + (i % 23);
    const branchValue = i % 17;
    lines.push(`function rule_${i}(v, p) {`);
    lines.push(`  v = (Math.imul(v ^ p, ${mul}) + ${add}) >>> 0;`);
    lines.push(`  v = (v ^ (v >>> ${shift})) >>> 0;`);
    lines.push(`  if ((v & ${branchMask}) === ${branchValue}) v = coldMix(v, ${i + 1});`);
    lines.push("  return v >>> 0;");
    lines.push("}");
  }

  lines.push(
    `const RULES = [${Array.from({ length: functionCount }, (_, index) => `rule_${index}`).join(",")}];`,
  );
  lines.push("function applyRule(v, p, r) {");
  lines.push("  return RULES[((v ^ p ^ r) >>> 0) % RULES.length](v, p);");
  lines.push("}");
  return lines;
}

function buildWorkloadSource(workload, functionCount) {
  const lines = ['"use strict";'];
  lines.push(`const WORKLOAD = ${JSON.stringify(workload)};`);
  lines.push(...buildGeneratedRules(functionCount));

  lines.push("function routerDispatch(payload) {");
  lines.push("  let acc = payload.seed >>> 0;");
  lines.push("  const values = payload.values;");
  lines.push("  for (let r = 0; r < payload.rounds; r++) {");
  lines.push("    const p = values[(r * 17) % values.length] >>> 0;");
  lines.push("    const ticket = coldMix(acc ^ p ^ r, r + values.length) % 100;");
  lines.push("    if (ticket < 52) acc = applyRule(acc, p, r);");
  lines.push("    else if (ticket < 76) acc = applyRule(acc ^ p, p + 31, r + 1);");
  lines.push("    else if (ticket < 92) acc = applyRule(acc + p, p + 17, r + 2);");
  lines.push("    else acc = coldMix(applyRule(acc, p ^ r, r + 3), p);");
  lines.push("  }");
  lines.push("  return acc >>> 0;");
  lines.push("}");

  lines.push("function jsonCodec(payload) {");
  lines.push("  let acc = payload.seed >>> 0;");
  lines.push("  for (let pass = 0; pass < payload.jsonPasses; pass++) {");
  lines.push("    const rows = JSON.parse(payload.jsonText);");
  lines.push("    const out = [];");
  lines.push("    for (let i = 0; i < rows.length; i++) {");
  lines.push("      const row = rows[i];");
  lines.push("      const score = applyRule((row.id ^ row.value ^ acc) >>> 0, row.bucket + pass, i);");
  lines.push("      if ((score & 7) !== 0) out.push({ id: row.id, bucket: row.bucket, score, tag: row.tag });");
  lines.push("      acc = (acc + score + row.value) >>> 0;");
  lines.push("    }");
  lines.push("    acc ^= JSON.stringify(out).length >>> 0;");
  lines.push("  }");
  lines.push("  return acc >>> 0;");
  lines.push("}");

  lines.push("function regexTokenizer(payload) {");
  lines.push("  let acc = payload.seed >>> 0;");
  lines.push("  const token = /[A-Za-z_][A-Za-z0-9_]*|\\d+\\.\\d+|\\d+|==|!=|<=|>=|[{}()[\\].,;:+\\-*\\/]/g;");
  lines.push("  const ident = /^[A-Za-z_]/;");
  lines.push("  for (let pass = 0; pass < payload.regexPasses; pass++) {");
  lines.push("    token.lastIndex = 0;");
  lines.push("    let match;");
  lines.push("    while ((match = token.exec(payload.sourceText)) !== null) {");
  lines.push("      const text = match[0];");
  lines.push("      const salt = ident.test(text) ? text.length * 131 : text.charCodeAt(0);");
  lines.push("      acc = applyRule((acc ^ salt) >>> 0, match.index + pass, text.length);");
  lines.push("    }");
  lines.push("  }");
  lines.push("  return acc >>> 0;");
  lines.push("}");

  lines.push("function escapeHtml(value) {");
  lines.push("  return String(value).replace(/[&<>\\\"]/g, ch => ch === '&' ? '&amp;' : ch === '<' ? '&lt;' : ch === '>' ? '&gt;' : '&quot;');");
  lines.push("}");
  lines.push("function templateRender(payload) {");
  lines.push("  let acc = payload.seed >>> 0;");
  lines.push("  let size = 0;");
  lines.push("  for (let pass = 0; pass < payload.templatePasses; pass++) {");
  lines.push("    let html = '';");
  lines.push("    for (let i = 0; i < payload.records.length; i++) {");
  lines.push("      const record = payload.records[i];");
  lines.push("      const score = applyRule((record.value ^ acc) >>> 0, record.bucket + pass, i);");
  lines.push("      html += '<article data-bucket=\"' + record.bucket + '\"><h2>' + escapeHtml(record.name) + '</h2><p>' + score + '</p></article>'; ");
  lines.push("      acc = (acc + score + html.length) >>> 0;");
  lines.push("    }");
  lines.push("    size += html.length;");
  lines.push("  }");
  lines.push("  return (acc ^ size) >>> 0;");
  lines.push("}");

  lines.push("function queryAggregate(payload) {");
  lines.push("  let acc = payload.seed >>> 0;");
  lines.push("  const buckets = new Uint32Array(32);");
  lines.push("  for (let pass = 0; pass < payload.queryPasses; pass++) {");
  lines.push("    buckets.fill(0);");
  lines.push("    for (let i = 0; i < payload.records.length; i++) {");
  lines.push("      const record = payload.records[i];");
  lines.push("      const score = applyRule((record.value + acc) >>> 0, record.bucket + pass, i);");
  lines.push("      if ((score & 3) !== 0) buckets[record.bucket & 31] = (buckets[record.bucket & 31] + score) >>> 0;");
  lines.push("      acc = (acc ^ score ^ record.id) >>> 0;");
  lines.push("    }");
  lines.push("    const ranked = Array.from(buckets).sort((a, b) => b - a);");
  lines.push("    acc = (acc + ranked[0] + ranked[1] + ranked[2]) >>> 0;");
  lines.push("  }");
  lines.push("  return acc >>> 0;");
  lines.push("}");

  lines.push("function handler(payload) {");
  lines.push("  switch (WORKLOAD) {");
  lines.push("    case 'router-dispatch': return routerDispatch(payload);");
  lines.push("    case 'json-codec': return jsonCodec(payload);");
  lines.push("    case 'regex-tokenizer': return regexTokenizer(payload);");
  lines.push("    case 'template-render': return templateRender(payload);");
  lines.push("    case 'query-aggregate': return queryAggregate(payload);");
  lines.push("    default: return routerDispatch(payload);");
  lines.push("  }");
  lines.push("}");
  lines.push("globalThis.__handler = handler;");
  return lines.join("\n");
}

function nextRand(state) {
  return (Math.imul(state ^ (state >>> 13), 1103515245) + 12345) >>> 0;
}

function buildPayload(workload, rounds, seed) {
  const values = [];
  let state = seed >>> 0;
  for (let i = 0; i < 512; i++) {
    state = nextRand(state + i);
    values.push(state >>> 0);
  }

  const records = [];
  const tags = ["alpha", "beta", "delta", "io", "render", "cache", "route", "query"];
  for (let i = 0; i < 220; i++) {
    state = nextRand(state + i * 17);
    records.push({
      id: i + 1,
      bucket: state & 31,
      value: state >>> 0,
      tag: tags[i % tags.length],
      name: `record_${i}_${tags[(state >>> 3) % tags.length]}`,
    });
  }

  const jsonText = JSON.stringify(records);
  const sourceParts = [];
  for (let i = 0; i < 180; i++) {
    const rec = records[i % records.length];
    sourceParts.push(`function fn_${i}(value_${i}) {`);
    sourceParts.push(`  const tmp_${i} = value_${i} ${i % 2 === 0 ? "+" : "^"} ${rec.value};`);
    sourceParts.push(`  return tmp_${i} >= ${rec.bucket} ? tmp_${i} : ${rec.id};`);
    sourceParts.push("}");
  }

  return {
    seed,
    values,
    records,
    jsonText,
    sourceText: sourceParts.join("\n"),
    rounds,
    jsonPasses: Math.max(1, Math.ceil(rounds / 2200)),
    regexPasses: Math.max(1, Math.ceil(rounds / 2600)),
    templatePasses: Math.max(1, Math.ceil(rounds / 2400)),
    queryPasses: Math.max(1, Math.ceil(rounds / 2200)),
  };
}

function compileHandler(source, cachedDataPath) {
  const options = { filename: "node-v8-native-bench.generated.js" };
  let cacheBytes = 0;
  if (cachedDataPath) {
    options.cachedData = fs.readFileSync(cachedDataPath);
    cacheBytes = options.cachedData.byteLength;
  }
  const script = new vm.Script(source, options);
  const context = vm.createContext({ console });
  script.runInContext(context);
  return {
    handler: context.__handler,
    cachedDataRejected: Boolean(script.cachedDataRejected),
    cacheBytes,
    script,
  };
}

function makeCache(args) {
  const workload = canonicalWorkload(args.workload);
  const cacheKind = String(args["cache-kind"] || "cold");
  const functionCount = asInt(args["function-count"], 3200);
  const rounds = asInt(args.rounds, 9000);
  const seed = asInt(args.seed, 0x517cc1b7);
  const warmupIterations = asInt(args["warmup-iterations"], 8);
  const out = args.out || args.cache;
  if (!out) throw new Error("--out is required for make-cache");

  const source = buildWorkloadSource(workload, functionCount);
  const script = new vm.Script(source, { filename: "node-v8-native-bench.generated.js" });
  if (cacheKind === "trained") {
    const context = vm.createContext({ console });
    script.runInContext(context);
    const handler = context.__handler;
    for (let i = 0; i < warmupIterations; i++) {
      handler(buildPayload(workload, rounds, seed + i * 97));
    }
  }

  const cachedData = script.createCachedData();
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, cachedData);
  process.stdout.write(
    JSON.stringify({
      mode: "make-cache",
      workload,
      cache_kind: cacheKind,
      source_bytes: Buffer.byteLength(source),
      cache_bytes: cachedData.byteLength,
      out,
    }) + "\n",
  );
}

function runBenchmark(args) {
  const workload = canonicalWorkload(args.workload);
  const variant = String(args.variant || "source");
  const iterations = asInt(args.iterations, 50);
  const functionCount = asInt(args["function-count"], 3200);
  const rounds = asInt(args.rounds, 9000);
  const seed = asInt(args.seed, 0x517cc1b7);
  const cachePath = args.cache || "";
  const source = buildWorkloadSource(workload, functionCount);
  let compiled = null;
  const rows = [];
  let lastChecksum = 0;

  for (let iteration = 1; iteration <= iterations; iteration++) {
    const payload = buildPayload(workload, rounds, seed + iteration * 104729);
    const started = process.hrtime.bigint();
    let compileMs = 0;
    let cachedDataRejected = false;
    let cacheBytes = 0;

    if (!compiled) {
      const compileStarted = process.hrtime.bigint();
      compiled = compileHandler(source, variant === "source" ? "" : cachePath);
      compileMs = hrMs(compileStarted);
      cachedDataRejected = compiled.cachedDataRejected;
      cacheBytes = compiled.cacheBytes;
    }

    const executeStarted = process.hrtime.bigint();
    lastChecksum = compiled.handler(payload) >>> 0;
    const executeMs = hrMs(executeStarted);
    const totalMs = hrMs(started);
    rows.push({
      iteration,
      total_ms: totalMs,
      compile_ms: compileMs,
      execute_ms: executeMs,
      cached_data_rejected: cachedDataRejected,
      cache_bytes: cacheBytes,
      checksum: lastChecksum,
    });
  }

  process.stdout.write(
    JSON.stringify({
      mode: "run",
      workload,
      variant,
      iterations,
      function_count: functionCount,
      rounds,
      source_bytes: Buffer.byteLength(source),
      node: process.version,
      v8: process.versions.v8,
      checksum: lastChecksum,
      rows,
    }) + "\n",
  );
}

function main() {
  const args = parseArgs(process.argv);
  const mode = args._[0] || args.mode || "run";
  if (mode === "make-cache") {
    makeCache(args);
  } else if (mode === "run") {
    runBenchmark(args);
  } else {
    throw new Error(`unknown mode: ${mode}`);
  }
}

main();
