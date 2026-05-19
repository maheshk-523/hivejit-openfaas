using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Http.Json;

var builder = WebApplication.CreateSlimBuilder(args);
builder.WebHost.UseUrls(Environment.GetEnvironmentVariable("HANDLER_ADDR") ?? "http://0.0.0.0:8082");
builder.Services.ConfigureHttpJsonOptions(options =>
{
    options.SerializerOptions.TypeInfoResolverChain.Insert(0, AppJsonSerializerContext.Default);
    options.SerializerOptions.PropertyNameCaseInsensitive = true;
    options.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.CamelCase;
});

var app = builder.Build();
app.MapGet("/healthz", () => Results.Json(
    new HealthResponse(true, BuildLabel()),
    AppJsonSerializerContext.Default.HealthResponse));
app.MapPost("/", (WorkRequest request) => RunWork(request));
app.MapPost("/work", (WorkRequest request) => RunWork(request));
app.MapGet("/", (HttpRequest request) => RunWork(WorkRequest.FromQuery(request)));
app.MapGet("/work", (HttpRequest request) => RunWork(WorkRequest.FromQuery(request)));
app.Run();

static IResult RunWork(WorkRequest request)
{
    long requestInPod = RuntimeState.NextRequestNumber();
    string scenario = string.IsNullOrWhiteSpace(request.Scenario) ? "serve-hot" : request.Scenario;
    int invocations = request.Invocations.GetValueOrDefault(1);
    ulong iterations = request.Iterations ?? request.Requests ?? 250_000UL;
    ulong seed = request.Seed ?? (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

    if (invocations <= 0 || iterations == 0)
    {
        return Results.Json(
            new ErrorResponse("invocations and iterations must be positive"),
            AppJsonSerializerContext.Default.ErrorResponse,
            statusCode: StatusCodes.Status400BadRequest);
    }

    Result result = Workload.Run(scenario, invocations, iterations, seed);
    return Results.Json(
        new WorkResponse(
            result.Scenario,
            result.Invocations,
            result.IterationsPerInvoke,
            result.ElapsedMs,
            result.InvocationP50Ms,
            result.InvocationP95Ms,
            result.Checksum.ToString("x16"),
            result.Runtime,
            result.OSArchitecture,
            BuildLabel(),
            Environment.MachineName,
            Environment.GetEnvironmentVariable("POD_UID") ?? Environment.MachineName,
            RuntimeState.ProcessUptimeMs(),
            requestInPod,
            Environment.ProcessId),
        AppJsonSerializerContext.Default.WorkResponse);
}

static string BuildLabel()
{
    string path = "/app/build-label";
    if (File.Exists(path))
    {
        return File.ReadAllText(path).Trim();
    }
    return Environment.GetEnvironmentVariable("BUILD_LABEL") ?? "unknown";
}

sealed record WorkRequest(string? Scenario, int? Invocations, ulong? Iterations, ulong? Requests, ulong? Seed)
{
    public static WorkRequest FromQuery(HttpRequest request)
    {
        IQueryCollection query = request.Query;
        return new WorkRequest(
            Scenario: query.TryGetValue("scenario", out var scenario) ? scenario.ToString() : null,
            Invocations: TryInt(query, "invocations"),
            Iterations: TryUlong(query, "iterations"),
            Requests: TryUlong(query, "requests"),
            Seed: TryUlong(query, "seed"));
    }

    private static int? TryInt(IQueryCollection query, string key)
    {
        return query.TryGetValue(key, out var value) && int.TryParse(value, out int parsed) ? parsed : null;
    }

    private static ulong? TryUlong(IQueryCollection query, string key)
    {
        return query.TryGetValue(key, out var value) && ulong.TryParse(value, out ulong parsed) ? parsed : null;
    }
}

sealed record HealthResponse(bool Ok, string Build);

sealed record ErrorResponse(string Error);

sealed record WorkResponse(
    string Scenario,
    int Invocations,
    ulong IterationsPerInvoke,
    double ElapsedMs,
    double P50Ms,
    double P95Ms,
    string Checksum,
    string Runtime,
    string OSArchitecture,
    string Build,
    string Hostname,
    string PodUid,
    double ProcessUptimeMs,
    long RequestInPod,
    int ProcessId);

[JsonSourceGenerationOptions(PropertyNamingPolicy = JsonKnownNamingPolicy.CamelCase)]
[JsonSerializable(typeof(WorkRequest))]
[JsonSerializable(typeof(HealthResponse))]
[JsonSerializable(typeof(ErrorResponse))]
[JsonSerializable(typeof(WorkResponse))]
internal sealed partial class AppJsonSerializerContext : JsonSerializerContext;

static class RuntimeState
{
    private static readonly Stopwatch Uptime = Stopwatch.StartNew();
    private static long RequestCount;

    public static long NextRequestNumber()
    {
        return Interlocked.Increment(ref RequestCount);
    }

    public static double ProcessUptimeMs()
    {
        return Uptime.Elapsed.TotalMilliseconds;
    }
}

interface IRoute
{
    ulong Run(ulong value);
}

sealed class HotRoute : IRoute
{
    public ulong Run(ulong value)
    {
        unchecked
        {
            for (ulong i = 0; i < 9; i++)
            {
                value = Workload.Mix64(value + i * 0x9e3779b97f4a7c15UL);
            }
            return value;
        }
    }
}

sealed class ParseRoute : IRoute
{
    public ulong Run(ulong value)
    {
        unchecked
        {
            for (ulong i = 0; i < 15; i++)
            {
                value = (value << 7) ^ (value >> 3) ^ Workload.Mix64(value + i);
            }
            return value;
        }
    }
}

sealed class RegexRoute : IRoute
{
    public ulong Run(ulong value)
    {
        unchecked
        {
            for (ulong i = 0; i < 19; i++)
            {
                value ^= (value & 1UL) == 0 ? Workload.Mix64(value + 31) : Workload.Mix64(value + 17);
            }
            return value;
        }
    }
}

sealed class GraphRoute : IRoute
{
    public ulong Run(ulong value)
    {
        unchecked
        {
            for (ulong i = 0; i < 23; i++)
            {
                value += Workload.Mix64(value ^ (i * 0x100000001b3UL));
            }
            return value;
        }
    }
}

sealed class InterpreterRoute : IRoute
{
    public ulong Run(ulong value)
    {
        unchecked
        {
            Span<ulong> stack = stackalloc ulong[8];
            int sp = 0;
            for (ulong i = 0; i < 17; i++)
            {
                stack[sp++ & 7] = Workload.Mix64(value + i);
                ulong rhs = stack[(sp - 1) & 7];
                ulong lhs = stack[(sp - 2) & 7];
                value ^= (lhs + rhs + i) * 0x100000001b3UL;
            }
            return value;
        }
    }
}

sealed class CallSiteRoute : IRoute
{
    public ulong Run(ulong value)
    {
        unchecked
        {
            for (ulong i = 0; i < 21; i++)
            {
                value = (i & 1UL) == 0
                    ? Workload.Mix64(value ^ (i + 0x51ed2705UL))
                    : Workload.Mix64(value + (i * 0x9e3779b97f4a7c15UL));
            }
            return value;
        }
    }
}

sealed class XmlRoute : IRoute
{
    public ulong Run(ulong value)
    {
        unchecked
        {
            ulong depth = 0;
            for (ulong i = 0; i < 25; i++)
            {
                depth += ((value >> (int)(i & 15)) & 1UL) == 0 ? 1UL : ulong.MaxValue;
                value ^= Workload.Mix64(value + depth + i);
            }
            return value;
        }
    }
}

sealed class LayoutRoute : IRoute
{
    public ulong Run(ulong value)
    {
        unchecked
        {
            ulong page = 1;
            ulong cursor = 0;
            for (ulong i = 0; i < 29; i++)
            {
                ulong height = 8 + (Workload.Mix64(value + i) % 64);
                if (cursor + height > 720)
                {
                    page++;
                    cursor = 0;
                }
                cursor += height;
                value ^= Workload.Mix64(page + cursor + i);
            }
            return value;
        }
    }
}

sealed class RenderRoute : IRoute
{
    public ulong Run(ulong value)
    {
        unchecked
        {
            for (ulong i = 0; i < 31; i++)
            {
                value = Workload.Mix64(value ^ ((i + 1) * 0x45d9f3bUL));
            }
            return value;
        }
    }
}

sealed record Result(
    string Domain,
    string Scenario,
    DateTimeOffset GeneratedAt,
    string Runtime,
    string OSArchitecture,
    int Invocations,
    ulong IterationsPerInvoke,
    double ElapsedMs,
    double PerInvocationNs,
    IReadOnlyList<double> InvocationTimesMs,
    double InvocationP50Ms,
    double InvocationP95Ms,
    ulong Checksum);

static class Workload
{
    private static readonly IRoute[] CommonRoutes =
    [
        new HotRoute(),
        new ParseRoute(),
        new RegexRoute(),
        new GraphRoute(),
    ];

    private static readonly IRoute[] JythonRoutes =
    [
        new InterpreterRoute(),
        new ParseRoute(),
        new CallSiteRoute(),
        new GraphRoute(),
    ];

    private static readonly IRoute[] FopRoutes =
    [
        new XmlRoute(),
        new LayoutRoute(),
        new RenderRoute(),
        new RegexRoute(),
    ];

    public static ulong Mix64(ulong value)
    {
        unchecked
        {
            value ^= value >> 33;
            value *= 0xff51afd7ed558ccdUL;
            value ^= value >> 33;
            value *= 0xc4ceb9fe1a85ec53UL;
            value ^= value >> 33;
            return value;
        }
    }

    public static Result Run(string scenario, int invocations, ulong iterations, ulong seedBase)
    {
        scenario = CanonicalScenario(scenario);
        List<double> invocationTimes = new(invocations);
        ulong checksum = 0UL;
        Stopwatch total = Stopwatch.StartNew();

        for (int i = 0; i < invocations; i++)
        {
            Stopwatch one = Stopwatch.StartNew();
            ulong seed = seedBase + ((ulong)i * 0x9e3779b97f4a7c15UL);
            checksum ^= InvokeHandler(scenario, iterations, seed);
            one.Stop();
            invocationTimes.Add(one.Elapsed.TotalMilliseconds);
        }

        total.Stop();
        return new Result(
            Domain: "dotnet-openfaas-readytorun",
            Scenario: scenario,
            GeneratedAt: DateTimeOffset.UtcNow,
            Runtime: Environment.Version.ToString(),
            OSArchitecture: RuntimeInformation.OSArchitecture.ToString(),
            Invocations: invocations,
            IterationsPerInvoke: iterations,
            ElapsedMs: total.Elapsed.TotalMilliseconds,
            PerInvocationNs: total.Elapsed.TotalMilliseconds * 1_000_000.0 / invocations / iterations,
            InvocationTimesMs: invocationTimes,
            InvocationP50Ms: Percentile(invocationTimes, 50),
            InvocationP95Ms: Percentile(invocationTimes, 95),
            Checksum: checksum);
    }

    private static int ChooseRoute(string scenario, ulong index, ulong state)
    {
        ulong ticket = Mix64(index ^ state) % 100UL;
        return scenario switch
        {
            "train" or "serve-hot" when ticket < 88 => 0,
            "train" or "serve-hot" when ticket < 94 => 1,
            "train" or "serve-hot" when ticket < 98 => 2,
            "train" or "serve-hot" => 3,
            "serve-mixed" when ticket < 45 => 0,
            "serve-mixed" when ticket < 65 => 1,
            "serve-mixed" when ticket < 84 => 2,
            "serve-mixed" => 3,
            "lusearch" when ticket < 56 => 2,
            "lusearch" when ticket < 76 => 1,
            "lusearch" when ticket < 93 => 0,
            "lusearch" => 3,
            "h2" when ticket < 48 => 3,
            "h2" when ticket < 73 => 0,
            "h2" when ticket < 91 => 1,
            "h2" => 2,
            "eclipse" when ticket < 43 => 1,
            "eclipse" when ticket < 71 => 3,
            "eclipse" when ticket < 90 => 2,
            "eclipse" => 0,
            "jython" when ticket < 44 => 0,
            "jython" when ticket < 69 => 2,
            "jython" when ticket < 88 => 1,
            "jython" => 3,
            "fop" when ticket < 45 => 0,
            "fop" when ticket < 76 => 1,
            "fop" when ticket < 92 => 2,
            "fop" => 3,
            _ => (int)(ticket & 3UL),
        };
    }

    private static string CanonicalScenario(string scenario)
    {
        string value = scenario.Trim().ToLowerInvariant();
        if (value.StartsWith("dacapo-", StringComparison.Ordinal))
        {
            value = value["dacapo-".Length..];
        }
        return value switch
        {
            "fopo" => "fop",
            _ => value,
        };
    }

    private static IRoute[] RoutesForScenario(string scenario)
    {
        return scenario switch
        {
            "jython" => JythonRoutes,
            "fop" => FopRoutes,
            _ => CommonRoutes,
        };
    }

    private static ulong InvokeHandler(string scenario, ulong iterations, ulong seed)
    {
        unchecked
        {
            ulong state = seed;
            IRoute[] routes = RoutesForScenario(scenario);
            for (ulong i = 0; i < iterations; i++)
            {
                IRoute route = routes[ChooseRoute(scenario, i, state)];
                state ^= route.Run(state + i);
            }
            return state;
        }
    }

    private static double Percentile(List<double> values, int percentile)
    {
        if (values.Count == 0)
        {
            return 0;
        }

        values.Sort();
        int index = (int)Math.Floor(percentile / 100.0 * values.Count);
        return values[Math.Min(index, values.Count - 1)];
    }
}
