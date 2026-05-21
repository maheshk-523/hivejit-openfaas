package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math/bits"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"runtime/pprof"
	"strconv"
	"strings"
	"text/template"
	"time"
)

var sink uint64

type workload struct {
	name string
	run  func(*fixture, int) uint64
}

type fixture struct {
	events       []event
	recordsJSON  []byte
	records      []record
	template     *template.Template
	templateData reportData
	regexpText   string
	regexp       *regexp.Regexp
	gzipPayload  []byte
}

type event struct {
	tenant  uint64
	route   uint64
	weight  uint64
	payload [8]uint64
}

type operation interface {
	Apply(event) uint64
}

type hashRoute struct{}
type scoreRoute struct{}
type validateRoute struct{}
type enrichRoute struct{}

type record struct {
	ID       int      `json:"id"`
	Tenant   string   `json:"tenant"`
	Route    string   `json:"route"`
	Score    float64  `json:"score"`
	Active   bool     `json:"active"`
	Tags     []string `json:"tags"`
	Counters []int    `json:"counters"`
}

type reportData struct {
	Title string
	Rows  []reportRow
}

type reportRow struct {
	Name   string
	Count  int
	Score  float64
	Active bool
	Tags   []string
}

var workloads = []workload{
	{"router-dispatch", runRouterDispatch},
	{"json-codec", runJSONCodec},
	{"template-render", runTemplateRender},
	{"regexp-scan", runRegexpScan},
	{"gzip-hash", runGzipHash},
}

