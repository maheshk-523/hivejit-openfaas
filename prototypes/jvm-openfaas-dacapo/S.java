import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import org.dacapo.harness.TestHarness;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.OutputStream;
import java.io.PrintStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileSystem;
import java.nio.file.FileSystems;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;

public class S {
  private static final Object RUN_LOCK = new Object();
  private static final AtomicLong REQUESTS = new AtomicLong(0);
  private static final long START_NANOS = System.nanoTime();
  private static final Set<String> DEFAULT_BENCHMARKS = Set.of("h2", "lusearch", "eclipse", "fop", "jython");

  public static void main(String[] args) throws Exception {
    int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
    HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
    server.createContext("/_/health", S::health);
    server.createContext("/benchmarks", S::benchmarks);
    server.createContext("/run", S::run);
    server.createContext("/", S::run);
    server.setExecutor(Executors.newFixedThreadPool(1));
    System.out.println("SERVER_READY port=" + port + " build=" + env("BUILD_LABEL", "local"));
    server.start();
  }

  private static void health(HttpExchange exchange) {
    send(exchange, 200, "OK\n", "text/plain; charset=utf-8");
  }

  private static void benchmarks(HttpExchange exchange) {
    send(exchange, 200, "{\"benchmarks\":[\"h2\",\"lusearch\",\"eclipse\",\"fop\",\"jython\"]}\n", "application/json");
  }

  private static void run(HttpExchange exchange) {
    long requestNumber = REQUESTS.incrementAndGet();
    long started = System.nanoTime();
    Map<String, String> query = queryParams(exchange.getRequestURI().getRawQuery());
    DacapoRequest request;
    try {
      request = DacapoRequest.from(query);
      String output = runDacapo(request);
      long elapsedMs = elapsedMillis(started);
      String body = "{"
          + jsonField("status", "ok") + ","
          + jsonField("benchmark", request.benchmark) + ","
          + jsonField("size", request.size) + ","
          + jsonField("iterations", request.iterations) + ","
          + jsonField("threads", request.threads) + ","
          + jsonNumberField("request_in_pod", requestNumber) + ","
          + jsonNumberField("elapsed_ms", elapsedMs) + ","
          + jsonNumberField("process_uptime_ms", elapsedMillis(START_NANOS)) + ","
          + jsonField("pod_uid", env("POD_UID", "")) + ","
          + jsonField("build", env("BUILD_LABEL", "local")) + ","
          + jsonField("cmd", String.join(" ", request.args)) + ","
          + jsonField("output_tail", tail(output, request.outputTailBytes))
          + "}\n";
      send(exchange, 200, body, "application/json");
    } catch (Throwable t) {
      long elapsedMs = elapsedMillis(started);
      String body = "{"
          + jsonField("status", "error") + ","
          + jsonNumberField("request_in_pod", requestNumber) + ","
          + jsonNumberField("elapsed_ms", elapsedMs) + ","
          + jsonNumberField("process_uptime_ms", elapsedMillis(START_NANOS)) + ","
          + jsonField("error", t.toString())
          + "}\n";
      send(exchange, 500, body, "application/json");
    }
  }

  @SuppressWarnings("removal")
  private static String runDacapo(DacapoRequest request) throws Exception {
    synchronized (RUN_LOCK) {
      ByteArrayOutputStream capture = new ByteArrayOutputStream();
      PrintStream oldOut = System.out;
      PrintStream oldErr = System.err;
      SecurityManager oldSecurityManager = System.getSecurityManager();
      Integer exitCode = null;
      try (PrintStream out = new PrintStream(capture, true, StandardCharsets.UTF_8)) {
        System.setOut(out);
        System.setErr(out);
        System.setSecurityManager(new NoExitSecurityManager(oldSecurityManager));
        try {
          TestHarness.main(request.args.toArray(new String[0]));
        } catch (ExitTrappedException exit) {
          exitCode = exit.status;
        }
      } finally {
        closeDacapoZipFileSystem();
        System.setSecurityManager(oldSecurityManager);
        System.setOut(oldOut);
        System.setErr(oldErr);
      }
      String output = capture.toString(StandardCharsets.UTF_8);
      if (exitCode != null && exitCode != 0) {
        throw new DacapoExitException(exitCode, output);
      }
      return output;
    }
  }

