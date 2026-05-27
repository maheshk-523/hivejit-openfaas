#!/usr/bin/env python3
"""Real JAX workload persistent-cache probe.

This is a self-contained JAX/XLA cache probe with scientific-kernel and Flax
model workloads. Static scenario/config fields produce specialized JAX/XLA
programs, and fresh processes can reuse persisted compilation artifacts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import statistics
import tarfile
import time
from pathlib import Path
from typing import Any


SCHEMA = "jax-real-workload-profile.v1"
SUMMARY_SCHEMA = "jax-real-workload-measurement.v1"

SCENARIOS: dict[str, dict[str, Any]] = {
    "torax-pulse-64": {
        "workloadKind": "transport",
        "mesh": 64,
        "channels": 4,
        "timesteps": 4,
        "solverIters": 2,
        "modelDepth": 2,
        "controllerMode": "pulse",
        "useSurrogate": False,
        "dtype": "float32",
    },
    "torax-mlsurrogate-64": {
        "workloadKind": "transport",
        "mesh": 64,
        "channels": 4,
        "timesteps": 4,
        "solverIters": 2,
        "modelDepth": 2,
        "controllerMode": "feedback",
        "useSurrogate": True,
        "dtype": "float32",
    },
    "torax-control-96": {
        "workloadKind": "transport",
        "mesh": 96,
        "channels": 4,
        "timesteps": 5,
        "solverIters": 2,
        "modelDepth": 3,
        "controllerMode": "feedback",
        "useSurrogate": True,
        "dtype": "float32",
    },
    "torax-pulse-64-mismatch": {
        "workloadKind": "transport",
        "mesh": 72,
        "channels": 4,
        "timesteps": 4,
        "solverIters": 3,
        "modelDepth": 2,
        "controllerMode": "pulse",
        "useSurrogate": False,
        "dtype": "float32",
    },
    "pyhpc-teos10-gsw-dhdt": {
        "workloadKind": "teos10",
        "mesh": 512,
        "channels": 4,
        "timesteps": 4,
        "solverIters": 1,
        "modelDepth": 4,
        "controllerMode": "eos",
        "useSurrogate": False,
        "dtype": "float32",
    },
    "pyhpc-isoneutral-mixing": {
        "workloadKind": "isoneutral",
        "mesh": 384,
        "channels": 4,
        "timesteps": 5,
        "solverIters": 2,
        "modelDepth": 3,
        "controllerMode": "mixing",
        "useSurrogate": False,
        "dtype": "float32",
    },
    "pyhpc-teos10-mismatch": {
        "workloadKind": "teos10",
        "mesh": 544,
        "channels": 4,
        "timesteps": 4,
        "solverIters": 1,
        "modelDepth": 5,
        "controllerMode": "eos",
        "useSurrogate": False,
        "dtype": "float32",
    },
    "flax-transformer-train-128": {
        "workloadKind": "flax_transformer_train",
        "mesh": 128,
        "channels": 192,
        "batch": 4,
        "vocabSize": 2048,
        "mlpDim": 768,
        "timesteps": 1,
        "solverIters": 4,
        "modelDepth": 4,
        "controllerMode": "language-model-train-step",
        "useSurrogate": False,
        "dtype": "float32",
    },
    "flax-transformer-infer-128": {
        "workloadKind": "flax_transformer_infer",
        "mesh": 128,
        "channels": 192,
        "batch": 4,
        "vocabSize": 2048,
        "mlpDim": 768,
        "timesteps": 1,
        "solverIters": 4,
        "modelDepth": 4,
        "controllerMode": "language-model-inference",
        "useSurrogate": False,
        "dtype": "float32",
    },
    "flax-transformer-train-160-mismatch": {
        "workloadKind": "flax_transformer_train",
        "mesh": 160,
        "channels": 192,
        "batch": 4,
        "vocabSize": 2048,
        "mlpDim": 768,
        "timesteps": 1,
        "solverIters": 4,
        "modelDepth": 4,
        "controllerMode": "language-model-train-step",
        "useSurrogate": False,
        "dtype": "float32",
    },
    "flax-mnist-cnn-train-real": {
        "workloadKind": "flax_mnist_cnn_train",
        "mesh": 28,
        "channels": 1,
        "batch": 128,
        "vocabSize": 10,
        "mlpDim": 256,
        "timesteps": 1,
        "solverIters": 2,
        "modelDepth": 2,
        "controllerMode": "mnist-cnn-train-step",
        "useSurrogate": False,
        "dtype": "float32",
    },
    "flax-mnist-cnn-train-real-mismatch": {
        "workloadKind": "flax_mnist_cnn_train",
        "mesh": 28,
        "channels": 1,
        "batch": 160,
        "vocabSize": 10,
        "mlpDim": 256,
        "timesteps": 1,
        "solverIters": 2,
        "modelDepth": 2,
        "controllerMode": "mnist-cnn-train-step",
        "useSurrogate": False,
        "dtype": "float32",
    },
}


def stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def stable_seed(name: str, iteration: int = 0) -> int:
    raw = f"{name}:{iteration}".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:8], 16)


def profile_for_scenarios(selected: list[str]) -> dict[str, Any]:
    scenarios = []
    for name in selected:
        if name not in SCENARIOS:
            raise ValueError(f"unknown scenario {name}")
        spec = dict(SCENARIOS[name])
        spec["name"] = name
        spec["scenarioHash"] = stable_hash(spec)
        scenarios.append(spec)

    profile = {
        "schema": SCHEMA,
        "domain": "JAX/XLA real workload",
        "workload": "JAX scientific and Flax model cache workloads",
        "pattern": (
            "scenario/config profile -> JAX trace -> StableHLO/XLA executable -> "
            "persistent compilation cache -> fresh-process reuse"
        ),
        "scenarios": scenarios,
    }
    profile["profileHash"] = stable_hash(profile)
    return profile


def configure_jax(cache_dir: Path | None, explain_cache: bool = False) -> None:
    os.environ.setdefault("JAX_PLATFORMS", "cpu")
    if explain_cache:
        os.environ.setdefault("JAX_DEBUG_LOG_MODULES", "jax._src.compiler,jax._src.lru_cache")

    import jax

    jax.config.update("jax_enable_x64", False)
    if explain_cache:
        try:
            jax.config.update("jax_explain_cache_misses", True)
        except Exception:
            pass
    if cache_dir is None:
        return

    cache_dir.mkdir(parents=True, exist_ok=True)
    jax.config.update("jax_compilation_cache_dir", str(cache_dir))
    jax.config.update("jax_persistent_cache_min_compile_time_secs", 0)
    jax.config.update("jax_persistent_cache_min_entry_size_bytes", -1)


def make_flax_transformer_model(
    *,
    vocab_size: int,
    max_length: int,
    hidden_size: int,
    num_heads: int,
    mlp_dim: int,
    num_layers: int,
) -> Any:
    from flax import linen as nn

    class TransformerBlock(nn.Module):
        hidden_size: int
        num_heads: int
        mlp_dim: int

        @nn.compact
        def __call__(self, x: Any) -> Any:
            attn_in = nn.LayerNorm(name="attn_norm")(x)
            attn = nn.SelfAttention(
                num_heads=self.num_heads,
                qkv_features=self.hidden_size,
                out_features=self.hidden_size,
                use_bias=False,
                deterministic=True,
                name="self_attention",
            )(attn_in)
            x = x + attn

            mlp_in = nn.LayerNorm(name="mlp_norm")(x)
            y = nn.Dense(self.mlp_dim, name="mlp_up")(mlp_in)
            y = nn.gelu(y)
            y = nn.Dense(self.hidden_size, name="mlp_down")(y)
            return x + y

    class TransformerLM(nn.Module):
        vocab_size: int
        max_length: int
        hidden_size: int
        num_heads: int
        mlp_dim: int
        num_layers: int

        @nn.compact
        def __call__(self, token_ids: Any) -> Any:
            import jax.numpy as jnp

            positions = jnp.arange(token_ids.shape[1], dtype=jnp.int32)[None, :]
            token_embed = nn.Embed(
                num_embeddings=self.vocab_size,
                features=self.hidden_size,
                name="token_embed",
            )(token_ids)
            position_embed = nn.Embed(
                num_embeddings=self.max_length,
                features=self.hidden_size,
                name="position_embed",
            )(positions)
            x = token_embed + position_embed
            for layer_index in range(self.num_layers):
                x = TransformerBlock(
                    hidden_size=self.hidden_size,
                    num_heads=self.num_heads,
                    mlp_dim=self.mlp_dim,
                    name=f"block_{layer_index}",
                )(x)
            x = nn.LayerNorm(name="final_norm")(x)
            return nn.Dense(self.vocab_size, name="lm_head")(x)

    return TransformerLM(
        vocab_size=vocab_size,
        max_length=max_length,
        hidden_size=hidden_size,
        num_heads=num_heads,
        mlp_dim=mlp_dim,
        num_layers=num_layers,
    )


def make_flax_mnist_cnn_model(*, hidden_size: int) -> Any:
    from flax import linen as nn

    class MNISTCNN(nn.Module):
        hidden_size: int

        @nn.compact
        def __call__(self, images: Any) -> Any:
            x = nn.Conv(features=32, kernel_size=(3, 3), name="conv1")(images)
            x = nn.relu(x)
            x = nn.avg_pool(x, window_shape=(2, 2), strides=(2, 2))
            x = nn.Conv(features=64, kernel_size=(3, 3), name="conv2")(x)
            x = nn.relu(x)
            x = nn.avg_pool(x, window_shape=(2, 2), strides=(2, 2))
            x = x.reshape((x.shape[0], -1))
            x = nn.Dense(features=self.hidden_size, name="dense1")(x)
            x = nn.relu(x)
            return nn.Dense(features=10, name="dense2")(x)

    return MNISTCNN(hidden_size=hidden_size)


def load_real_mnist_batch(spec: dict[str, Any], iteration: int) -> tuple[Any, Any]:
    import numpy as np

    dataset_path = Path(
        os.environ.get(
            "JAX_REAL_WORKLOAD_MNIST_NPZ",
            str(Path(__file__).resolve().parent / "data" / "mnist.npz"),
        )
    )
    if not dataset_path.exists():
        raise FileNotFoundError(
            "MNIST data is required for real-data Flax scenarios. "
            f"Download it to {dataset_path} or set JAX_REAL_WORKLOAD_MNIST_NPZ."
        )
    with np.load(dataset_path) as data:
        images = data["x_train"]
        labels = data["y_train"]
    batch = int(spec.get("batch", 128))
    start = ((max(iteration, 1) - 1) * batch) % (len(images) - batch)
    image_batch = images[start : start + batch].astype(np.float32) / 255.0
    image_batch = image_batch[..., None]
    label_batch = labels[start : start + batch].astype(np.int32)
    return image_batch, label_batch


def make_inputs(spec: dict[str, Any], iteration: int) -> tuple[Any, Any, Any, Any, Any, Any]:
    import jax
    import jax.numpy as jnp
    import numpy as np

    workload_kind = str(spec.get("workloadKind", "transport"))
    cells = int(spec["mesh"])
    channels = int(spec["channels"])
    timesteps = int(spec["timesteps"])
    rng = np.random.default_rng(stable_seed(spec["name"], iteration))

    if workload_kind.startswith("flax_transformer"):
        batch = int(spec.get("batch", 4))
        vocab_size = int(spec.get("vocabSize", 2048))
        hidden_size = int(spec["channels"])
        mlp_dim = int(spec.get("mlpDim", hidden_size * 4))
        num_heads = int(spec["solverIters"])
        num_layers = int(spec["modelDepth"])
        token_ids = rng.integers(0, vocab_size, size=(batch, cells), dtype=np.int32)
        targets = np.roll(token_ids, shift=-1, axis=1).astype(np.int32)
        model = make_flax_transformer_model(
            vocab_size=vocab_size,
            max_length=cells,
            hidden_size=hidden_size,
            num_heads=num_heads,
            mlp_dim=mlp_dim,
            num_layers=num_layers,
        )
        params = model.init(jax.random.PRNGKey(stable_seed(spec["name"], -1)), jnp.asarray(token_ids))["params"]
        loss_scale = jnp.asarray([1.0], dtype=jnp.float32)
        return (
            jnp.asarray(token_ids),
            params,
            jnp.asarray(targets),
            loss_scale,
            loss_scale,
            loss_scale,
        )

    if workload_kind == "flax_mnist_cnn_train":
        image_batch, label_batch = load_real_mnist_batch(spec, iteration)
        model = make_flax_mnist_cnn_model(hidden_size=int(spec.get("mlpDim", 256)))
        params = model.init(jax.random.PRNGKey(stable_seed(spec["name"], -1)), jnp.asarray(image_batch))["params"]
        loss_scale = jnp.asarray([1.0], dtype=jnp.float32)
        return (
            jnp.asarray(image_batch),
            params,
            jnp.asarray(label_batch),
            loss_scale,
            loss_scale,
            loss_scale,
        )

    if workload_kind in {"teos10", "isoneutral"}:
        x = np.linspace(-1.0, 1.0, cells, dtype=np.float32)
        y = np.linspace(-1.0, 1.0, cells, dtype=np.float32)
        xx, yy = np.meshgrid(x, y, indexing="ij")
        temperature = 9.0 + 2.5 * np.sin(np.pi * xx) * np.cos(np.pi * yy)
        salinity = 34.5 + 0.45 * np.cos(2.0 * np.pi * xx) + 0.08 * rng.normal(size=(cells, cells))
        pressure = 1000.0 + 180.0 * (yy + 1.0) + 25.0 * np.sin(np.pi * xx)
        tracer = 0.6 + 0.25 * np.sin(np.pi * xx * yy) + 0.03 * rng.normal(size=(cells, cells))
        coeff = np.stack([salinity, temperature, pressure, tracer], axis=-1).astype(np.float32)
        source = np.stack(
            [
                0.05 * np.sin(np.pi * xx),
                0.04 * np.cos(np.pi * yy),
                0.02 * np.sin(2.0 * np.pi * (xx + yy)),
                0.03 * np.cos(np.pi * xx * yy),
            ],
            axis=-1,
        ).astype(np.float32)
        schedule = np.stack(
            [
                np.linspace(0.8, 1.1, timesteps, dtype=np.float32),
                np.linspace(0.4, 0.7, timesteps, dtype=np.float32),
                np.linspace(0.2, 0.5, timesteps, dtype=np.float32),
                np.linspace(0.1, 0.3, timesteps, dtype=np.float32),
            ],
            axis=1,
        )
        w1 = rng.normal(0.0, 0.10, size=(channels, 12)).astype(np.float32)
        w2 = rng.normal(0.0, 0.08, size=(12, channels)).astype(np.float32)
        return (
            jnp.asarray(temperature.astype(np.float32)),
            jnp.asarray(coeff),
            jnp.asarray(source),
            jnp.asarray(schedule),
            jnp.asarray(w1),
            jnp.asarray(w2),
        )

    rho = np.linspace(0.0, 1.0, cells, dtype=np.float32)

    base = np.stack(
        [
            1.40 - 0.55 * rho**2,
            1.05 - 0.35 * rho**1.5,
            0.70 + 0.18 * np.cos(np.pi * rho),
            0.48 + 0.12 * np.sin(2.0 * np.pi * rho),
        ],
        axis=1,
    ).astype(np.float32)
    state = base + rng.normal(0.0, 0.015, size=(cells, channels)).astype(np.float32)

    coeff = np.stack(
        [
            0.045 + 0.018 * rho,
            0.038 + 0.014 * rho**2,
            0.026 + 0.008 * np.sin(np.pi * rho) ** 2,
            0.018 + 0.012 * rho,
        ],
        axis=1,
    ).astype(np.float32)

    heat = np.exp(-((rho - 0.28) ** 2) / 0.018)
    edge = np.exp(-((rho - 0.83) ** 2) / 0.012)
    source = np.stack(
        [
            0.08 * heat,
            0.06 * heat + 0.015 * edge,
            0.025 * edge,
            0.018 * (1.0 - rho),
        ],
        axis=1,
    ).astype(np.float32)

    schedule = []
    for step in range(timesteps):
        phase = step / max(timesteps - 1, 1)
        schedule.append(
            [
                0.80 + 0.20 * np.sin(2.0 * np.pi * phase),
                0.55 + 0.12 * np.cos(2.0 * np.pi * phase),
                0.25 + 0.20 * phase,
                0.18 + 0.05 * np.sin(4.0 * np.pi * phase),
            ]
        )
    actuator_schedule = np.asarray(schedule, dtype=np.float32)

    hidden = 12
    w1 = rng.normal(0.0, 0.18, size=(channels, hidden)).astype(np.float32)
    w2 = rng.normal(0.0, 0.10, size=(hidden, channels)).astype(np.float32)

    return (
        jnp.asarray(state),
        jnp.asarray(coeff),
        jnp.asarray(source),
        jnp.asarray(actuator_schedule),
        jnp.asarray(w1),
        jnp.asarray(w2),
    )


def transport_kernel(
    state: Any,
    coeff: Any,
    source: Any,
    actuator_schedule: Any,
    surrogate_w1: Any,
    surrogate_w2: Any,
    *,
    workload_kind: str,
    mesh: int,
    channels: int,
    batch: int,
    vocab_size: int,
    mlp_dim: int,
    timesteps: int,
    solver_iters: int,
    model_depth: int,
    controller_mode: str,
    use_surrogate: bool,
) -> Any:
    import jax
    import jax.numpy as jnp

    if workload_kind in {"flax_transformer_train", "flax_transformer_infer"}:
        token_ids = state.astype(jnp.int32)
        params = coeff
        targets = source.astype(jnp.int32)
        model = make_flax_transformer_model(
            vocab_size=vocab_size,
            max_length=mesh,
            hidden_size=channels,
            num_heads=solver_iters,
            mlp_dim=mlp_dim,
            num_layers=model_depth,
        )

        def loss_fn(candidate_params: Any) -> Any:
            logits = model.apply({"params": candidate_params}, token_ids)
            labels = jax.nn.one_hot(targets, vocab_size, dtype=logits.dtype)
            token_loss = -jnp.sum(labels * jax.nn.log_softmax(logits), axis=-1)
            return jnp.mean(token_loss)

        if workload_kind == "flax_transformer_infer":
            logits = model.apply({"params": params}, token_ids)
            return jnp.asarray(
                [
                    jnp.mean(logits),
                    jnp.std(logits),
                    jnp.max(logits),
                    jnp.min(logits),
                ],
                dtype=logits.dtype,
            )

        loss, grads = jax.value_and_grad(loss_fn)(params)
        updated_params = jax.tree.map(lambda param, grad: param - 0.01 * grad, params, grads)
        grad_leaves = jax.tree.leaves(grads)
        param_leaves = jax.tree.leaves(updated_params)
        grad_norm = jnp.sqrt(sum(jnp.sum(jnp.square(leaf)) for leaf in grad_leaves))
        param_checksum = sum(jnp.sum(leaf) * jnp.asarray(1.0e-7, dtype=leaf.dtype) for leaf in param_leaves)
        return jnp.asarray([loss, grad_norm, param_checksum], dtype=jnp.float32)

    if workload_kind == "flax_mnist_cnn_train":
        images = state
        params = coeff
        labels = source.astype(jnp.int32)
        model = make_flax_mnist_cnn_model(hidden_size=mlp_dim)

        def loss_fn(candidate_params: Any) -> Any:
            logits = model.apply({"params": candidate_params}, images)
            one_hot = jax.nn.one_hot(labels, 10, dtype=logits.dtype)
            return -jnp.mean(jnp.sum(one_hot * jax.nn.log_softmax(logits), axis=-1))

        loss, grads = jax.value_and_grad(loss_fn)(params)
        logits = model.apply({"params": params}, images)
        accuracy = jnp.mean(jnp.argmax(logits, axis=-1) == labels)
        grad_norm = jnp.sqrt(sum(jnp.sum(jnp.square(leaf)) for leaf in jax.tree.leaves(grads)))
        return jnp.asarray([loss, accuracy, grad_norm], dtype=jnp.float32)

    if workload_kind == "teos10":
        temperature = state
        salinity = coeff[..., 0]
        pressure = coeff[..., 2]
        tracer = coeff[..., 3]
        t = temperature * 0.025
        s = (salinity - 35.0) * 0.10
        p = pressure * 0.0005
        z = (
            0.78
            + 0.12 * t
            - 0.08 * s
            + 0.04 * p
            + 0.03 * t * s
            - 0.02 * s * p
            + 0.015 * t * t
        )
        for depth in range(model_depth):
            z = (
                z
                + (0.10 + depth * 0.015) * jnp.sin(t + z)
                + 0.055 * jnp.cos(s - p)
                + 0.020 * jnp.roll(z, shift=depth + 1, axis=0)
                - 0.016 * jnp.roll(z, shift=depth + 1, axis=1)
            )
            derivative = (
                0.12
                + 0.03 * s
                + 0.03 * t
                + 0.10 * jnp.cos(t + z)
                - 0.015 * jnp.sin(z)
            )
            z = jnp.tanh(z + 0.08 * derivative + 0.01 * tracer)
        summary = jnp.asarray(
            [
                jnp.mean(z),
                jnp.std(z),
                jnp.mean(derivative),
                jnp.max(derivative),
                jnp.min(derivative),
            ],
            dtype=z.dtype,
        )
        sample = z[:: max(z.shape[0] // 8, 1), :: max(z.shape[1] // 8, 1)].reshape(-1)
        return jnp.concatenate([summary, sample])

    if workload_kind == "isoneutral":
        tracer = state
        salinity = coeff[..., 0]
        temperature = coeff[..., 1]
        mixing = 0.04 + 0.015 * jnp.tanh(coeff[..., 3])
        current = tracer
        diagnostics = []
        for step in range(timesteps):
            forcing = source[..., step % source.shape[-1]]
            for depth in range(model_depth):
                east = jnp.roll(current, shift=-1, axis=1)
                west = jnp.roll(current, shift=1, axis=1)
                north = jnp.roll(current, shift=-1, axis=0)
                south = jnp.roll(current, shift=1, axis=0)
                slope_x = jnp.roll(temperature, -1, axis=1) - jnp.roll(temperature, 1, axis=1)
                slope_y = jnp.roll(salinity, -1, axis=0) - jnp.roll(salinity, 1, axis=0)
                lap = east + west + north + south - 4.0 * current
                skew_flux = slope_x * (east - west) + slope_y * (north - south)
                current = jnp.tanh(current + mixing * lap + 0.025 * skew_flux + 0.015 * forcing)
                current = 0.85 * current + 0.15 * jnp.sort(current, axis=1)
            diagnostics.append(jnp.asarray([jnp.mean(current), jnp.std(current)], dtype=current.dtype))
        sample = current[:: max(current.shape[0] // 8, 1), :: max(current.shape[1] // 8, 1)].reshape(-1)
        return jnp.concatenate([jnp.stack(diagnostics).reshape(-1), sample])

    cells = state.shape[0]
    rho = jnp.linspace(0.0, 1.0, cells, dtype=state.dtype)
    radial_core = jnp.exp(-((rho - 0.30) ** 2) / 0.020)[:, None]
    radial_edge = jnp.exp(-((rho - 0.82) ** 2) / 0.014)[:, None]
    coupling = jnp.asarray(
        [
            [0.96, 0.03, 0.00, 0.01],
            [0.04, 0.93, 0.02, 0.01],
            [0.01, 0.03, 0.94, 0.02],
            [0.02, 0.00, 0.04, 0.94],
        ],
        dtype=state.dtype,
    )

    current = state
    diagnostic_rows = []
    for step_index in range(timesteps):
        actuator = actuator_schedule[step_index]
        if controller_mode == "feedback":
            error = jnp.mean(current[:, :2], axis=0) - jnp.asarray([1.04, 0.86], dtype=current.dtype)
            feedback = jnp.asarray([error[0], error[1], -0.5 * error[0], 0.25 * error[1]], dtype=current.dtype)
            actuator = actuator - 0.35 * feedback

        shaped_source = source + radial_core * actuator[None, :] * 0.030
        shaped_source = shaped_source + radial_edge * actuator[None, :] * jnp.asarray(
            [0.00, 0.008, 0.020, 0.006], dtype=current.dtype
        )

        guess = current
        for _solver_iteration in range(solver_iters):
            left = jnp.roll(guess, shift=1, axis=0)
            right = jnp.roll(guess, shift=-1, axis=0)
            lap = left - 2.0 * guess + right
            grad = right - left
            diffusion = coeff * (1.0 + 0.10 * jnp.tanh(guess))
            transport = diffusion * lap - 0.018 * grad
            coupled = (guess + shaped_source) @ coupling
            scan = jnp.cumsum(coupled + transport, axis=0) / jnp.asarray(cells, dtype=guess.dtype)
            ranked = jnp.sort(coupled + 0.10 * transport, axis=0)
            nonlinear = jnp.tanh(coupled + 0.06 * scan + 0.04 * ranked + 0.12 * jnp.sin(guess))

            correction = jnp.zeros_like(guess)
            if use_surrogate:
                hidden = jnp.tanh(guess @ surrogate_w1)
                correction = 0.030 * (hidden @ surrogate_w2)

            shaped = nonlinear
            for depth in range(model_depth):
                shifted = jnp.roll(shaped, shift=depth + 1, axis=0)
                shaped = jnp.tanh(0.70 * shaped + 0.22 * shifted + 0.08 * jnp.sin(guess))

            residual = transport + shaped_source - 0.030 * guess + 0.035 * shaped + correction
            guess = jnp.tanh(guess + 0.080 * residual)
            guess = jnp.where(guess > -0.98, guess, -0.98)

        current = guess
        diagnostics = jnp.asarray(
            [
                jnp.mean(current[:, 0]),
                jnp.mean(current[:, 1]),
                jnp.max(current[:, 2]),
                jnp.sum(current[:, 3]) / cells,
            ],
            dtype=current.dtype,
        )
        diagnostic_rows.append(diagnostics)

    final_state = current
    diagnostics = jnp.stack(diagnostic_rows, axis=0)
    sample_stride = max(cells // 8, 1)
    sampled = final_state[::sample_stride, :].reshape(-1)
    return jnp.concatenate(
        [
            jnp.mean(final_state, axis=0),
            jnp.std(final_state, axis=0),
            diagnostics.reshape(-1),
            sampled,
        ]
    )


def jitted_kernel() -> Any:
    import jax

    return jax.jit(
        transport_kernel,
        static_argnames=(
            "workload_kind",
            "mesh",
            "channels",
            "batch",
            "vocab_size",
            "mlp_dim",
            "timesteps",
            "solver_iters",
            "model_depth",
            "controller_mode",
            "use_surrogate",
        ),
    )


def cache_stats(path: Path | None) -> tuple[int, int]:
    if path is None or not path.exists():
        return 0, 0
    files = [entry for entry in path.rglob("*") if entry.is_file()]
    return len(files), sum(entry.stat().st_size for entry in files)


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def measure_scenario(
    label: str,
    spec: dict[str, Any],
    iteration: int,
    cache_dir: Path | None,
    hlo_dir: Path | None,
    executions: int,
    import_meta: dict[str, Any],
    export_meta: dict[str, Any],
    split_trace: bool,
) -> dict[str, Any]:
    import jax
    import jax.numpy as jnp

    cache_files_before, cache_bytes_before = cache_stats(cache_dir)
    kernel = jitted_kernel()
    inputs = make_inputs(spec, iteration)
    static_kwargs = {
        "workload_kind": str(spec.get("workloadKind", "transport")),
        "mesh": int(spec["mesh"]),
        "channels": int(spec["channels"]),
        "batch": int(spec.get("batch", 0)),
        "vocab_size": int(spec.get("vocabSize", 0)),
        "mlp_dim": int(spec.get("mlpDim", int(spec["channels"]) * 4)),
        "timesteps": int(spec["timesteps"]),
        "solver_iters": int(spec["solverIters"]),
        "model_depth": int(spec["modelDepth"]),
        "controller_mode": str(spec["controllerMode"]),
        "use_surrogate": bool(spec["useSurrogate"]),
    }

    trace_ms = 0.0
    if split_trace and hasattr(kernel, "trace"):
        trace_start = time.perf_counter()
        traced = kernel.trace(*inputs, **static_kwargs)
        trace_ms = (time.perf_counter() - trace_start) * 1000.0
        lower_start = time.perf_counter()
        lowered = traced.lower()
    else:
        # Keep the default path aligned with the stable JAX persistent-cache API.
        # In JAX 0.10, the split trace/lower diagnostic path can produce
        # process-unstable persistent-cache keys for this miniapp.
        lower_start = time.perf_counter()
        lowered = kernel.lower(*inputs, **static_kwargs)
    lower_ms = (time.perf_counter() - lower_start) * 1000.0

    compile_start = time.perf_counter()
    compiled = lowered.compile()
    compile_or_load_ms = (time.perf_counter() - compile_start) * 1000.0

    hlo_path = ""
    if hlo_dir is not None:
        hlo_dir.mkdir(parents=True, exist_ok=True)
        hlo_file = hlo_dir / f"{label}-{spec['name']}-{spec['scenarioHash']}.hlo.txt"
        try:
            hlo_file.write_text(compiled.as_text(), encoding="utf-8")
            hlo_path = str(hlo_file)
        except Exception:
            hlo_path = ""

    execute_ms_values: list[float] = []
    checksum = 0.0
    current_inputs = inputs
    for exec_index in range(executions):
        start = time.perf_counter()
        out = compiled(*current_inputs)
        out.block_until_ready()
        execute_ms_values.append((time.perf_counter() - start) * 1000.0)
        checksum = float(jax.device_get(jnp.sum(out)))
        if exec_index + 1 < executions:
            perturb = jnp.asarray(0.00001 * (exec_index + 1), dtype=current_inputs[0].dtype)
            current_inputs = (current_inputs[0] + perturb, *current_inputs[1:])

    cache_files_after, cache_bytes_after = cache_stats(cache_dir)
    first_execute_ms = execute_ms_values[0]
    handler_ms = trace_ms + lower_ms + compile_or_load_ms + first_execute_ms
    artifact_import_ms = float(import_meta.get("import_ms", 0.0) or 0.0)
    artifact_export_ms = float(export_meta.get("export_ms", 0.0) or 0.0)
    return {
        "schema": SUMMARY_SCHEMA,
        "label": label,
        "scenario": spec["name"],
        "scenario_hash": spec["scenarioHash"],
        "iteration": iteration,
        "trace_ms": trace_ms,
        "lower_ms": lower_ms,
        "compile_or_load_ms": compile_or_load_ms,
        "first_execute_ms": first_execute_ms,
        "execute_ms_median": statistics.median(execute_ms_values),
        "execute_ms_min": min(execute_ms_values),
        "execute_ms_max": max(execute_ms_values),
        "handler_ms": handler_ms,
        "startup_plus_first_request_ms": artifact_import_ms + handler_ms,
        "checksum": checksum,
        "cache_enabled": cache_dir is not None,
        "cache_files_before": cache_files_before,
        "cache_bytes_before": cache_bytes_before,
        "cache_files_after": cache_files_after,
        "cache_bytes_after": cache_bytes_after,
        "artifact_imported": bool(import_meta.get("imported", False)),
        "artifact_import_ms": artifact_import_ms,
        "artifact_export_ms": artifact_export_ms,
        "archive_bytes": int(import_meta.get("archive_bytes") or export_meta.get("archive_bytes") or 0),
        "hlo_path": hlo_path,
    }


def load_profile(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"unsupported profile schema in {path}")
    return list(data["scenarios"])


CSV_FIELDS = [
    "label",
    "scenario",
    "scenario_hash",
    "iteration",
    "trace_ms",
    "lower_ms",
    "compile_or_load_ms",
    "first_execute_ms",
    "execute_ms_median",
    "execute_ms_min",
    "execute_ms_max",
    "handler_ms",
    "startup_plus_first_request_ms",
    "checksum",
    "cache_enabled",
    "cache_files_before",
    "cache_bytes_before",
    "cache_files_after",
    "cache_bytes_after",
    "artifact_imported",
    "artifact_import_ms",
    "artifact_export_ms",
    "archive_bytes",
    "hlo_path",
]


def write_csv(path: Path, rows: list[dict[str, Any]], append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    mode = "a" if append else "w"
    with path.open(mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not append or not exists:
            writer.writeheader()
        for row in rows:
            formatted = {}
            for key in CSV_FIELDS:
                value = row.get(key, "")
                if isinstance(value, float):
                    formatted[key] = f"{value:.6f}"
                elif isinstance(value, bool):
                    formatted[key] = str(value).lower()
                else:
                    formatted[key] = value
            writer.writerow(formatted)


def archive_cache(cache_dir: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        if cache_dir.exists():
            for entry in sorted(cache_dir.rglob("*")):
                if entry.is_file():
                    tar.add(entry, arcname=str(entry.relative_to(cache_dir)))
    return buf.getvalue()


def safe_extract_cache(archive: bytes, cache_dir: Path) -> None:
    root = cache_dir.resolve()
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            target = (cache_dir / member.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"refusing unsafe tar member: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out)


def archive_path(store_dir: Path, key: str) -> Path:
    safe_key = key.replace("/", "_").replace(":", "_")
    return store_dir / f"{safe_key}.tar.gz"


def command_profile(args: argparse.Namespace) -> int:
    profile = profile_for_scenarios(args.scenario)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print(f"wrote scenario profile: {args.out}")
    print(f"profile hash: {profile['profileHash']}")
    for scenario in profile["scenarios"]:
        print(
            f"observed {scenario['name']}: mesh={scenario['mesh']} "
            f"timesteps={scenario['timesteps']} solverIters={scenario['solverIters']} "
            f"surrogate={scenario['useSurrogate']}"
        )
    return 0


def command_cache_key(args: argparse.Namespace) -> int:
    data = json.loads(args.profile.read_text(encoding="utf-8"))
    print(f"jax-real-workload-cache:{data['profileHash']}")
    return 0


def command_measure(args: argparse.Namespace) -> int:
    cache_dir = args.cache_dir if args.cache_dir else None
    configure_jax(cache_dir, explain_cache=args.explain_cache)
    import_meta = read_json(args.import_meta)
    export_meta = read_json(args.export_meta)
    rows = []
    for spec in load_profile(args.profile):
        rows.append(
            measure_scenario(
                label=args.label,
                spec=spec,
                iteration=args.iteration,
                cache_dir=cache_dir,
                hlo_dir=args.hlo_dir,
                executions=args.executions,
                import_meta=import_meta,
                export_meta=export_meta,
                split_trace=args.split_trace,
            )
        )
    write_csv(args.csv, rows, append=args.append)
    for row in rows:
        print(
            f"{row['label']} {row['scenario']} iter={row['iteration']} "
            f"trace={row['trace_ms']:.2f}ms lower={row['lower_ms']:.2f}ms "
            f"compile/load={row['compile_or_load_ms']:.2f}ms execute={row['first_execute_ms']:.2f}ms "
            f"cache_files={row['cache_files_after']}"
        )
    return 0


def command_export_cache(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    payload = archive_cache(args.cache_dir)
    args.store_dir.mkdir(parents=True, exist_ok=True)
    out = archive_path(args.store_dir, args.key)
    out.write_bytes(payload)
    files, bytes_on_disk = cache_stats(args.cache_dir)
    meta = {
        "exported": True,
        "key": args.key,
        "archive": str(out),
        "export_ms": (time.perf_counter() - started) * 1000.0,
        "cache_files": files,
        "cache_bytes": bytes_on_disk,
        "archive_bytes": len(payload),
    }
    write_json(args.metadata, meta)
    print(json.dumps(meta, indent=2), flush=True)
    return 0


def command_import_cache(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    src = archive_path(args.store_dir, args.key)
    if not src.exists():
        meta = {
            "imported": False,
            "key": args.key,
            "archive": str(src),
            "import_ms": (time.perf_counter() - started) * 1000.0,
            "cache_files": 0,
            "cache_bytes": 0,
            "archive_bytes": 0,
            "status": "missing",
        }
        write_json(args.metadata, meta)
        print(json.dumps(meta, indent=2), flush=True)
        return 2 if args.require else 0
    payload = src.read_bytes()
    safe_extract_cache(payload, args.cache_dir)
    files, bytes_on_disk = cache_stats(args.cache_dir)
    meta = {
        "imported": True,
        "key": args.key,
        "archive": str(src),
        "import_ms": (time.perf_counter() - started) * 1000.0,
        "cache_files": files,
        "cache_bytes": bytes_on_disk,
        "archive_bytes": len(payload),
        "status": "ok",
    }
    write_json(args.metadata, meta)
    print(json.dumps(meta, indent=2), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile = subparsers.add_parser("profile")
    profile.add_argument("--out", type=Path, required=True)
    profile.add_argument("--scenario", action="append", required=True)
    profile.set_defaults(func=command_profile)

    key = subparsers.add_parser("cache-key")
    key.add_argument("--profile", type=Path, required=True)
    key.set_defaults(func=command_cache_key)

    measure = subparsers.add_parser("measure")
    measure.add_argument("--profile", type=Path, required=True)
    measure.add_argument("--label", required=True)
    measure.add_argument("--csv", type=Path, required=True)
    measure.add_argument("--cache-dir", type=Path)
    measure.add_argument("--hlo-dir", type=Path)
    measure.add_argument("--iteration", type=int, default=1)
    measure.add_argument("--executions", type=int, default=3)
    measure.add_argument("--append", action="store_true")
    measure.add_argument("--import-meta", type=Path)
    measure.add_argument("--export-meta", type=Path)
    measure.add_argument("--explain-cache", action="store_true")
    measure.add_argument("--split-trace", action="store_true")
    measure.set_defaults(func=command_measure)

    export_cache = subparsers.add_parser("export-cache")
    export_cache.add_argument("--cache-dir", type=Path, required=True)
    export_cache.add_argument("--store-dir", type=Path, required=True)
    export_cache.add_argument("--key", required=True)
    export_cache.add_argument("--metadata", type=Path, required=True)
    export_cache.set_defaults(func=command_export_cache)

    import_cache = subparsers.add_parser("import-cache")
    import_cache.add_argument("--cache-dir", type=Path, required=True)
    import_cache.add_argument("--store-dir", type=Path, required=True)
    import_cache.add_argument("--key", required=True)
    import_cache.add_argument("--metadata", type=Path, required=True)
    import_cache.add_argument("--require", action="store_true")
    import_cache.set_defaults(func=command_import_cache)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