func main() {
	iterations := flag.Int("iterations", 10, "measured iterations per workload")
	scale := flag.Int("scale", 1, "work multiplier")
	seed := flag.Uint64("seed", 1, "deterministic fixture seed")
	profileOut := flag.String("profile-out", "", "write CPU profile")
	csvOut := flag.Bool("csv", false, "write CSV rows to stdout")
	flag.Parse()

	stopProfile, err := startCPUProfile(*profileOut)
	if err != nil {
		fatalf("profile start failed: %v", err)
	}

	fix := newFixture(*seed, *scale)
	if *csvOut {
		writer := csv.NewWriter(os.Stdout)
		if err := writer.Write([]string{"workload", "iteration", "elapsed_ms", "checksum", "go_version"}); err != nil {
			fatalf("csv header failed: %v", err)
		}
		for _, bench := range workloads {
			for iteration := 1; iteration <= *iterations; iteration++ {
				elapsed, checksum := measure(func() uint64 {
					return bench.run(fix, *scale+iteration%3)
				})
				sink ^= checksum
				if err := writer.Write([]string{
					bench.name,
					strconv.Itoa(iteration),
					fmt.Sprintf("%.6f", elapsed),
					fmt.Sprintf("%016x", checksum),
					runtime.Version(),
				}); err != nil {
					fatalf("csv row failed: %v", err)
				}
			}
		}
		writer.Flush()
		if err := writer.Error(); err != nil {
			fatalf("csv flush failed: %v", err)
		}
		stopProfile()
		return
	}

	for _, bench := range workloads {
		for iteration := 1; iteration <= *iterations; iteration++ {
			_, checksum := measure(func() uint64 {
				return bench.run(fix, *scale+iteration%3)
			})
			sink ^= checksum
		}
	}
	stopProfile()
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
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

func measure(fn func() uint64) (float64, uint64) {
	start := time.Now()
	checksum := fn()
	return float64(time.Since(start).Microseconds()) / 1000.0, checksum
}

func newFixture(seed uint64, scale int) *fixture {
	if scale < 1 {
		scale = 1
	}
	events := make([]event, 18000*scale)
	state := seed + 0x9e3779b97f4a7c15
	for i := range events {
		state = nextState(state)
		events[i] = makeEvent(state, uint64(i))
	}

	records := make([]record, 900*scale)
	for i := range records {
		records[i] = record{
			ID:       i,
			Tenant:   fmt.Sprintf("tenant-%03d", i%97),
			Route:    fmt.Sprintf("/api/v1/item/%d", i%31),
			Score:    float64((i*7919)%10000) / 97.0,
			Active:   i%7 != 0,
			Tags:     []string{fmt.Sprintf("region-%d", i%11), fmt.Sprintf("plan-%d", i%5)},
			Counters: []int{i, i * 3, i * 7, i * 11},
		}
	}
	recordsJSON, err := json.Marshal(records)
	if err != nil {
		panic(err)
	}

	tpl := template.Must(template.New("report").Parse(`{{.Title}}
{{range .Rows}}{{if .Active}}{{.Name}} {{printf "%.2f" .Score}} {{.Count}} {{range .Tags}}[{{.}}]{{end}}
{{else}}{{.Name}} inactive
{{end}}{{end}}`))
	rows := make([]reportRow, 450*scale)
	for i := range rows {
		rows[i] = reportRow{
			Name:   fmt.Sprintf("series-%04d", i),
			Count:  17 + (i*13)%1009,
			Score:  float64((i*3571)%100000) / 113.0,
			Active: i%9 != 0,
			Tags:   []string{fmt.Sprintf("team-%d", i%17), fmt.Sprintf("zone-%d", i%7)},
		}
	}

	var text strings.Builder
	for i := 0; i < 2600*scale; i++ {
		fmt.Fprintf(
			&text,
			"ts=%d level=%s route=/api/v1/%d user=user-%05d latency=%d.%03d status=%d trace=%016x\n",
			1700000000+i,
			[]string{"INFO", "WARN", "DEBUG", "ERROR"}[i%4],
			i%41,
			(i*17)%20000,
			(i*37)%800,
			(i*19)%1000,
			[]int{200, 201, 204, 400, 404, 500}[i%6],
			nextState(uint64(i)+seed),
		)
	}

	payload := bytes.Repeat([]byte("profile-cache-native-go-workload:abcdefghijklmnopqrstuvwxyz0123456789\n"), 700*scale)
	return &fixture{
		events:      events,
		recordsJSON: recordsJSON,
		records:     records,
		template:    tpl,
		templateData: reportData{
			Title: "Go native profile cache report",
			Rows:  rows,
		},
		regexpText:  text.String(),
		regexp:      regexp.MustCompile(`route=/api/v1/(\d+) user=(user-\d+) latency=(\d+)\.(\d+) status=(\d+)`),
		gzipPayload: payload,
	}
}

func runRouterDispatch(fix *fixture, scale int) uint64 {
	ops := []operation{hashRoute{}, hashRoute{}, scoreRoute{}, validateRoute{}, enrichRoute{}}
	total := uint64(scale) ^ 0x6a09e667f3bcc909
	for i, ev := range fix.events {
		selector := ev.weight % 100
		var op operation
		switch {
		case selector < 72:
			op = ops[int(selector)&1]
		case selector < 88:
			op = ops[2]
		case selector < 97:
			op = ops[3]
		default:
			op = ops[4]
		}
		total ^= handleBurst(op, ev) + uint64(i)*0x100000001b3
		total = bits.RotateLeft64(total, int(ev.route&31))
	}
	return total
}

func runJSONCodec(fix *fixture, scale int) uint64 {
	var total uint64
	for i := 0; i < 2+scale; i++ {
		var decoded []record
		if err := json.Unmarshal(fix.recordsJSON, &decoded); err != nil {
			panic(err)
		}
		for _, row := range decoded {
			if row.Active {
				total += uint64(row.ID*len(row.Tags) + len(row.Route))
			}
		}
		encoded, err := json.Marshal(decoded[:len(decoded)/2])
		if err != nil {
			panic(err)
		}
		total ^= uint64(len(encoded))
	}
	return finalize(total)
}

func runTemplateRender(fix *fixture, scale int) uint64 {
	var total uint64
	for i := 0; i < 8+scale*2; i++ {
		var buf bytes.Buffer
		if err := fix.template.Execute(&buf, fix.templateData); err != nil {
			panic(err)
		}
		total ^= uint64(buf.Len()) + uint64(bytes.Count(buf.Bytes(), []byte("series")))
	}
	return finalize(total)
}

func runRegexpScan(fix *fixture, scale int) uint64 {
	var total uint64
	for i := 0; i < 5+scale; i++ {
		matches := fix.regexp.FindAllStringSubmatch(fix.regexpText, -1)
		for _, match := range matches {
			if len(match) >= 6 {
				total += uint64(len(match[1]) + len(match[2]) + len(match[5]))
			}
		}
	}
	return finalize(total)
}

func runGzipHash(fix *fixture, scale int) uint64 {
	var total uint64
	for i := 0; i < 4+scale; i++ {
		var buf bytes.Buffer
		zw, err := gzip.NewWriterLevel(&buf, gzip.BestSpeed)
		if err != nil {
			panic(err)
		}
		if _, err := zw.Write(fix.gzipPayload); err != nil {
			panic(err)
		}
		if err := zw.Close(); err != nil {
			panic(err)
		}
		h := sha256.Sum256(buf.Bytes())
		decoded, err := gzip.NewReader(bytes.NewReader(buf.Bytes()))
		if err != nil {
			panic(err)
		}
		n, err := io.Copy(io.Discard, decoded)
		if err != nil {
			panic(err)
		}
		if err := decoded.Close(); err != nil {
			panic(err)
		}
		prefix, _ := hex.DecodeString(hex.EncodeToString(h[:4]))
		total ^= uint64(n) + uint64(prefix[0])<<24 + uint64(prefix[1])<<16 + uint64(prefix[2])<<8 + uint64(prefix[3])
	}
	return finalize(total)
}

func handleBurst(op operation, ev event) uint64 {
	var total uint64
	for i := 0; i < 10; i++ {
		ev.payload[i&7] += uint64(i) + total
		total ^= op.Apply(ev)
		total = bits.RotateLeft64(total, int(ev.route)+i)
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
		tenant:  (state >> 9) & 0xffff,
		route:   state % 17,
		weight:  (state >> 37) | 1,
		payload: payload,
	}
}

func (hashRoute) Apply(ev event) uint64 {
	x := ev.tenant ^ 0xcbf29ce484222325
	for _, word := range ev.payload {
		x ^= foldHash(word + ev.weight)
		x *= 0x100000001b3
	}
	return finalize(x ^ ev.route)
}

func (scoreRoute) Apply(ev event) uint64 {
	x := ev.weight ^ 0x517cc1b727220a95
	for i, word := range ev.payload {
		shift := uint((i*7 + int(ev.route)) & 63)
		x += bits.RotateLeft64(word^ev.tenant, int(shift))
		x ^= x >> 29
		x *= 0x9ddfea08eb382d69
	}
	return finalize(x)
}

func (validateRoute) Apply(ev event) uint64 {
	var x uint64 = 0x243f6a8885a308d3
	for i, word := range ev.payload {
		if ((word >> uint(i)) & 7) == ev.route&7 {
			x ^= foldHash(word ^ ev.weight)
		} else {
			x += bits.Reverse64(word) ^ ev.tenant
		}
		x = bits.RotateLeft64(x, 11)
	}
	return finalize(x)
}

func (enrichRoute) Apply(ev event) uint64 {
	x := ev.tenant + ev.route + 0x13198a2e03707344
	for i := 0; i < 3; i++ {
		for _, word := range ev.payload {
			x ^= foldHash(word + uint64(i)*ev.weight)
			x = bits.RotateLeft64(x*0xff51afd7ed558ccd, 17)
		}
	}
	return finalize(x)
}

func nextState(x uint64) uint64 {
	x += 0x9e3779b97f4a7c15
	x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9
	x = (x ^ (x >> 27)) * 0x94d049bb133111eb
	return x ^ (x >> 31)
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
