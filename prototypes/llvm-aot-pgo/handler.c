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

static int choose_route(const char *scenario, uint64_t i, uint64_t state) {
  uint64_t ticket = (mix64(i ^ state) % 100u);

  if (strcmp(scenario, "train") == 0 || strcmp(scenario, "serve-hot") == 0) {
    if (ticket < 88) return 0;
    if (ticket < 94) return 1;
    if (ticket < 98) return 2;
    return 3;
  }

  if (strcmp(scenario, "serve-mixed") == 0) {
    if (ticket < 45) return 0;
    if (ticket < 65) return 1;
    if (ticket < 84) return 2;
    return 3;
  }

  return (int)(ticket & 3u);
}

static uint64_t invoke_handler(const char *scenario, uint64_t iterations) {
  uint64_t state = 0x123456789abcdef0ull;

  for (uint64_t i = 0; i < iterations; i++) {
    switch (choose_route(scenario, i, state)) {
      case 0:
        state ^= hot_route(state + i);
        break;
      case 1:
        state ^= parse_route(state + i);
        break;
      case 2:
        state ^= regex_route(state + i);
        break;
      default:
        state ^= graph_route(state + i);
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
