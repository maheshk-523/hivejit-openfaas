#!/usr/bin/env julia
"""OpenFaaS HTTP handler for Julia precompile-cache warmup experiments.

DaCapo-analog workloads (pure-Julia, no external data files required):
  lusearch  – inverted-index Boolean search over a generated string corpus
  h2        – in-memory key-value store with CRUD / scan operations
  eclipse   – Julia expression parsing and lightweight AST analysis
  jython    – stack-machine bytecode interpretation over generated programs
  fop       – XML-like document parse, layout, and render checksum
  matrix    – dense linear-algebra (multiply, det, eigen, svd) via LAPACK
  regex     – compile and match complex regex patterns over generated text
  sort      – sort large arrays with mixed types and custom comparators

Optimisation path:
  baseline  – cold pod; Julia LLVM JIT compiles on first call (visible warmup spike)
  populate  – same as baseline but Julia is started with --trace-compile so every
              compiled method specialisation is recorded to JULIA_TRACE_FILE
  redis     – entrypoint pulls the trace file from Redis before starting;
              include() replays all precompile() calls before the HTTP server
              opens, so first-request latency is near-warm-state

Environment variables read at startup:
  JULIA_CACHE_MODE   baseline | populate | redis  (default: baseline)
  JULIA_PRECOMPILE_FILE  path to replay on startup when set and file exists
  HANDLER_PORT       port the HTTP server binds to (default: 8000)
  BUILD_LABEL        arbitrary label echoed in every JSON response
  POD_UID            Kubernetes pod UID injected via downward API
"""

using HTTP
using JSON3
using LinearAlgebra
using Logging
using Random
using Sockets

const STARTED_AT = time()
const REQUEST_COUNT = Ref{Int}(0)
const REQUEST_LOCK  = ReentrantLock()
const BUILD_LABEL   = get(ENV, "BUILD_LABEL",  "unknown")
const POD_UID       = get(ENV, "POD_UID",      "unknown")
const CACHE_MODE    = get(ENV, "JULIA_CACHE_MODE", "baseline")
const TRACE_FILE    = get(ENV, "JULIA_TRACE_FILE", "/tmp/julia-trace.jl")

# ============================================================
# Workload: lusearch – inverted-index Boolean text search
# ============================================================

const _WORDS = [
    "the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog",
    "java", "julia", "python", "compiler", "jit", "llvm", "optimization",
    "profile", "serverless", "container", "cache", "warmup", "latency",
    "throughput", "benchmark", "dacapo", "lucene", "search", "index",
    "inverted", "tokenize", "query", "boolean", "term", "frequency",
    "graal", "hotspot", "tiered", "deopt", "method", "inlining", "escape",
]

function _build_corpus(n_docs::Int)::Vector{String}
    nw = length(_WORDS)
    docs = Vector{String}(undef, n_docs)
    for i in 1:n_docs
        doc_len = 12 + (i % 22)
        parts = [_WORDS[(i * j * 3 + j + 7) % nw + 1] for j in 1:doc_len]
        docs[i] = join(parts, " ")
    end
    docs
end

function _build_inverted_index(docs::Vector{String})::Dict{String,Vector{Int}}
    idx = Dict{String,Vector{Int}}()
    for (doc_id, doc) in enumerate(docs)
        for word in split(doc)
            push!(get!(idx, word, Int[]), doc_id)
        end
    end
    idx
end

function _boolean_and(idx::Dict{String,Vector{Int}}, terms::Vector{String})::Vector{Int}
    isempty(terms) && return Int[]
    result = Set{Int}(get(idx, terms[1], Int[]))
    for term in terms[2:end]
        intersect!(result, Set{Int}(get(idx, term, Int[])))
    end
    sort!(collect(result))
end

function run_lusearch(size::Int)::Dict{String,Any}
    n_docs = size == 1 ? 400 : size == 2 ? 1500 : 4000
    docs   = _build_corpus(n_docs)
    idx    = _build_inverted_index(docs)
    queries = [
        ["the", "compiler"],
        ["jit", "optimization"],
        ["serverless", "cache"],
        ["julia", "profile"],
        ["latency", "benchmark"],
        ["inverted", "index", "search"],
    ]
    total_hits = 0
    for q in queries
        total_hits += length(_boolean_and(idx, q))
    end
    Dict{String,Any}("workload" => "lusearch", "n_docs" => n_docs, "total_hits" => total_hits)
