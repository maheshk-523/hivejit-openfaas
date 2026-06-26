import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.HttpExchange;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class S {
  static byte[] slurp(InputStream in) throws Exception {
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    byte[] buf = new byte[8192];
    int n;
    while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
    return out.toByteArray();
  }

  static void send(HttpExchange e, int code, byte[] body) throws Exception {
    e.getResponseHeaders().set("Content-Type", "text/plain; charset=utf-8");
    e.sendResponseHeaders(code, body.length);
    try (OutputStream os = e.getResponseBody()) {
      os.write(body);
    }
  }

  public static void main(String[] args) throws Exception {
    HttpServer s = HttpServer.create(new InetSocketAddress(8080), 0);

    s.createContext("/_/health", e -> {
      try {
        send(e, 200, "OK".getBytes(StandardCharsets.UTF_8));
      } catch (Exception ex) {
        throw new RuntimeException(ex);
      }
    });

    s.createContext("/", e -> {
      try {
        long t0 = System.nanoTime();
        Map<String, String> env = System.getenv();
        String javaBin = env.getOrDefault("DACAPO_JAVA", "/opt/george-jdk/bin/java");
        String jar = env.getOrDefault("DACAPO_JAR", "/app/lib/dacapo.jar");
        String benchmark = env.getOrDefault("DACAPO_BENCHMARK", "lusearch");
        String iterations = env.getOrDefault("DACAPO_ITERATIONS", "1");
        String threads = env.getOrDefault("DACAPO_THREADS", "");
        String size = env.getOrDefault("DACAPO_SIZE", "");
        String scratch = env.getOrDefault("DACAPO_SCRATCH", "/tmp/dacapo-scratch");
        String logDir = env.getOrDefault("DACAPO_LOG_DIR", "/tmp/dacapo-log");

        List<String> cmd = new ArrayList<>();
        cmd.add(javaBin);
        cmd.add("-jar");
        cmd.add(jar);
        cmd.add("--scratch-directory");
        cmd.add(scratch + "-" + System.nanoTime());
        cmd.add("--log-directory");
        cmd.add(logDir);
        if (!threads.isBlank()) {
          cmd.add("-t");
          cmd.add(threads);
        }
        if (!size.isBlank()) {
          cmd.add("-s");
          cmd.add(size);
        }
        cmd.add(benchmark);
        cmd.add("-n");
        cmd.add(iterations);
        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.redirectErrorStream(true);
        Process p = pb.start();
        byte[] out = slurp(p.getInputStream());
        int rc = p.waitFor();
        long ms = (System.nanoTime() - t0) / 1_000_000L;
        String body = "exit=" + rc + "\nelapsed_ms=" + ms + "\n" + new String(out, StandardCharsets.UTF_8);
        send(e, rc == 0 ? 200 : 500, body.getBytes(StandardCharsets.UTF_8));
      } catch (Exception ex) {
        String body = ex.toString() + "\n";
        try {
          send(e, 500, body.getBytes(StandardCharsets.UTF_8));
        } catch (Exception ex2) {
          throw new RuntimeException(ex2);
        }
      }
    });

    s.setExecutor(null);
    System.out.println("SERVER_READY port=8080");
    s.start();
  }
}