  private static void closeDacapoZipFileSystem() {
    try {
      URI uri = TestHarness.class.getClassLoader().getResource("META-INF/cnf").toURI();
      String scheme = uri.getScheme();
      if (!"jar".equals(scheme) && !"resource".equals(scheme)) {
        return;
      }
      FileSystem fs = FileSystems.getFileSystem(uri);
      fs.close();
    } catch (Exception ignored) {
      // DaCapo opens the jar as a zip filesystem while parsing benchmark names.
      // Closing it here makes repeated in-process HTTP invocations possible.
    }
  }

  private static void send(HttpExchange exchange, int code, String body, String contentType) {
    try {
      byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
      exchange.getResponseHeaders().set("Content-Type", contentType);
      exchange.sendResponseHeaders(code, bytes.length);
      try (OutputStream output = exchange.getResponseBody()) {
        output.write(bytes);
      }
    } catch (Exception e) {
      throw new RuntimeException(e);
    }
  }

  private static Map<String, String> queryParams(String rawQuery) {
    Map<String, String> params = new HashMap<>();
    if (rawQuery == null || rawQuery.isBlank()) {
      return params;
    }
    for (String pair : rawQuery.split("&")) {
      if (pair.isBlank()) {
        continue;
      }
      String[] parts = pair.split("=", 2);
      String key = URLDecoder.decode(parts[0], StandardCharsets.UTF_8);
      String value = parts.length == 2 ? URLDecoder.decode(parts[1], StandardCharsets.UTF_8) : "";
      params.put(key, value);
    }
    return params;
  }

  private static String option(Map<String, String> query, String queryKey, String envKey, String fallback) {
    String value = query.get(queryKey);
    if (value != null && !value.isBlank()) {
      return value;
    }
    return env(envKey, fallback);
  }

  private static String env(String key, String fallback) {
    String value = System.getenv(key);
    return value == null || value.isBlank() ? fallback : value;
  }

  private static long elapsedMillis(long startedNanos) {
    return (System.nanoTime() - startedNanos) / 1_000_000L;
  }

  private static String tail(String value, int bytes) {
    if (value.length() <= bytes) {
      return value;
    }
    return value.substring(value.length() - bytes);
  }

  private static String jsonField(String key, String value) {
    return "\"" + escapeJson(key) + "\":\"" + escapeJson(value) + "\"";
  }

  private static String jsonNumberField(String key, long value) {
    return "\"" + escapeJson(key) + "\":" + value;
  }

  private static String escapeJson(String value) {
    StringBuilder out = new StringBuilder(value.length() + 16);
    for (int i = 0; i < value.length(); i++) {
      char c = value.charAt(i);
      switch (c) {
        case '\\':
          out.append("\\\\");
          break;
        case '"':
          out.append("\\\"");
          break;
        case '\n':
          out.append("\\n");
          break;
        case '\r':
          out.append("\\r");
          break;
        case '\t':
          out.append("\\t");
          break;
        default:
          if (c < 0x20) {
            out.append(String.format("\\u%04x", (int) c));
          } else {
            out.append(c);
          }
      }
    }
    return out.toString();
  }

  private static final class DacapoRequest {
    final String benchmark;
    final String size;
    final String iterations;
    final String threads;
    final int outputTailBytes;
    final List<String> args;

    private DacapoRequest(String benchmark, String size, String iterations, String threads, int outputTailBytes, List<String> args) {
      this.benchmark = benchmark;
      this.size = size;
      this.iterations = iterations;
      this.threads = threads;
      this.outputTailBytes = outputTailBytes;
      this.args = args;
    }