end

# ============================================================
# Workload: h2 – in-memory key-value store
# ============================================================

struct _KVEntry
    key::String
    value::String
    version::Int
end

mutable struct _KVStore
    data::Dict{String,_KVEntry}
    version::Int
    log_len::Int
    _KVStore() = new(Dict{String,_KVEntry}(), 0, 0)
end

function _kv_put!(s::_KVStore, key::String, val::String)
    s.version += 1
    s.data[key] = _KVEntry(key, val, s.version)
    s.log_len   += 1
end

function _kv_get(s::_KVStore, key::String)::Union{_KVEntry,Nothing}
    get(s.data, key, nothing)
end

function _kv_delete!(s::_KVStore, key::String)::Bool
    if haskey(s.data, key)
        delete!(s.data, key)
        s.log_len += 1
        return true
    end
    false
end

function _kv_scan(s::_KVStore, prefix::String)::Vector{_KVEntry}
    [v for v in values(s.data) if startswith(v.key, prefix)]
end

function _kv_range_sum(s::_KVStore, prefix::String)::Int
    sum(length(v.value) for v in values(s.data) if startswith(v.key, prefix); init=0)
end

function run_h2(size::Int)::Dict{String,Any}
    n_ops = size == 1 ? 800 : size == 2 ? 4000 : 12000
    store = _KVStore()
    for i in 1:n_ops
        key = string("k:", lpad(i, 6, '0'))
        val = string("v:", i * 13 % 9973, ":", i % 256)
        _kv_put!(store, key, val)
    end
    hits = 0
    for i in 1:div(n_ops, 10)
        _kv_get(store, string("k:", lpad(i * 10, 6, '0'))) !== nothing && (hits += 1)
    end
    deletes = 0
    for i in 1:div(n_ops, 5)
        i % 4 == 0 && _kv_delete!(store, string("k:", lpad(i, 6, '0'))) && (deletes += 1)
    end
    prefix  = "k:0001"
    scanned = length(_kv_scan(store, prefix))
    rsum    = _kv_range_sum(store, prefix)
    Dict{String,Any}(
        "workload" => "h2", "n_ops" => n_ops, "hits" => hits,
        "deletes" => deletes, "scanned" => scanned, "range_sum" => rsum,
    )
end

# ============================================================
# Workload: eclipse – expression parsing and AST analysis
# ============================================================

function _ast_nodes(ex)::Int
    ex isa Expr || return 1
    1 + sum(_ast_nodes(a) for a in ex.args; init=0)
end

function _ast_depth(ex, d::Int=0)::Int
    ex isa Expr || return d
    isempty(ex.args) && return d
    maximum(_ast_depth(a, d + 1) for a in ex.args)
end

function _count_calls(ex)::Int
    ex isa Expr || return 0
    (ex.head === :call ? 1 : 0) + sum(_count_calls(a) for a in ex.args; init=0)
end

const _TEMPLATES = [
    "x + y * z - w / (x + 1)",
    "sin(x) * cos(y) + tan(z) - atan(w, x)",
    "if x > 0; x^2 + y; elseif x < 0; -x * y; else; zero(x); end",
    "[f(i, j) for i in 1:n, j in 1:m if i != j]",
    "sum(i^2 + j for i in 1:n for j in 1:i)",
    "function g(a::Int, b::Float64=1.0; c::Bool=false)\n  a * b + (c ? 1 : 0)\nend",
    "Dict{String,Vector{Int}}(k => collect(1:v) for (k, v) in pairs(d))",
    "try\n  parse(Int, s)\ncatch e\n  e isa ArgumentError ? 0 : rethrow()\nend",
    "(x::AbstractVector{T} where T<:Real) -> mapreduce(abs2, +, x)",
    "struct Point{T<:Number}; x::T; y::T; end",
]

