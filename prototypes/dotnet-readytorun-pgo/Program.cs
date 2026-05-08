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
    double InvocationP50Ms,
    double InvocationP95Ms,
    ulong Checksum);

static class Workload
{
    private static readonly IRoute[] Routes =
    [
        new HotRoute(),
        new ParseRoute(),
        new RegexRoute(),
        new GraphRoute(),
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
            _ => (int)(ticket & 3UL),
        };
    }

    private static ulong InvokeHandler(string scenario, ulong iterations, ulong seed)
    {
        unchecked
        {
            ulong state = seed;
            for (ulong i = 0; i < iterations; i++)
            {
                IRoute route = Routes[ChooseRoute(scenario, i, state)];
                state ^= route.Run(state + i);
            }
            return state;
        }
    }

    public static Result Run(string scenario, int invocations, ulong iterations)
    {
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