    static DacapoRequest from(Map<String, String> query) {
      Set<String> allowed = new HashSet<>(DEFAULT_BENCHMARKS);
      String configured = env("DACAPO_ALLOWED_BENCHMARKS", "");
      if (!configured.isBlank()) {
        allowed.clear();
        for (String item : Arrays.asList(configured.split(","))) {
          String trimmed = item.trim();
          if (!trimmed.isBlank()) {
            allowed.add(trimmed);
          }
        }
      }

      String benchmark = option(query, "benchmark", "DACAPO_BENCHMARK", "lusearch");
      if (!allowed.contains(benchmark)) {
        throw new IllegalArgumentException("unsupported benchmark: " + benchmark);
      }
      String size = option(query, "size", "DACAPO_SIZE", "small");
      String iterations = option(query, "iterations", "DACAPO_ITERATIONS", "1");
      String threads = option(query, "threads", "DACAPO_THREADS", "1");
      String scratchRoot = option(query, "scratch", "DACAPO_SCRATCH", "/tmp/dacapo-scratch");
      String logDir = option(query, "log_dir", "DACAPO_LOG_DIR", "/tmp/dacapo-log");
      String validation = option(query, "validation", "DACAPO_VALIDATION", "none");
      boolean preGc = Boolean.parseBoolean(option(query, "pre_gc", "DACAPO_PRE_GC", "true"));
      boolean digestOutput = Boolean.parseBoolean(option(query, "digest_output", "DACAPO_DIGEST_OUTPUT", "false"));
      int outputTailBytes = Integer.parseInt(option(query, "output_tail_bytes", "DACAPO_OUTPUT_TAIL_BYTES", "4096"));

      String scratch = scratchRoot + "/" + benchmark + "-" + Instant.now().toEpochMilli() + "-" + System.nanoTime();
      new File(scratchRoot).mkdirs();
      new File(logDir).mkdirs();
      List<String> args = new ArrayList<>();
      args.add("--scratch-directory");
      args.add(scratch);
      args.add("--log-directory");
      args.add(logDir);
      if (!preGc) {
        args.add("--no-pre-iteration-gc");
      }
      if (!digestOutput) {
        args.add("--no-digest-output");
      }
      if ("ignore".equals(validation)) {
        args.add("--ignore-validation");
      } else if ("none".equals(validation) || "false".equals(validation)) {
        args.add("--no-validation");
      }
      if (!threads.isBlank() && !threads.equals("auto")) {
        args.add("-t");
        args.add(threads);
      }
      if (!size.isBlank()) {
        args.add("-s");
        args.add(size);
      }
      args.add(benchmark);
      args.add("-n");
      args.add(iterations);
      return new DacapoRequest(benchmark, size, iterations, threads, outputTailBytes, args);
    }
  }

  @SuppressWarnings("removal")
  private static final class NoExitSecurityManager extends SecurityManager {
    private final SecurityManager delegate;

    private NoExitSecurityManager(SecurityManager delegate) {
      this.delegate = delegate;
    }

    @Override
    public void checkPermission(java.security.Permission permission) {
      if (delegate != null) {
        delegate.checkPermission(permission);
      }
    }

    @Override
    public void checkPermission(java.security.Permission permission, Object context) {
      if (delegate != null) {
        delegate.checkPermission(permission, context);
      }
    }

    @Override
    public void checkExit(int status) {
      throw new ExitTrappedException(status);
    }
  }

  private static final class ExitTrappedException extends SecurityException {
    final int status;

    private ExitTrappedException(int status) {
      super("intercepted System.exit(" + status + ")");
      this.status = status;
    }
  }

  private static final class DacapoExitException extends Exception {
    private DacapoExitException(int status, String output) {
      super("DaCapo harness exited with status " + status + ": " + tail(output, 2048));
    }
  }
}