function run_eclipse(size::Int)::Dict{String,Any}
    n_parse     = size == 1 ? 200 : size == 2 ? 800 : 2500
    parsed      = 0
    total_nodes = 0
    total_depth = 0
    total_calls = 0
    nt          = length(_TEMPLATES)
    for i in 1:n_parse
        src = _TEMPLATES[i % nt + 1]
        try
            ex = Meta.parse(src)
            parsed      += 1
            total_nodes += _ast_nodes(ex)
            total_depth += _ast_depth(ex)
            total_calls += _count_calls(ex)
        catch
        end
    end
    Dict{String,Any}(
        "workload" => "eclipse", "n_parse" => n_parse, "parsed" => parsed,
        "total_nodes" => total_nodes, "total_depth" => total_depth, "total_calls" => total_calls,
    )
end

# ============================================================
# Workload: jython – bytecode parsing and interpreter dispatch
# ============================================================

struct _PyInstr
    op::Symbol
    arg::Int
end

mutable struct _PyFrame
    stack::Vector{Int}
    locals::Dict{Symbol,Int}
    checksum::Int
    _PyFrame() = new(Int[], Dict{Symbol,Int}(), 0)
end

function _build_py_program(n_ops::Int)::Vector{_PyInstr}
    ops = Vector{_PyInstr}(undef, n_ops)
    for i in 1:n_ops
        slot = i % 17
        ops[i] = if i % 11 == 0
            _PyInstr(:store, slot)
        elseif i % 7 == 0
            _PyInstr(:load, slot)
        elseif i % 5 == 0
            _PyInstr(:mul, slot + 3)
        elseif i % 3 == 0
            _PyInstr(:add, slot + 5)
        else
            _PyInstr(:const, i * 13 % 997)
        end
    end
    ops
end

function _exec_py_program!(frame::_PyFrame, program::Vector{_PyInstr}, loops::Int)::Int
    for loop in 1:loops
        empty!(frame.stack)
        frame.locals[:seed] = loop
        for instr in program
            if instr.op === :const
                push!(frame.stack, instr.arg)
            elseif instr.op === :load
                push!(frame.stack, get(frame.locals, Symbol("v", instr.arg), instr.arg))
            elseif instr.op === :store
                val = isempty(frame.stack) ? instr.arg : pop!(frame.stack)
                frame.locals[Symbol("v", instr.arg)] = val ⊻ loop
            elseif instr.op === :add
                lhs = isempty(frame.stack) ? loop : pop!(frame.stack)
                rhs = isempty(frame.stack) ? instr.arg : pop!(frame.stack)
                push!(frame.stack, lhs + rhs + instr.arg)
            elseif instr.op === :mul
                lhs = isempty(frame.stack) ? 1 : pop!(frame.stack)
                rhs = isempty(frame.stack) ? instr.arg : pop!(frame.stack)
                push!(frame.stack, (lhs * rhs + loop) % 1_000_003)
            end
        end
        frame.checksum ⊻= sum(frame.stack; init=0) + length(frame.locals)
    end
    frame.checksum
end

function run_jython(size::Int)::Dict{String,Any}
    n_ops = size == 1 ? 700 : size == 2 ? 2400 : 7000
    loops = size == 1 ? 12 : size == 2 ? 24 : 40
    program = _build_py_program(n_ops)
    frame = _PyFrame()
    checksum = _exec_py_program!(frame, program, loops)
    Dict{String,Any}(
        "workload" => "jython", "n_ops" => n_ops, "loops" => loops,
        "locals" => length(frame.locals), "checksum" => checksum,
    )
end

# ============================================================
# Workload: fop – XML-like parse, layout, and render checksum
# ============================================================

struct _FopBlock
    kind::Symbol
    width::Int
    height::Int
    text::String
end

