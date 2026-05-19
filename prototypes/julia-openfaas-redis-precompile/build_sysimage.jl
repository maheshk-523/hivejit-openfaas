#!/usr/bin/env julia
"""Build a PackageCompiler sysimage for the Julia OpenFaaS handler.

Runs each workload N_PROFILES times to maximise method-specialisation coverage
before calling create_sysimage(). The resulting sysimage.so is loaded at pod
startup with `julia -J /app/sysimage.so`, eliminating first-call JIT entirely
for all covered method specialisations.

Usage:
  julia --startup-file=no build_sysimage.jl [n_profiles [workloads...]]

Examples:
  julia --startup-file=no build_sysimage.jl 5
  julia --startup-file=no build_sysimage.jl 10 lusearch h2 eclipse jython fop
"""

n_profiles = length(ARGS) >= 1 ? parse(Int, ARGS[1]) : 5
workloads  = length(ARGS) >= 2 ? collect(ARGS[2:end]) : ["lusearch", "h2", "eclipse", "jython", "fop"]

@info "AOT sysimage build" n_profiles=n_profiles workloads=workloads

using PackageCompiler
import Pkg

project_dir = mktempdir()
Pkg.activate(project_dir)
Pkg.add(["HTTP", "JSON3"])

exec_file = tempname() * ".jl"
open(exec_file, "w") do io
    println(io, """
ENV["JULIA_BUILD_SYSIMAGE"] = "1"
include("/app/handler.jl")

@info "precompile warmup" n_profiles=$n_profiles workloads=$(repr(workloads))
for _profile in 1:$n_profiles
    for _wl in $(repr(workloads))
        try
            dispatch_workload(_wl, 2)
        catch e
            @warn "warmup failed" profile=_profile workload=_wl exception=e
        end
    end
end
@info "precompile warmup done"
""")
end

t0 = time()
# Keep the sysimage focused on runtime-derived workload specialisations.
# Baking the full HTTP/JSON server stack into the image made the Docker build
# impractically slow on the local OpenFaaS test VM, while the benchmark signal
# comes from dispatch_workload() and its profile-driven callees.
create_sysimage(
    String[],
    project = project_dir,
    sysimage_path = "/app/sysimage.so",
    precompile_execution_file = exec_file,
)
elapsed = round(time() - t0; digits=1)
sz_mb   = round(stat("/app/sysimage.so").size / 1024^2; digits=1)

@info "sysimage ready" path="/app/sysimage.so" size_mb=sz_mb build_seconds=elapsed n_profiles=n_profiles
