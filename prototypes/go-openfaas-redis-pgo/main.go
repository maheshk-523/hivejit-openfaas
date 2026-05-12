package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/bits"
	"net"
	"net/http"
	"os"
	"runtime"
	"runtime/pprof"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
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

type workRequest struct {
	Requests  int    `json:"requests"`
	Seed      uint64 `json:"seed"`
	Benchmark string `json:"benchmark"`
}

type redisClient struct {
	addr     string
	password string
	db       int
	timeout  time.Duration
}

type server struct {
	redis       redisClient
	profileLock sync.Mutex
	seedCounter atomic.Uint64
}

var sink uint64

func main() {
	addr := envDefault("HANDLER_ADDR", ":8082")
	if v := os.Getenv("http_upstream_url"); v != "" {
		addr = strings.TrimPrefix(v, "http://")
	}

	s := &server{
		redis: redisClient{
			addr:     envDefault("REDIS_ADDR", envDefault("redis_addr", "profile-cache-redis:6379")),
			password: envDefault("REDIS_PASSWORD", envDefault("redis_password", "")),
			db:       envInt("REDIS_DB", envInt("redis_db", 0)),
			timeout:  envDuration("REDIS_TIMEOUT", 5*time.Second),
		},
	}
	s.seedCounter.Store(uint64(time.Now().UnixNano()))

	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleWork)
	mux.HandleFunc("/work", s.handleWork)
	mux.HandleFunc("/healthz", s.handleHealth)
	mux.HandleFunc("/profile/ping", s.handleProfilePing)
	mux.HandleFunc("/profile/capture", s.handleProfileCapture)
	mux.HandleFunc("/profile/fetch", s.handleProfileFetch)
	mux.HandleFunc("/profile/put", s.handleProfilePut)

	httpServer := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}

	fmt.Fprintf(os.Stderr, "go-pgo-redis handler listening on %s redis=%s build=%s\n", addr, s.redis.addr, buildLabel())
	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		fmt.Fprintf(os.Stderr, "server failed: %v\n", err)
		os.Exit(1)
	}
}

func (s *server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "build": buildLabel()})
}

func (s *server) handleWork(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" && r.URL.Path != "/work" {
		http.NotFound(w, r)
		return
	}

	req := workRequest{
		Requests:  queryInt(r, "requests", 350000),
		Seed:      queryUint64(r, "seed", 0),
		Benchmark: queryString(r, "benchmark", envDefault("BENCHMARK", "router")),
	}
	if r.Method == http.MethodPost && r.Body != nil {
		defer r.Body.Close()
		if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil && !errors.Is(err, io.EOF) {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
	}
	if req.Requests <= 0 {
		req.Requests = 350000
	}
	if req.Seed == 0 {
		req.Seed = s.seedCounter.Add(1)
	}

	start := time.Now()
	checksum, normalizedBenchmark, err := runBenchmarkInvocation(req.Requests, req.Seed, req.Benchmark)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	workDuration := time.Since(start)
	sink ^= checksum

	writeJSON(w, http.StatusOK, map[string]any{
		"requests":   req.Requests,
		"seed":       req.Seed,
		"benchmark":  normalizedBenchmark,
		"checksum":   fmt.Sprintf("%016x", checksum),
		"work_ms":    float64(workDuration.Microseconds()) / 1000.0,
		"go_version": runtime.Version(),
		"build":      buildLabel(),
		"hostname":   hostname(),
	})
}

func (s *server) handleProfilePing(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), s.redis.timeout)
	defer cancel()
	reply, err := s.redis.ping(ctx)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"ok": false, "error": err.Error(), "redis_addr": s.redis.addr})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "reply": reply, "redis_addr": s.redis.addr})
}

func (s *server) handleProfileCapture(w http.ResponseWriter, r *http.Request) {
	seconds := queryInt(r, "seconds", 20)
	if seconds < 1 {
		seconds = 1
	}
	if seconds > 120 {
		seconds = 120
	}

	key := r.URL.Query().Get("key")
	if key == "" {
		key = fmt.Sprintf("%s:%s:%d.pprof", envDefault("PROFILE_KEY_PREFIX", "go-pgo"), hostname(), time.Now().UnixNano())
	}

	if !s.profileLock.TryLock() {
		writeJSON(w, http.StatusConflict, map[string]any{"error": "a profile capture is already running"})
		return
	}
	defer s.profileLock.Unlock()

	var buf bytes.Buffer
	if err := pprof.StartCPUProfile(&buf); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	time.Sleep(time.Duration(seconds) * time.Second)
	pprof.StopCPUProfile()

	ctx, cancel := context.WithTimeout(r.Context(), s.redis.timeout)
	defer cancel()
	if err := s.redis.set(ctx, key, buf.Bytes()); err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error(), "key": key, "bytes": buf.Len()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"key":     key,
		"bytes":   buf.Len(),
		"seconds": seconds,
		"build":   buildLabel(),
	})
}

func (s *server) handleProfileFetch(w http.ResponseWriter, r *http.Request) {
	key := r.URL.Query().Get("key")
	if key == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "missing key"})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), s.redis.timeout)
	defer cancel()
	value, err := s.redis.get(ctx, key)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error(), "key": key})
		return
	}
	if value == nil {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "key not found", "key": key})
		return
	}

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("X-Redis-Key", key)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(value)
}

func (s *server) handleProfilePut(w http.ResponseWriter, r *http.Request) {
	key := r.URL.Query().Get("key")
	if key == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "missing key"})
		return
	}
	defer r.Body.Close()
	body, err := io.ReadAll(io.LimitReader(r.Body, 128<<20))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error(), "key": key})
		return
	}
	if len(body) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "empty body", "key": key})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), s.redis.timeout)
	defer cancel()
	if err := s.redis.set(ctx, key, body); err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error(), "key": key, "bytes": len(body)})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"key": key, "bytes": len(body)})
}

