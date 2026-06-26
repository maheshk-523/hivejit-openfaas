package com.example;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;

public class ServerMain {
    private static final AtomicLong REQ = new AtomicLong(0);

    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/", ServerMain::handle);
        server.setExecutor(Executors.newFixedThreadPool(1));
        server.start();
        System.out.println("SERVER_READY port=" + port);
    }

    private static void handle(HttpExchange exchange) throws IOException {
        long n = REQ.incrementAndGet();
        long t0 = System.nanoTime();

        String status = "ok";
        String error = "";

        try {
            runLusearch();
        } catch (Throwable t) {
            status = "error";
            error = t.toString().replace("\"", "'");
        }

        long t1 = System.nanoTime();
        double latencyMs = (t1 - t0) / 1_000_000.0;

        String body = "{"
                + "\"status\":\"" + status + "\","
                + "\"request_in_pod\":" + n + ","
                + "\"latency_ms\":" + latencyMs + ","
                + "\"error\":\"" + error + "\""
                + "}";

        exchange.getResponseHeaders().add("Content-Type", "application/json");
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(status.equals("ok") ? 200 : 500, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }

    private static void runLusearch() throws Exception {
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
        pb.environment().put("JAVA_HOME", "/opt/george-jdk");
        pb.redirectErrorStream(true);

        Process p = pb.start();

        try (InputStream is = p.getInputStream()) {
            is.transferTo(OutputStream.nullOutputStream());
        }

        int rc = p.waitFor();
        if (rc != 0) {
            throw new RuntimeException("lusearch failed with exit code " + rc);
        }
    }
}
