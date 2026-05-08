"use strict";

function buildWorkloadSource(options = {}) {
  const functionCount = Number(options.functionCount || 420);
  const lines = [];

  lines.push('"use strict";');
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
  lines.push("function handler(payload) {");
  lines.push("  let acc = payload.seed >>> 0;");
  lines.push("  const values = payload.values;");
  lines.push("  const rounds = payload.rounds | 0;");
  lines.push("  for (let r = 0; r < rounds; r++) {");
  lines.push("    const idx = (acc + values[r % values.length] + r) % FUNCS.length;");
  lines.push("    acc = FUNCS[idx](acc, values[(r * 17) % values.length]);");
  lines.push("    if ((r & 63) === 0) acc = coldMix(acc, r + values.length);");
  lines.push("  }");
  lines.push("  return acc >>> 0;");
  lines.push("}");
  lines.push("globalThis.handler = handler;");

  return lines.join("\n");
}

function buildPayload(options = {}) {
  const valueCount = Number(options.valueCount || 256);
  const rounds = Number(options.rounds || 2200);
  const values = [];
  let x = 0x12345678;

  for (let i = 0; i < valueCount; i++) {
    x = (Math.imul(x ^ (x >>> 11), 1103515245) + 12345 + i) >>> 0;
    values.push(x >>> 0);
  }

  return {
    seed: 0xdecafbad,
    rounds,
    values,
  };
}

module.exports = {
  buildPayload,
  buildWorkloadSource,
};
