package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"runtime/pprof"
	"sort"
	"time"
)

type route interface {
	Run(uint64) uint64
}

type hotRoute struct{}
type parseRoute struct{}
type regexRoute struct{}
type graphRoute struct{}

var routes = []route{
	hotRoute{},
	parseRoute{},
	regexRoute{},
	graphRoute{},
}

type result struct {
	Domain             string    `json:"domain"`
	Scenario           string    `json:"scenario"`
	GeneratedAt        time.Time `json:"generatedAt"`
	GoVersion          string    `json:"goVersion"`
	GOOS               string    `json:"goos"`
	GOARCH             string    `json:"goarch"`
	Invocations        int       `json:"invocations"`
	IterationsPerInvoke uint64   `json:"iterationsPerInvoke"`
	ElapsedMs          float64   `json:"elapsedMs"`
	PerInvocationNs    float64   `json:"perInvocationNs"`
	InvocationP50Ms    float64   `json:"invocationP50Ms"`
	InvocationP95Ms    float64   `json:"invocationP95Ms"`
	Checksum           uint64    `json:"checksum"`
	CPUProfile         string    `json:"cpuProfile,omitempty"`
}

func mix64(x uint64) uint64 {
	x ^= x >> 33
	x *= 0xff51afd7ed558ccd
	x ^= x >> 33
	x *= 0xc4ceb9fe1a85ec53
	x ^= x >> 33
	return x
}

func (hotRoute) Run(x uint64) uint64 {
	for i := uint64(0); i < 9; i++ {
		x = mix64(x + i*0x9e3779b97f4a7c15)
	}
	return x
}

func (parseRoute) Run(x uint64) uint64 {
	for i := uint64(0); i < 15; i++ {
		x = (x << 7) ^ (x >> 3) ^ mix64(x+i)
	}
	return x
}

func (regexRoute) Run(x uint64) uint64 {
	for i := uint64(0); i < 19; i++ {
		if x&1 == 0 {
			x ^= mix64(x + 31)
		} else {
			x ^= mix64(x + 17)
		}
	}
	return x
}

func (graphRoute) Run(x uint64) uint64 {
	for i := uint64(0); i < 23; i++ {
		x += mix64(x ^ (i * 0x100000001b3))
	}
	return x
}

func chooseRoute(scenario string, i uint64, state uint64) int {
	ticket := mix64(i^state) % 100

	switch scenario {
	case "train", "serve-hot":
		if ticket < 88 {
			return 0
		}
		if ticket < 94 {
			return 1
		}
		if ticket < 98 {
			return 2
		}
		return 3
	case "serve-mixed":
		if ticket < 45 {
			return 0
		}
		if ticket < 65 {
			return 1
		}
		if ticket < 84 {
			return 2
		}
		return 3
	default:
		return int(ticket & 3)
	}
}

func invokeHandler(scenario string, iterations uint64, seed uint64) uint64 {
	state := seed
	for i := uint64(0); i < iterations; i++ {
		r := routes[chooseRoute(scenario, i, state)]
		state ^= r.Run(state + i)
	}
	return state
}

func percentile(values []float64, p float64) float64 {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]float64(nil), values...)
	sort.Float64s(sorted)
	index := int((p / 100) * float64(len(sorted)))
	if index >= len(sorted) {
		index = len(sorted) - 1
	}
	return sorted[index]
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

func run(scenario string, invocations int, iterations uint64, profilePath string) (result, error) {
	stopProfile, err := startCPUProfile(profilePath)
	if err != nil {
		return result{}, err
	}
	defer stopProfile()

	invocationTimes := make([]float64, 0, invocations)
	checksum := uint64(0)
	totalStart := time.Now()

	for i := 0; i < invocations; i++ {
		start := time.Now()
		seed := uint64(0x123456789abcdef0) + uint64(i)*0x9e3779b97f4a7c15
		checksum ^= invokeHandler(scenario, iterations, seed)
		invocationTimes = append(invocationTimes, float64(time.Since(start).Nanoseconds())/1_000_000.0)
	}

	elapsed := time.Since(totalStart)
	return result{
		Domain:              "go-pgo-serverless",
		Scenario:            scenario,
		GeneratedAt:         time.Now().UTC(),
		GoVersion:           runtime.Version(),
		GOOS:                runtime.GOOS,
		GOARCH:              runtime.GOARCH,
		Invocations:         invocations,
		IterationsPerInvoke: iterations,
		ElapsedMs:           float64(elapsed.Nanoseconds()) / 1_000_000.0,
		PerInvocationNs:     float64(elapsed.Nanoseconds()) / float64(invocations) / float64(iterations),
		InvocationP50Ms:     percentile(invocationTimes, 50),
		InvocationP95Ms:     percentile(invocationTimes, 95),
		Checksum:            checksum,
		CPUProfile:          profilePath,
	}, nil
}

func main() {
	scenario := flag.String("scenario", "serve-hot", "scenario: train, serve-hot, serve-mixed")
	invocations := flag.Int("invocations", 4, "number of simulated fresh function invocations")
	iterations := flag.Uint64("iterations", 250000, "work iterations per invocation")
	cpuProfile := flag.String("cpuprofile", "", "write a CPU pprof profile for PGO training")
	asJSON := flag.Bool("json", false, "emit JSON")
	flag.Parse()

	if *invocations <= 0 {
		fmt.Fprintln(os.Stderr, "--invocations must be positive")
		os.Exit(2)
	}
	if *iterations == 0 {
		fmt.Fprintln(os.Stderr, "--iterations must be positive")
		os.Exit(2)
	}

	out, err := run(*scenario, *invocations, *iterations, *cpuProfile)
	if err != nil {
		fmt.Fprintf(os.Stderr, "go-pgo-serverless failed: %v\n", err)
		os.Exit(1)
	}

	if *asJSON {
		enc := json.NewEncoder(os.Stdout)
		if err := enc.Encode(out); err != nil {
			fmt.Fprintf(os.Stderr, "json encode failed: %v\n", err)
			os.Exit(1)
		}
		return
	}

	fmt.Printf(
		"scenario=%s invocations=%d iterations=%d checksum=%d elapsed_ms=%.3f per_invocation_ns=%.2f p50_ms=%.3f p95_ms=%.3f\n",
		out.Scenario,
		out.Invocations,
		out.IterationsPerInvoke,
		out.Checksum,
		out.ElapsedMs,
		out.PerInvocationNs,
		out.InvocationP50Ms,
		out.InvocationP95Ms,
	)
}
