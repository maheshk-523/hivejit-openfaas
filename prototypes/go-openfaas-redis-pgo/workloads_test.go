package main

import "testing"

func TestNormalizeNewDaCapoBenchmarks(t *testing.T) {
	tests := map[string]string{
		"jython":        "dacapo-jython",
		"dacapo-jython": "dacapo-jython",
		"dacapo:jython": "dacapo-jython",
		"fop":           "dacapo-fop",
		"dacapo-fop":    "dacapo-fop",
		"dacapo:fop":    "dacapo-fop",
	}

	for input, want := range tests {
		got, ok := normalizeBenchmark(input)
		if !ok {
			t.Fatalf("normalizeBenchmark(%q) returned ok=false", input)
		}
		if got != want {
			t.Fatalf("normalizeBenchmark(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestNewDaCapoBenchmarksRunDeterministically(t *testing.T) {
	for _, benchmark := range []string{"dacapo-jython", "dacapo-fop"} {
		got, normalized, err := runBenchmarkInvocation(128, 42, benchmark)
		if err != nil {
			t.Fatalf("runBenchmarkInvocation(%q) failed: %v", benchmark, err)
		}
		again, normalizedAgain, err := runBenchmarkInvocation(128, 42, benchmark)
		if err != nil {
			t.Fatalf("second runBenchmarkInvocation(%q) failed: %v", benchmark, err)
		}
		if normalized != benchmark || normalizedAgain != benchmark {
			t.Fatalf("normalized names = %q/%q, want %q", normalized, normalizedAgain, benchmark)
		}
		if got != again {
			t.Fatalf("%s checksum is not deterministic: %x != %x", benchmark, got, again)
		}
	}
}
