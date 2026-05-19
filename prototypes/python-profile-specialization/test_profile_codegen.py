#!/usr/bin/env python3
"""Checksum tests for generated Python profile-specialization artifacts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handler
import profile_codegen


class ProfileCodegenTest(unittest.TestCase):
    def test_generated_artifacts_match_generic_handler(self) -> None:
        with tempfile.TemporaryDirectory(prefix="python-profile-codegen-") as raw_tmp:
            tmp = Path(raw_tmp)
            for benchmark in sorted(handler.ROUTES):
                profile_paths = []
                for seed in range(1, 4):
                    checksum, route_counts = handler.run_generic(benchmark, 1200, seed)
                    profile_path = tmp / f"{benchmark}-{seed}.json"
                    handler.write_profile(profile_path, benchmark, 1200, seed, checksum, route_counts)
                    profile_paths.append(profile_path)

                generated = profile_codegen.generate_artifact(*profile_codegen.load_profiles(profile_paths))
                artifact_path = tmp / f"{benchmark}-specialized.py"
                artifact_path.write_text(generated, encoding="utf-8")
                artifact = handler.load_artifact(artifact_path)

                for seed in range(1, 7):
                    with self.subTest(benchmark=benchmark, seed=seed):
                        generic_checksum, _route_counts = handler.run_generic(benchmark, 1200, seed)
                        specialized_checksum = int(artifact.run(1200, seed))
                        self.assertEqual(generic_checksum, specialized_checksum)


if __name__ == "__main__":
    unittest.main()
