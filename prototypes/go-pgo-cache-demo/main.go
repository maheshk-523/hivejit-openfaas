package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"math/bits"
	"os"
	"path/filepath"
	"runtime"
	"runtime/pprof"
	"time"
)

type event struct {
	Tenant  uint64
	Route   uint64
	Weight  uint64
	Payload [8]uint64
}

type operation interface {
	Apply(event) uint64
}

type hashRoute struct{}
type scoreRoute struct{}
type validateRoute struct{}
type enrichRoute struct{}

var sink uint64

func main() {
	requests := flag.Int("requests", 350000, "number of synthetic requests handled by this one-shot serverless invocation")
	seed := flag.Uint64("seed", 1, "deterministic workload seed")
	benchmark := flag.String("benchmark", "router", "benchmark workload: router, dacapo-lusearch, dacapo-eclipse, dacapo-h2, dacapo-jython, dacapo-fop")
	profileOut := flag.String("profile-out", "", "write a Go CPU pprof profile for this invocation")
	jsonOut := flag.Bool("json", false, "print result as JSON")
	flag.Parse()

	stopProfile, err := startCPUProfile(*profileOut)
	if err != nil {
		fatalf("profile start failed: %v", err)
	}

	start := time.Now()
	checksum, normalizedBenchmark, err := runBenchmarkInvocation(*requests, *seed, *benchmark)
	if err != nil {
		fatalf("%v", err)
	}
	workDuration := time.Since(start)

	stopProfile()
	sink ^= checksum

	result := map[string]any{
		"requests":        *requests,
		"seed":            *seed,
		"benchmark":       normalizedBenchmark,
		"checksum":        fmt.Sprintf("%016x", checksum),
		"work_ms":         float64(workDuration.Microseconds()) / 1000.0,
		"go_version":      runtime.Version(),
		"profile_written": *profileOut != "",
	}

	if *jsonOut {
		if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
			fatalf("json encode failed: %v", err)
		}
		return
	}

	fmt.Printf("benchmark=%s requests=%d seed=%d work_ms=%.3f checksum=%016x\n", normalizedBenchmark, *requests, *seed, float64(workDuration.Microseconds())/1000.0, checksum)
}

func startCPUProfile(path string) (func(), error) {
	if path == "" {
		return func() {}, nil
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.Create(path)
	if err != nil {
		return nil, err
	}
	if err := pprof.StartCPUProfile(f); err != nil {
		_ = f.Close()
		return nil, err
	}
	return func() {
		pprof.StopCPUProfile()
		_ = f.Close()
	}, nil
}

func runRouterInvocation(requests int, seed uint64) uint64 {
	ops := []operation{
		hashRoute{},
		hashRoute{},
		hashRoute{},
		hashRoute{},
		scoreRoute{},
		validateRoute{},
		enrichRoute{},
	}

	var total uint64 = seed ^ 0x6a09e667f3bcc909
	state := seed + 0x9e3779b97f4a7c15
	for i := 0; i < requests; i++ {
		state = nextState(state)
		ev := makeEvent(state, uint64(i))

		// The route mix intentionally mirrors production skew: one hot handler,
		// a medium path, and a few cold paths. Go PGO can use this profile to
		// bias inlining and interface-call decisions toward the hot route.
		selector := state % 100
		var op operation
		switch {
		case selector < 84:
			op = ops[int(selector)&3]
		case selector < 94:
			op = ops[4]
		case selector < 98:
			op = ops[5]
		default:
			op = ops[6]
		}

		total ^= handleBurst(op, ev) + uint64(i)*0x100000001b3
		total = bits.RotateLeft64(total, int(ev.Weight&31))
	}
	return total
}

func handleBurst(op operation, ev event) uint64 {
	var total uint64
	for i := 0; i < 12; i++ {
		ev.Payload[i&7] += uint64(i) + total
		total ^= op.Apply(ev)
		total = bits.RotateLeft64(total, int(ev.Route)+i)
	}
	return total
}

func makeEvent(state uint64, index uint64) event {
	var payload [8]uint64
	x := state ^ (index * 0xbf58476d1ce4e5b9)
	for i := range payload {
		x = nextState(x + uint64(i)*0x94d049bb133111eb)
		payload[i] = x
	}
	return event{
		Tenant:  (state >> 9) & 0xffff,
		Route:   state % 17,
		Weight:  (state >> 37) | 1,
		Payload: payload,
	}
}

func (hashRoute) Apply(ev event) uint64 {
	x := ev.Tenant ^ 0xcbf29ce484222325
	for _, word := range ev.Payload {
		x ^= foldHash(word + ev.Weight)
		x *= 0x100000001b3
	}
	return finalize(x ^ ev.Route)
}

func (scoreRoute) Apply(ev event) uint64 {
	x := ev.Weight ^ 0x517cc1b727220a95
	for i, word := range ev.Payload {
		shift := uint((i*7 + int(ev.Route)) & 63)
		x += bits.RotateLeft64(word^ev.Tenant, int(shift))
		x ^= x >> 29
		x *= 0x9ddfea08eb382d69
	}
	return finalize(x)
}

func (validateRoute) Apply(ev event) uint64 {
	var x uint64 = 0x243f6a8885a308d3
	for i, word := range ev.Payload {
		if ((word >> uint(i)) & 7) == ev.Route&7 {
			x ^= foldHash(word ^ ev.Weight)
		} else {
			x += bits.Reverse64(word) ^ ev.Tenant
		}
		x = bits.RotateLeft64(x, 11)
	}
	return finalize(x)
}

func (enrichRoute) Apply(ev event) uint64 {
	x := ev.Tenant + ev.Route + 0x13198a2e03707344
	for i := 0; i < 3; i++ {
		for _, word := range ev.Payload {
			x ^= foldHash(word + uint64(i)*ev.Weight)
			x = bits.RotateLeft64(x*0xff51afd7ed558ccd, 17)
		}
	}
	return finalize(x)
}

func foldHash(x uint64) uint64 {
	x ^= x >> 33
	x *= 0xff51afd7ed558ccd
	x ^= x >> 33
	x *= 0xc4ceb9fe1a85ec53
	x ^= x >> 33
	return x
}

func finalize(x uint64) uint64 {
	x ^= x >> 30
	x *= 0xbf58476d1ce4e5b9
	x ^= x >> 27
	x *= 0x94d049bb133111eb
	return x ^ (x >> 31)
}

func nextState(x uint64) uint64 {
	x += 0x9e3779b97f4a7c15
	x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9
	x = (x ^ (x >> 27)) * 0x94d049bb133111eb
	return x ^ (x >> 31)
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
