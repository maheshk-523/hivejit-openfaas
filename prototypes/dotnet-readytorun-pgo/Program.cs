using System.Diagnostics;
using System.Text.Json;

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

    public static Result Run(string scenario, int invocations, ulong iterations)
    {
        scenario = CanonicalScenario(scenario);
        List<double> invocationTimes = new(invocations);
        ulong checksum = 0UL;
        Stopwatch total = Stopwatch.StartNew();

        for (int i = 0; i < invocations; i++)
        {
            Stopwatch one = Stopwatch.StartNew();
            ulong seed = 0x123456789abcdef0UL + ((ulong)i * 0x9e3779b97f4a7c15UL);
            checksum ^= InvokeHandler(scenario, iterations, seed);
            one.Stop();
            invocationTimes.Add(one.Elapsed.TotalMilliseconds);
        }

        total.Stop();
        return new Result(
            Domain: "dotnet-readytorun-pgo",
            Scenario: scenario,
            GeneratedAt: DateTimeOffset.UtcNow,
            Runtime: Environment.Version.ToString(),
            OSArchitecture: System.Runtime.InteropServices.RuntimeInformation.OSArchitecture.ToString(),
            Invocations: invocations,
            IterationsPerInvoke: iterations,
            ElapsedMs: total.Elapsed.TotalMilliseconds,
            PerInvocationNs: total.Elapsed.TotalMilliseconds * 1_000_000.0 / invocations / iterations,
            InvocationTimesMs: invocationTimes,
            InvocationP50Ms: Percentile(invocationTimes, 50),
            InvocationP95Ms: Percentile(invocationTimes, 95),
            Checksum: checksum);
    }

    private static double Percentile(List<double> values, int percentile)
    {
        if (values.Count == 0)
        {
            return 0;
        }

        values.Sort();
        int index = (int)Math.Floor(percentile / 100.0 * values.Count);
        if (index >= values.Count)
        {
            index = values.Count - 1;
        }
        return values[index];
    }
}

static class Args
{
    public static string Get(string[] args, string name, string fallback)
    {
        string prefix = name + "=";
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i].StartsWith(prefix, StringComparison.Ordinal))
            {
                return args[i][prefix.Length..];
            }
            if (args[i] == name && i + 1 < args.Length)
            {
                return args[i + 1];
            }
        }
        return fallback;
    }

    public static bool Has(string[] args, string name)
    {
        return args.Any(arg => arg == name);
    }
}

static class Program
{
    public static int Main(string[] args)
    {
        string scenario = Args.Get(args, "--scenario", "serve-hot");
        int invocations = int.Parse(Args.Get(args, "--invocations", "4"));
        ulong iterations = ulong.Parse(Args.Get(args, "--iterations", "250000"));
        bool asJson = Args.Has(args, "--json");

        if (invocations <= 0 || iterations == 0)
        {
            Console.Error.WriteLine("--invocations and --iterations must be positive");
            return 2;
        }

        Result result = Workload.Run(scenario, invocations, iterations);

        if (asJson)
        {
            Console.WriteLine(JsonSerializer.Serialize(result));
        }
        else
        {
            Console.WriteLine(
                $"scenario={result.Scenario} invocations={result.Invocations} iterations={result.IterationsPerInvoke} " +
                $"checksum={result.Checksum} elapsed_ms={result.ElapsedMs:F3} per_invocation_ns={result.PerInvocationNs:F2} " +
                $"p50_ms={result.InvocationP50Ms:F3} p95_ms={result.InvocationP95Ms:F3}");
        }

        return 0;
    }
}
