package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"os"
	"sort"
	"strconv"
)

type sample struct {
	label string
	wall  float64
	work  float64
}

type stats struct {
	label string
	n     int
	mean  float64
	p50   float64
	p95   float64
	min   float64
	max   float64
}

func main() {
	outPath := flag.String("out", "", "optional summary CSV path")
	flag.Parse()
	if flag.NArg() == 0 {
		fatalf("usage: summarize [-out summary.csv] result.csv ...")
	}

	groups := map[string][]sample{}
	for _, path := range flag.Args() {
		readCSV(path, groups)
	}

	summaries := make([]stats, 0, len(groups))
	for label, samples := range groups {
		summaries = append(summaries, summarize(label, samples))
	}
	sort.Slice(summaries, func(i, j int) bool { return summaries[i].label < summaries[j].label })

	fmt.Println("| label | n | mean wall ms | p50 wall ms | p95 wall ms | min | max |")
	fmt.Println("|---|---:|---:|---:|---:|---:|---:|")
	for _, s := range summaries {
		fmt.Printf("| %s | %d | %.3f | %.3f | %.3f | %.3f | %.3f |\n", s.label, s.n, s.mean, s.p50, s.p95, s.min, s.max)
	}

	if *outPath != "" {
		writeSummary(*outPath, summaries)
	}
}

func readCSV(path string, groups map[string][]sample) {
	f, err := os.Open(path)
	if err != nil {
		fatalf("open %s: %v", path, err)
	}
	defer f.Close()

	r := csv.NewReader(f)
	header, err := r.Read()
	if err != nil {
		fatalf("read %s: %v", path, err)
	}
	labelIdx := requiredColumn(path, header, "label")
	wallIdx := requiredColumn(path, header, "wall_ms")
	workIdx := requiredColumn(path, header, "work_ms")
	maxIdx := max(labelIdx, wallIdx, workIdx)

	rows, err := r.ReadAll()
	if err != nil {
		fatalf("read %s: %v", path, err)
	}
	for i, row := range rows {
		if len(row) <= maxIdx {
			fatalf("%s row %d: expected at least %d columns", path, i+2, maxIdx+1)
		}
		wall, err := strconv.ParseFloat(row[wallIdx], 64)
		if err != nil {
			fatalf("%s row %d wall_ms: %v", path, i+2, err)
		}
		work, err := strconv.ParseFloat(row[workIdx], 64)
		if err != nil {
			fatalf("%s row %d work_ms: %v", path, i+2, err)
		}
		label := row[labelIdx]
		groups[label] = append(groups[label], sample{label: label, wall: wall, work: work})
	}
}

func requiredColumn(path string, header []string, name string) int {
	for i, column := range header {
		if column == name {
			return i
		}
	}
	fatalf("%s: missing required column %q", path, name)
	return -1
}

func summarize(label string, samples []sample) stats {
	values := make([]float64, len(samples))
	var sum float64
	for i, s := range samples {
		values[i] = s.wall
		sum += s.wall
	}
	sort.Float64s(values)
	return stats{
		label: label,
		n:     len(values),
		mean:  sum / float64(len(values)),
		p50:   percentile(values, 0.50),
		p95:   percentile(values, 0.95),
		min:   values[0],
		max:   values[len(values)-1],
	}
}

func percentile(sorted []float64, p float64) float64 {
	if len(sorted) == 1 {
		return sorted[0]
	}
	pos := p * float64(len(sorted)-1)
	lower := int(pos)
	upper := lower + 1
	if upper >= len(sorted) {
		return sorted[len(sorted)-1]
	}
	weight := pos - float64(lower)
	return sorted[lower]*(1-weight) + sorted[upper]*weight
}

func writeSummary(path string, summaries []stats) {
	f, err := os.Create(path)
	if err != nil {
		fatalf("create %s: %v", path, err)
	}
	defer f.Close()
	w := csv.NewWriter(f)
	defer w.Flush()
	mustWrite(w, []string{"label", "n", "mean_wall_ms", "p50_wall_ms", "p95_wall_ms", "min_wall_ms", "max_wall_ms"})
	for _, s := range summaries {
		mustWrite(w, []string{
			s.label,
			strconv.Itoa(s.n),
			fmt.Sprintf("%.3f", s.mean),
			fmt.Sprintf("%.3f", s.p50),
			fmt.Sprintf("%.3f", s.p95),
			fmt.Sprintf("%.3f", s.min),
			fmt.Sprintf("%.3f", s.max),
		})
	}
}

func mustWrite(w *csv.Writer, row []string) {
	if err := w.Write(row); err != nil {
		fatalf("write summary: %v", err)
	}
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