function _generate_fop_doc(n_blocks::Int)::String
    io = IOBuffer()
    println(io, "<root>")
    kinds = (:para, :title, :table, :list, :code)
    for i in 1:n_blocks
        kind = kinds[i % length(kinds) + 1]
        width = 32 + (i * 7 % 96)
        height = 8 + (i * 11 % 44)
        text = "block $(i) $(kind) layout render profile cache $(i % 13)"
        println(io, "<block kind=\"$(kind)\" width=\"$(width)\" height=\"$(height)\">$(text)</block>")
    end
    println(io, "</root>")
    String(take!(io))
end

function _parse_fop_doc(doc::String)::Vector{_FopBlock}
    pattern = Regex("<block kind=\"(\\w+)\" width=\"(\\d+)\" height=\"(\\d+)\">(.*?)</block>")
    blocks = _FopBlock[]
    for m in eachmatch(pattern, doc)
        push!(blocks, _FopBlock(
            Symbol(m.captures[1]),
            parse(Int, m.captures[2]),
            parse(Int, m.captures[3]),
            m.captures[4],
        ))
    end
    blocks
end

function _layout_fop(blocks::Vector{_FopBlock}, page_height::Int)::Tuple{Int,Int}
    pages = 1
    cursor = 0
    checksum = 0
    for (i, block) in enumerate(blocks)
        text_lines = cld(length(block.text), max(1, block.width ÷ 8))
        block_height = block.height + text_lines * 4
        if cursor + block_height > page_height
            pages += 1
            cursor = 0
        end
        cursor += block_height
        checksum ⊻= Int(hash((block.kind, pages, cursor, i, length(block.text))) & 0x7fffffff)
    end
    pages, checksum
end

function run_fop(size::Int)::Dict{String,Any}
    n_blocks = size == 1 ? 450 : size == 2 ? 1600 : 4200
    doc = _generate_fop_doc(n_blocks)
    blocks = _parse_fop_doc(doc)
    pages, checksum = _layout_fop(blocks, 720)
    Dict{String,Any}(
        "workload" => "fop", "n_blocks" => n_blocks, "parsed_blocks" => length(blocks),
        "pages" => pages, "doc_bytes" => sizeof(doc), "checksum" => checksum,
    )
end

# ============================================================
# Workload: matrix – dense linear algebra (LAPACK JIT-heavy)
# ============================================================

function run_matrix(size::Int)::Dict{String,Any}
    n = max(50, size * 50)
    rng = Random.MersenneTwister(42)
    A = randn(rng, Float64, n, n)
    B = randn(rng, Float64, n, n)

    t0 = time_ns()

    # Matrix multiply – triggers BLAS compilation
    C = A * B
    mul_sum = sum(C)

    # Determinant – triggers LU factorisation path
    d = det(A)

    # Eigenvalue decomposition – triggers LAPACK eigen routines
    evals = eigvals(A)
    eval_sum = sum(real.(evals))

    # SVD – triggers LAPACK SVD routines
    F = svd(A)
    sv_sum = sum(F.S)

    # QR decomposition
    Q, R = qr(A)
    qr_diag_sum = sum(diag(R))

    elapsed = (time_ns() - t0) / 1.0e6
    Dict{String,Any}(
        "workload" => "matrix", "n" => n, "elapsed_ms" => elapsed,
        "mul_sum" => mul_sum, "det" => d, "eval_sum" => eval_sum,
        "sv_sum" => sv_sum, "qr_diag_sum" => qr_diag_sum,
    )
end

# ============================================================
# Workload: regex – compile and match complex patterns
# ============================================================

const _REGEX_PATTERNS = [
    r"\b[A-Z][a-z]+(?:ing|tion|ment|ness)\b",
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    r"(?:https?://)?(?:www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s]*)?",
    r"\b(?:error|warn|info|debug)\s*[:=]\s*[^\n]{1,80}",
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}",
    r"(?:[A-Z][a-z]+){2,4}",
    r"\b(?:function|struct|module|abstract|mutable)\s+[A-Za-z_]\w*",
]

function _generate_corpus(rng::Random.AbstractRNG, n_lines::Int)::String
    words = ["Processing", "Warning", "Connection", "established",
             "error:", "timeout", "2024-01-15T10:30:00", "info=",
             "debug:", "192.168.1.100", "user@example.com",
             "https://api.example.com/v1/data", "FunctionName",
             "AbstractType", "mutable struct Foo", "module Bar",
             "completed", "initialization", "management", "brightness"]
    lines = String[]
    for _ in 1:n_lines
        nw = rand(rng, 5:15)
        push!(lines, join(rand(rng, words, nw), " "))
    end
    join(lines, "\n")