func (c redisClient) ping(ctx context.Context) (string, error) {
	value, err := c.do(ctx, [][]byte{[]byte("PING")})
	if err != nil {
		return "", err
	}
	switch v := value.(type) {
	case string:
		return v, nil
	case []byte:
		return string(v), nil
	default:
		return fmt.Sprintf("%v", v), nil
	}
}

func (c redisClient) set(ctx context.Context, key string, value []byte) error {
	reply, err := c.do(ctx, [][]byte{[]byte("SET"), []byte(key), value})
	if err != nil {
		return err
	}
	if fmt.Sprintf("%v", reply) != "OK" {
		return fmt.Errorf("unexpected SET reply: %v", reply)
	}
	return nil
}

func (c redisClient) get(ctx context.Context, key string) ([]byte, error) {
	reply, err := c.do(ctx, [][]byte{[]byte("GET"), []byte(key)})
	if err != nil {
		return nil, err
	}
	if reply == nil {
		return nil, nil
	}
	value, ok := reply.([]byte)
	if !ok {
		return nil, fmt.Errorf("unexpected GET reply type %T", reply)
	}
	return value, nil
}

func (c redisClient) do(ctx context.Context, args [][]byte) (any, error) {
	addr := c.addr
	if addr == "" {
		return nil, errors.New("REDIS_ADDR is empty")
	}

	dialer := net.Dialer{Timeout: c.timeout}
	conn, err := dialer.DialContext(ctx, "tcp", addr)
	if err != nil {
		return nil, err
	}
	defer conn.Close()

	deadline, ok := ctx.Deadline()
	if !ok {
		deadline = time.Now().Add(c.timeout)
	}
	_ = conn.SetDeadline(deadline)

	reader := bufio.NewReader(conn)
	if c.password != "" {
		if _, err := c.writeRead(conn, reader, [][]byte{[]byte("AUTH"), []byte(c.password)}); err != nil {
			return nil, err
		}
	}
	if c.db > 0 {
		if _, err := c.writeRead(conn, reader, [][]byte{[]byte("SELECT"), []byte(strconv.Itoa(c.db))}); err != nil {
			return nil, err
		}
	}
	return c.writeRead(conn, reader, args)
}

func (c redisClient) writeRead(conn net.Conn, reader *bufio.Reader, args [][]byte) (any, error) {
	if err := writeRESP(conn, args); err != nil {
		return nil, err
	}
	return readRESP(reader)
}

func writeRESP(w io.Writer, args [][]byte) error {
	if _, err := fmt.Fprintf(w, "*%d\r\n", len(args)); err != nil {
		return err
	}
	for _, arg := range args {
		if _, err := fmt.Fprintf(w, "$%d\r\n", len(arg)); err != nil {
			return err
		}
		if _, err := w.Write(arg); err != nil {
			return err
		}
		if _, err := io.WriteString(w, "\r\n"); err != nil {
			return err
		}
	}
	return nil
}

func readRESP(r *bufio.Reader) (any, error) {
	prefix, err := r.ReadByte()
	if err != nil {
		return nil, err
	}
	line, err := r.ReadString('\n')
	if err != nil {
		return nil, err
	}
	line = strings.TrimSuffix(strings.TrimSuffix(line, "\n"), "\r")

	switch prefix {
	case '+':
		return line, nil
	case '-':
		return nil, errors.New(line)
	case ':':
		return strconv.ParseInt(line, 10, 64)
	case '$':
		n, err := strconv.Atoi(line)
		if err != nil {
			return nil, err
		}
		if n < 0 {
			return nil, nil
		}
		buf := make([]byte, n+2)
		if _, err := io.ReadFull(r, buf); err != nil {
			return nil, err
		}
		return buf[:n], nil
	case '*':
		n, err := strconv.Atoi(line)
		if err != nil {
			return nil, err
		}
		if n < 0 {
			return nil, nil
		}
		items := make([]any, 0, n)
		for i := 0; i < n; i++ {
			item, err := readRESP(r)
			if err != nil {
				return nil, err
			}
			items = append(items, item)
		}
		return items, nil
	default:
		return nil, fmt.Errorf("unexpected RESP prefix %q", prefix)
	}
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

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func queryInt(r *http.Request, name string, fallback int) int {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return fallback
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return value
}

func queryUint64(r *http.Request, name string, fallback uint64) uint64 {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return fallback
	}
	value, err := strconv.ParseUint(raw, 10, 64)
	if err != nil {
		return fallback
	}
	return value
}

func queryString(r *http.Request, name string, fallback string) string {
	raw := r.URL.Query().Get(name)
	if raw == "" {
		return fallback
	}
	return raw
}

func envDefault(name string, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func envInt(name string, fallback int) int {
	if value := os.Getenv(name); value != "" {
		parsed, err := strconv.Atoi(value)
		if err == nil {
			return parsed
		}
	}
	return fallback
}

func envDuration(name string, fallback time.Duration) time.Duration {
	if value := os.Getenv(name); value != "" {
		if parsed, err := time.ParseDuration(value); err == nil {
			return parsed
		}
		if seconds, err := strconv.Atoi(value); err == nil {
			return time.Duration(seconds) * time.Second
		}
	}
	return fallback
}

func buildLabel() string {
	return envDefault("BUILD_LABEL", "nopgo")
}

func hostname() string {
	name, err := os.Hostname()
	if err != nil || name == "" {
		return "unknown"
	}
	return name
}
