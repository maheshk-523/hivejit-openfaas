package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"
)

type handlerResult struct {
	Requests int     `json:"requests"`
	Seed     uint64  `json:"seed"`
	Checksum string  `json:"checksum"`
	WorkMS   float64 `json:"work_ms"`
}

func main() {
	bin := flag.String("bin", "", "handler binary to invoke")
	label := flag.String("label", "", "label written to CSV")
	benchmark := flag.String("benchmark", "router", "handler benchmark workload")
	iterations := flag.Int("iterations", 20, "cold process invocations")
	requests := flag.Int("requests", 350000, "requests per handler invocation")
	csvPath := flag.String("csv", "", "output CSV path")
	seedBase := flag.Uint64("seed-base", 1000, "first seed base")
	flag.Parse()

	if *bin == "" || *label == "" || *csvPath == "" {
		fatalf("-bin, -label, and -csv are required")
	}

	absBin, err := filepath.Abs(*bin)
	if err != nil {
		fatalf("resolve binary path: %v", err)
	}
	if err := os.MkdirAll(filepath.Dir(*csvPath), 0o755); err != nil {
		fatalf("create csv dir: %v", err)
	}

	f, err := os.Create(*csvPath)
	if err != nil {
		fatalf("create csv: %v", err)
	}
	defer f.Close()

	w := csv.NewWriter(f)
	defer w.Flush()
	mustWrite(w, []string{"label", "benchmark", "iteration", "wall_ms", "work_ms", "checksum"})

	for i := 1; i <= *iterations; i++ {
		seed := *seedBase + uint64(i)
		cmd := exec.Command(absBin,
			"-requests", strconv.Itoa(*requests),
			"-seed", strconv.FormatUint(seed, 10),
			"-benchmark", *benchmark,
			"-json",
		)
		cmd.Env = append(os.Environ(), "GOMAXPROCS=1")

		start := time.Now()
		out, err := cmd.CombinedOutput()
		wallMS := float64(time.Since(start).Microseconds()) / 1000.0
		if err != nil {
			fatalf("invoke %s failed on iteration %d: %v\n%s", absBin, i, err, out)
		}

		var result handlerResult
		if err := json.Unmarshal(out, &result); err != nil {
			fatalf("decode handler JSON on iteration %d: %v\n%s", i, err, out)
		}
		mustWrite(w, []string{
			*label,
			*benchmark,
			strconv.Itoa(i),
			fmt.Sprintf("%.3f", wallMS),
			fmt.Sprintf("%.3f", result.WorkMS),
			result.Checksum,
		})
	}
}

func mustWrite(w *csv.Writer, row []string) {
	if err := w.Write(row); err != nil {
		fatalf("write csv: %v", err)
	}
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