end

function run_regex(size::Int)::Dict{String,Any}
    n_lines = max(200, size * 200)
    rng = Random.MersenneTwister(123)
    corpus = _generate_corpus(rng, n_lines)

    t0 = time_ns()
    total_matches = 0
    pattern_hits = Dict{Int,Int}()
    for (i, pat) in enumerate(_REGEX_PATTERNS)
        matches = collect(eachmatch(pat, corpus))
        total_matches += length(matches)
        pattern_hits[i] = length(matches)
    end

    # Also do replacements (triggers different codepaths)
    replaced = replace(corpus, r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}" => "[REDACTED]")
    replaced = replace(replaced, r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}" => "[EMAIL]")

    elapsed = (time_ns() - t0) / 1.0e6
    Dict{String,Any}(
        "workload" => "regex", "n_lines" => n_lines, "elapsed_ms" => elapsed,
        "total_matches" => total_matches, "n_patterns" => length(_REGEX_PATTERNS),
        "replaced_len" => length(replaced),
    )
end

# ============================================================
# Workload: sort – mixed types, custom comparators
# ============================================================

struct TaggedValue{T}
    tag::Symbol
    val::T
end

Base.isless(a::TaggedValue, b::TaggedValue) = isless(a.val, b.val)

function run_sort(size::Int)::Dict{String,Any}
    n = max(5000, size * 5000)
    rng = Random.MersenneTwister(7)

    t0 = time_ns()

    # Sort Float64 array
    f64 = randn(rng, n)
    sort!(f64)

    # Sort Int array
    ints = rand(rng, Int, n)
    sort!(ints)

    # Sort String array
    strs = [String(rand(rng, 'a':'z', rand(rng, 3:12))) for _ in 1:n]
    sort!(strs)

    # Sort Tuple array (triggers specialised comparators)
    tuples = [(rand(rng, 1:100), rand(rng)) for _ in 1:n]
    sort!(tuples; by=x -> (x[1], -x[2]))

    # Sort custom struct array (triggers generic dispatch)
    tagged = [TaggedValue(rand(rng, [:a, :b, :c]), randn(rng)) for _ in 1:n]
    sort!(tagged)

    # Partial sort / partialsortperm
    k = min(100, n)
    perm = partialsortperm(f64, 1:k)

    elapsed = (time_ns() - t0) / 1.0e6
    Dict{String,Any}(
        "workload" => "sort", "n" => n, "elapsed_ms" => elapsed,
        "f64_median" => f64[n ÷ 2], "n_strings" => length(strs),
        "top_tuple" => tuples[1], "perm_len" => length(perm),
    )
end

# ============================================================
# Dispatch
# ============================================================

function dispatch_workload(workload::AbstractString, size::Int)::Dict{String,Any}
    workload = lowercase(workload)
    workload == "fopo"     && (workload = "fop")
    workload == "lusearch" && return run_lusearch(size)
    workload == "h2"       && return run_h2(size)
    workload == "eclipse"  && return run_eclipse(size)
    workload == "jython"   && return run_jython(size)
    workload == "fop"      && return run_fop(size)
    workload == "matrix"   && return run_matrix(size)
    workload == "regex"    && return run_regex(size)
    workload == "sort"     && return run_sort(size)
    error("unknown workload: $workload (use lusearch|h2|eclipse|jython|fop|matrix|regex|sort)")
end

# ============================================================
# HTTP helpers
# ============================================================

function _parse_query(qs::AbstractString)::Dict{String,String}
    params = Dict{String,String}()
    for part in split(qs, '&')
        idx = findfirst('=', part)
        idx === nothing && continue
        k = String(part[1:prevind(part, idx)])
        v = String(part[nextind(part, idx):end])
        isempty(k) || (params[k] = v)
    end
    params
end

