"use strict";

const WORKLOADS = new Set(["lusearch", "h2", "fop", "jython", "eclipse"]);

function canonicalWorkload(value = "lusearch") {
  let workload = String(value || "lusearch").trim().toLowerCase();
  if (workload.startsWith("dacapo-")) workload = workload.slice("dacapo-".length);
  if (workload === "fopo") workload = "fop";
  return WORKLOADS.has(workload) ? workload : "lusearch";
}

function buildWorkloadSource(options = {}) {
  const functionCount = Number(options.functionCount || 3000);
  const workload = canonicalWorkload(options.workload || options.scenario || "lusearch");
  const lines = [];

  lines.push('"use strict";');
  lines.push(`const WORKLOAD = ${JSON.stringify(workload)};`);
  lines.push("function coldMix(v, salt) {");
  lines.push("  v = (v ^ (salt * 2654435761)) >>> 0;");
  lines.push("  for (let i = 0; i < 7; i++) {");
  lines.push("    v = Math.imul(v ^ (v >>> 13), 2246822519) >>> 0;");
  lines.push("  }");
  lines.push("  return v >>> 0;");
  lines.push("}");

  for (let i = 0; i < functionCount; i++) {
    const a = 1664525 + i * 97;
    const b = 1013904223 ^ (i * 2654435761);
    const shift = 5 + (i % 19);
    lines.push(`function mix_${i}(v, p) {`);
    lines.push(`  v = (Math.imul(v ^ p, ${a}) + ${b}) >>> 0;`);
    lines.push(`  v = (v ^ (v >>> ${shift})) >>> 0;`);
    lines.push(`  if ((v & ${31 + (i % 17)}) === ${i % 13}) v = coldMix(v, ${i + 1});`);
    lines.push("  return v >>> 0;");
    lines.push("}");
  }

  lines.push(`const FUNCS = [${Array.from({ length: functionCount }, (_, i) => `mix_${i}`).join(",")}];`);
  lines.push("function pick(v, p, r) {");
  lines.push("  return FUNCS[((v ^ p ^ r) >>> 0) % FUNCS.length](v, p);");
  lines.push("}");
  lines.push("function routeHot(v, p, r) {");
  lines.push("  return pick(v, p, r);");
  lines.push("}");
  lines.push("function routeParse(v, p, r) {");
  lines.push("  for (let i = 0; i < 3; i++) v = pick((v << 7) ^ (v >>> 3), p + i, r + i);");
  lines.push("  return v >>> 0;");
  lines.push("}");
  lines.push("function routeRegex(v, p, r) {");
  lines.push("  for (let i = 0; i < 4; i++) {");
  lines.push("    v = ((v & 1) === 0 ? pick(v ^ p, p + 31, r + i) : pick(v + p, p + 17, r + i)) >>> 0;");
  lines.push("  }");
  lines.push("  return v >>> 0;");
  lines.push("}");
  lines.push("function routeGraph(v, p, r) {");
  lines.push("  for (let i = 0; i < 5; i++) v = (v + pick(v ^ (i * 16777619), p, r + i)) >>> 0;");
  lines.push("  return v >>> 0;");
  lines.push("}");
  lines.push("function routeInterpreter(v, p, r) {");
  lines.push("  const stack = new Uint32Array(8);");
  lines.push("  let sp = 0;");
  lines.push("  for (let i = 0; i < 5; i++) {");
  lines.push("    stack[sp++ & 7] = pick(v + i, p, r + i);");
  lines.push("    const rhs = stack[(sp - 1) & 7];");
  lines.push("    const lhs = stack[(sp - 2) & 7];");
  lines.push("    v = (v ^ Math.imul((lhs + rhs + i) >>> 0, 16777619)) >>> 0;");
  lines.push("  }");
  lines.push("  return v >>> 0;");
  lines.push("}");
  lines.push("function routeCallSite(v, p, r) {");
  lines.push("  for (let i = 0; i < 5; i++) {");
  lines.push("    v = (i & 1) === 0 ? pick(v ^ 1372390213, p + i, r) : pick(v + 2654435761, p ^ i, r + i);");
  lines.push("  }");
  lines.push("  return v >>> 0;");
  lines.push("}");
  lines.push("function routeXml(v, p, r) {");
  lines.push("  let depth = 0;");
  lines.push("  for (let i = 0; i < 6; i++) {");
  lines.push("    depth += ((v >>> (i & 15)) & 1) === 0 ? 1 : -1;");
  lines.push("    v = pick(v ^ depth, p + depth, r + i);");
  lines.push("  }");
  lines.push("  return v >>> 0;");
  lines.push("}");
  lines.push("function routeLayout(v, p, r) {");
  lines.push("  let page = 1;");
  lines.push("  let cursor = 0;");
  lines.push("  for (let i = 0; i < 7; i++) {");
  lines.push("    const height = 8 + (pick(v, p + i, r) & 63);");
  lines.push("    if (cursor + height > 720) { page++; cursor = 0; }");
  lines.push("    cursor += height;");
  lines.push("    v = pick(v ^ page ^ cursor, p, r + i);");
  lines.push("  }");
  lines.push("  return v >>> 0;");
  lines.push("}");
  lines.push("function routeRender(v, p, r) {");
  lines.push("  for (let i = 0; i < 8; i++) v = pick(v ^ Math.imul(i + 1, 73244475), p, r + i);");
  lines.push("  return v >>> 0;");
  lines.push("}");
  lines.push("function routeByIndex(index, v, p, r) {");
  lines.push("  switch (index) {");
  lines.push("    case 0: return routeHot(v, p, r);");
  lines.push("    case 1: return routeParse(v, p, r);");
  lines.push("    case 2: return routeRegex(v, p, r);");
  lines.push("    case 3: return routeGraph(v, p, r);");
  lines.push("    case 4: return routeInterpreter(v, p, r);");
  lines.push("    case 5: return routeCallSite(v, p, r);");
  lines.push("    case 6: return routeXml(v, p, r);");
  lines.push("    case 7: return routeLayout(v, p, r);");
  lines.push("    default: return routeRender(v, p, r);");
  lines.push("  }");
  lines.push("}");
  lines.push("function chooseRoute(ticket) {");
  lines.push("  switch (WORKLOAD) {");
  lines.push("    case 'lusearch': return ticket < 56 ? 2 : ticket < 76 ? 1 : ticket < 93 ? 0 : 3;");
  lines.push("    case 'h2': return ticket < 48 ? 3 : ticket < 73 ? 0 : ticket < 91 ? 1 : 2;");
  lines.push("    case 'eclipse': return ticket < 43 ? 1 : ticket < 71 ? 3 : ticket < 90 ? 2 : 0;");
  lines.push("    case 'jython': return ticket < 44 ? 4 : ticket < 69 ? 5 : ticket < 88 ? 1 : 3;");
  lines.push("    case 'fop': return ticket < 45 ? 6 : ticket < 76 ? 7 : ticket < 92 ? 8 : 2;");
  lines.push("    default: return ticket & 3;");
  lines.push("  }");
  lines.push("}");
  lines.push("function handler(payload) {");
  lines.push("  let acc = payload.seed >>> 0;");
  lines.push("  const values = payload.values;");
  lines.push("  const rounds = payload.rounds | 0;");
  lines.push("  for (let r = 0; r < rounds; r++) {");
  lines.push("    const p = values[(r * 17) % values.length] >>> 0;");
  lines.push("    const ticket = coldMix(acc ^ p ^ r, r + values.length) % 100;");
  lines.push("    acc = routeByIndex(chooseRoute(ticket), acc, p, r);");
  lines.push("    if ((r & 63) === 0) acc = coldMix(acc, r + values.length);");
  lines.push("  }");
  lines.push("  return acc >>> 0;");
  lines.push("}");
  lines.push("globalThis.handler = handler;");

  return lines.join("\n");
}

function buildPayload(options = {}) {
  const valueCount = Number(options.valueCount || 256);
  const rounds = Number(options.rounds || 10000);
  const seed = Number(options.seed || 0xdecafbad);
  const workload = canonicalWorkload(options.workload || options.scenario || "lusearch");
  const workloadSalt = {
    lusearch: 0x1d872b41,
    h2: 0x3c6ef372,
    fop: 0xa54ff53a,
    jython: 0x510e527f,
    eclipse: 0x9b05688c,
  }[workload];
  const values = [];
  let x = (seed ^ workloadSalt) >>> 0;

  for (let i = 0; i < valueCount; i++) {
    x = (Math.imul(x ^ (x >>> 11), 1103515245) + 12345 + i) >>> 0;
    values.push(x >>> 0);
  }

  return {
    seed,
    rounds,
    values,
  };
}

module.exports = {
  buildPayload,
  buildWorkloadSource,
  canonicalWorkload,
};
