#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static uint64_t now_ns(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ((uint64_t)ts.tv_sec * 1000000000ull) + (uint64_t)ts.tv_nsec;
}

static uint64_t mix64(uint64_t x) {
  x ^= x >> 33;
  x *= 0xff51afd7ed558ccdull;
  x ^= x >> 33;
  x *= 0xc4ceb9fe1a85ec53ull;
  x ^= x >> 33;
  return x;
}

static uint64_t rotl64(uint64_t x, unsigned int shift) {
  shift &= 63u;
  if (shift == 0) return x;
  return (x << shift) | (x >> (64u - shift));
}

__attribute__((noinline)) static uint64_t hot_route(uint64_t x) {
  for (int i = 0; i < 9; i++) {
    x = mix64(x + (uint64_t)i * 0x9e3779b97f4a7c15ull);
  }
  return x;
}

__attribute__((noinline)) static uint64_t parse_route(uint64_t x) {
  for (int i = 0; i < 15; i++) {
    x = (x << 7) ^ (x >> 3) ^ mix64(x + (uint64_t)i);
  }
  return x;
}

__attribute__((noinline)) static uint64_t regex_route(uint64_t x) {
  for (int i = 0; i < 19; i++) {
    x ^= (x & 1ull) ? mix64(x + 17u) : mix64(x + 31u);
  }
  return x;
}

__attribute__((noinline)) static uint64_t graph_route(uint64_t x) {
  for (int i = 0; i < 23; i++) {
    x += mix64(x ^ ((uint64_t)i * 0x100000001b3ull));
  }
  return x;
}

__attribute__((noinline)) static uint64_t py_load_route(uint64_t x) {
  for (int i = 0; i < 11; i++) {
    uint64_t slot = (x >> (unsigned)((i * 5) & 31)) & 0xffu;
    x ^= mix64(slot + x + (uint64_t)i * 0x517cc1b727220a95ull);
    x = rotl64(x, 7u);
  }
  return x;
}

__attribute__((noinline)) static uint64_t py_binary_route(uint64_t x) {
  for (int i = 0; i < 17; i++) {
    uint64_t lhs = mix64(x + (uint64_t)i);
    uint64_t rhs = mix64(x ^ ((uint64_t)i * 0xbf58476d1ce4e5b9ull));
    if ((lhs ^ rhs) & 1u) {
      x += lhs * 3u + rhs;
    } else {
      x ^= rotl64(lhs + rhs, (unsigned)i + 3u);
    }
  }
  return x;
}

__attribute__((noinline)) static uint64_t py_call_route(uint64_t x) {
  for (int frame = 0; frame < 9; frame++) {
    uint64_t locals = mix64(x + (uint64_t)frame * 0x100000001b3ull);
    for (int arg = 0; arg < 4; arg++) {
      x ^= mix64(locals + (uint64_t)arg * 131u + x);
      x = rotl64(x, (unsigned)(frame + arg + 5));
    }
  }
  return x;
}

__attribute__((noinline)) static uint64_t py_exception_route(uint64_t x) {
  for (int i = 0; i < 23; i++) {
    uint64_t code = mix64(x + (uint64_t)i * 17u);
    if ((code & 15u) == 0) {
      x ^= mix64(code ^ 0xd1b54a32d192ed03ull);
    } else {
      x += rotl64(code, (unsigned)i + 1u);
    }
  }
  return x;
}

__attribute__((noinline)) static uint64_t xml_parse_route(uint64_t x) {
  for (int token = 0; token < 13; token++) {
    uint64_t kind = (mix64(x + (uint64_t)token) >> 11) & 7u;
    if (kind < 4u) {
      x += mix64(kind + x + 0x9e3779b97f4a7c15ull);
    } else {
      x ^= rotl64(mix64(x ^ kind), (unsigned)token + 2u);
    }
  }
  return x;
}

__attribute__((noinline)) static uint64_t layout_tree_route(uint64_t x) {
  for (int box = 0; box < 18; box++) {
    uint64_t width = (x >> (unsigned)(box & 15)) & 0x3ffu;
    uint64_t height = mix64(x + width + (uint64_t)box) & 0x1ffu;
    x ^= mix64(width * 31u + height * 17u + x);
    x = rotl64(x, (unsigned)(box % 19) + 1u);
  }
  return x;
}

__attribute__((noinline)) static uint64_t pagination_route(uint64_t x) {
  uint64_t page = 1u;
  for (int area = 0; area < 21; area++) {
    uint64_t block = mix64(x + (uint64_t)area * 0x94d049bb133111ebull) & 0xfffu;
    page += (block > 3000u) ? 1u : 0u;
    x ^= mix64(block + page * 4099u + x);
  }
  return x;
}

__attribute__((noinline)) static uint64_t pdf_render_route(uint64_t x) {
  for (int object = 0; object < 24; object++) {
    uint64_t stream = mix64(x ^ ((uint64_t)object * 0xff51afd7ed558ccdull));
    x += (stream & 0xffffu) * 33u;
    x ^= rotl64(stream, (unsigned)object + 7u);
  }
  return x;
}