function _json_response(status::Int, payload::Dict{String,Any})::HTTP.Response
    body = JSON3.write(payload)
    HTTP.Response(status, ["Content-Type" => "application/json"], body)
end

# ============================================================
# Cache push endpoint helper (calls cachectl.py)
# ============================================================

function _push_trace_to_redis()::Dict{String,Any}
    if !isfile(TRACE_FILE)
        return Dict{String,Any}("ok" => false, "error" => "trace file not found: $TRACE_FILE")
    end
    result = run(ignorestatus(
        `python3 /app/cachectl.py push --trace-file $(TRACE_FILE)`
    ))
    success(result) && return Dict{String,Any}("ok" => true, "trace_file" => TRACE_FILE)
    return Dict{String,Any}("ok" => false, "error" => "cachectl.py push exited $(result.exitcode)")
end

# ============================================================
# Request handler
# ============================================================

function handle_request(req::HTTP.Request)::HTTP.Response
    target = req.target
    q_pos  = findfirst('?', target)
    path   = q_pos === nothing ? target : target[1:prevind(target, q_pos)]
    params = q_pos === nothing ? Dict{String,String}() :
             _parse_query(target[nextind(target, q_pos):end])

    if path in ("/_/health", "/healthz") || endswith(path, "/_/health")
        return _json_response(200, Dict{String,Any}(
            "ok" => true, "mode" => CACHE_MODE,
            "pod_uid" => POD_UID, "build_label" => BUILD_LABEL,
            "process_uptime_ms" => (time() - STARTED_AT) * 1000.0,
        ))
    end

    if path == "/_/cache/push" || endswith(path, "/_/cache/push")
        return _json_response(200, _push_trace_to_redis())
    end

    if !endswith(path, "/run") && path != "/run"
        return _json_response(404, Dict{String,Any}("error" => "not found", "path" => path))
    end

    workload = get(params, "workload", "lusearch")
    size     = something(tryparse(Int, get(params, "size", "1")), 1)

    req_num = lock(REQUEST_LOCK) do
        REQUEST_COUNT[] += 1
        REQUEST_COUNT[]
    end

    t0         = time_ns()
    result     = dispatch_workload(workload, size)
    elapsed_ms = (time_ns() - t0) / 1.0e6
    uptime_ms  = (time() - STARTED_AT) * 1000.0

    payload = merge(result, Dict{String,Any}(
        "elapsed_ms"        => elapsed_ms,
        "process_uptime_ms" => uptime_ms,
        "request_in_pod"    => req_num,
        "pod_uid"           => POD_UID,
        "build_label"       => BUILD_LABEL,
        "cache_mode"        => CACHE_MODE,
    ))
    _json_response(200, payload)
end

# ============================================================
# Precompile cache loader
# ============================================================

function _load_precompile_cache()
    cachefile = get(ENV, "JULIA_PRECOMPILE_FILE", "")
    isempty(cachefile) && return
    isfile(cachefile) || (@warn "precompile file not found" file=cachefile; return)
    @info "loading precompile cache" file=cachefile mode=CACHE_MODE
    t0 = time_ns()
    try
        include(cachefile)
        elapsed = (time_ns() - t0) / 1.0e6
        @info "precompile cache loaded" file=cachefile elapsed_ms=round(elapsed; digits=1)
    catch ex
        elapsed = (time_ns() - t0) / 1.0e6
        @warn "precompile cache failed; falling back to cold start" file=cachefile elapsed_ms=round(elapsed; digits=1) exception=(ex, catch_backtrace())
    end
end

# ============================================================
# Entry point
# ============================================================

const HANDLER_PORT = parse(Int, get(ENV, "HANDLER_PORT", "8000"))

if get(ENV, "JULIA_BUILD_SYSIMAGE", "") != "1"
    _load_precompile_cache()
    GC.gc(true)  # clear precompile IR allocations before first request
    @info "Julia handler ready" port=HANDLER_PORT mode=CACHE_MODE pod_uid=POD_UID build_label=BUILD_LABEL
    HTTP.serve(handle_request, "0.0.0.0", HANDLER_PORT)
end