typedef enum {
  SCENARIO_HOT,
  SCENARIO_MIXED,
  SCENARIO_LUSEARCH,
  SCENARIO_H2,
  SCENARIO_ECLIPSE,
  SCENARIO_JYTHON,
  SCENARIO_FOP,
  SCENARIO_UNIFORM
} scenario_kind;

static scenario_kind parse_scenario(const char *scenario) {
  if (strcmp(scenario, "train") == 0 ||
      strcmp(scenario, "serve-hot") == 0 ||
      strcmp(scenario, "router") == 0) {
    return SCENARIO_HOT;
  }
  if (strcmp(scenario, "serve-mixed") == 0) {
    return SCENARIO_MIXED;
  }
  if (strcmp(scenario, "lusearch") == 0 || strcmp(scenario, "dacapo-lusearch") == 0) {
    return SCENARIO_LUSEARCH;
  }
  if (strcmp(scenario, "h2") == 0 || strcmp(scenario, "dacapo-h2") == 0) {
    return SCENARIO_H2;
  }
  if (strcmp(scenario, "eclipse") == 0 || strcmp(scenario, "dacapo-eclipse") == 0) {
    return SCENARIO_ECLIPSE;
  }
  if (strcmp(scenario, "jython") == 0 || strcmp(scenario, "dacapo-jython") == 0) {
    return SCENARIO_JYTHON;
  }
  if (strcmp(scenario, "fop") == 0 || strcmp(scenario, "dacapo-fop") == 0) {
    return SCENARIO_FOP;
  }
  return SCENARIO_UNIFORM;
}

static int choose_route(scenario_kind scenario, uint64_t i, uint64_t state) {
  uint64_t ticket = (mix64(i ^ state) % 100u);

  if (scenario == SCENARIO_HOT) {
    if (ticket < 88) return 0;
    if (ticket < 94) return 1;
    if (ticket < 98) return 2;
    return 3;
  }

  if (scenario == SCENARIO_MIXED) {
    if (ticket < 45) return 0;
    if (ticket < 65) return 1;
    if (ticket < 84) return 2;
    return 3;
  }

  if (scenario == SCENARIO_LUSEARCH) {
    if (ticket < 54) return 2;
    if (ticket < 76) return 1;
    if (ticket < 93) return 0;
    return 3;
  }

  if (scenario == SCENARIO_H2) {
    if (ticket < 46) return 3;
    if (ticket < 72) return 0;
    if (ticket < 91) return 1;
    return 2;
  }

  if (scenario == SCENARIO_ECLIPSE) {
    if (ticket < 42) return 1;
    if (ticket < 71) return 3;
    if (ticket < 88) return 2;
    return 0;
  }

  if (scenario == SCENARIO_JYTHON) {
    if (ticket < 38) return 0;
    if (ticket < 67) return 1;
    if (ticket < 90) return 2;
    return 3;
  }

  if (scenario == SCENARIO_FOP) {
    if (ticket < 42) return 0;
    if (ticket < 76) return 1;
    if (ticket < 92) return 2;
    return 3;
  }

  return (int)(ticket & 3u);
}

static uint64_t invoke_handler(const char *scenario, uint64_t iterations) {
  uint64_t state = 0x123456789abcdef0ull;
  scenario_kind kind = parse_scenario(scenario);

  for (uint64_t i = 0; i < iterations; i++) {
    switch (choose_route(kind, i, state)) {
      case 0:
        if (kind == SCENARIO_JYTHON) {
          state ^= py_load_route(state + i);
        } else if (kind == SCENARIO_FOP) {
          state ^= xml_parse_route(state + i);
        } else {
          state ^= hot_route(state + i);
        }
        break;
      case 1:
        if (kind == SCENARIO_JYTHON) {
          state ^= py_binary_route(state + i);
        } else if (kind == SCENARIO_FOP) {
          state ^= layout_tree_route(state + i);
        } else {
          state ^= parse_route(state + i);
        }
        break;
      case 2:
        if (kind == SCENARIO_JYTHON) {
          state ^= py_call_route(state + i);
        } else if (kind == SCENARIO_FOP) {
          state ^= pagination_route(state + i);
        } else {
          state ^= regex_route(state + i);
        }
        break;
      default:
        if (kind == SCENARIO_JYTHON) {
          state ^= py_exception_route(state + i);
        } else if (kind == SCENARIO_FOP) {
          state ^= pdf_render_route(state + i);
        } else {
          state ^= graph_route(state + i);
        }
        break;
    }
  }

  return state;
}

int main(int argc, char **argv) {
  const char *scenario = argc > 1 ? argv[1] : "serve-hot";
  uint64_t iterations = argc > 2 ? strtoull(argv[2], NULL, 10) : 1800000ull;

  uint64_t start = now_ns();
  uint64_t result = invoke_handler(scenario, iterations);
  uint64_t elapsed = now_ns() - start;

  printf("scenario=%s iterations=%llu result=%llu elapsed_ms=%.3f per_invocation_ns=%.2f\n",
         scenario,
         (unsigned long long)iterations,
         (unsigned long long)result,
         (double)elapsed / 1000000.0,
         (double)elapsed / (double)iterations);

  return (int)(result & 0xffu);
}
