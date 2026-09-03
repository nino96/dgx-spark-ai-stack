from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("modelctl_module", str(ROOT / "bin" / "modelctl"))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
modelctl = importlib.util.module_from_spec(spec)
loader.exec_module(modelctl)


def make_catalog(**overrides) -> dict:
    catalog = {
        "defaults": {"host_reserve_gib": 10, "health": {"path": "/v1/models", "timeout_s": 1200}},
        "models": {
            "text-a": {
                "backend": "vllm",
                "slot": "primary",
                "role": "text",
                "boot": True,
                "budget_gib": 60,
                "served_model_name": "text-a",
                "source": {"hf_repo": "org/text-a", "revision": "deadbeef"},
                "experimental": False,
            },
            "text-b": {
                "backend": "vllm",
                "slot": "primary",
                "role": "text",
                "boot": False,
                "budget_gib": 40,
                "served_model_name": "text-b",
                "source": {"hf_repo": "org/text-b", "revision": "deadbeef"},
                "experimental": False,
            },
            "vision-a": {
                "backend": "vllm",
                "slot": "secondary",
                "role": "vision",
                "boot": False,
                "budget_gib": 30,
                "served_model_name": "vision-a",
                "source": {"hf_repo": "org/vision-a", "revision": "deadbeef"},
                "experimental": False,
            },
            "gguf-a": {
                "backend": "llamacpp",
                "slot": "primary",
                "role": "text",
                "boot": False,
                "budget_gib": 90,
                "served_model_name": "gguf-a",
                "source": {
                    "hf_repo": "org/gguf-a",
                    "revision": "deadbeef",
                    "filename": "gguf-a.gguf",
                    "sha256": "a" * 64,
                    "local_path": "/tmp/does-not-matter/gguf-a.gguf",
                },
                "experimental": False,
            },
            "unsized": {
                "backend": "llamacpp-fork",
                "slot": "secondary",
                "role": "text",
                "boot": False,
                "budget_gib": None,
                "served_model_name": "unsized",
                "source": {"hf_repo": "org/unsized", "revision": None},
                "experimental": False,
            },
            "exp-model": {
                "backend": "ds4",
                "slot": "primary",
                "role": "text",
                "boot": False,
                "budget_gib": 90,
                "served_model_name": "exp-model",
                "source": {"hf_repo": "org/exp", "revision": "deadbeef"},
                "experimental": True,
            },
        },
        "tested_pairs": [],
    }
    catalog.update(overrides)
    return catalog


class ConcurrencyTests(unittest.TestCase):
    def test_single_model_always_allowed(self) -> None:
        modelctl.validate_concurrency(["text-a"], make_catalog())  # no raise

    def test_distinct_roles_distinct_slots_allowed(self) -> None:
        modelctl.validate_concurrency(["text-a", "vision-a"], make_catalog())  # no raise

    def test_same_role_without_tested_pair_rejected(self) -> None:
        # text-b uses the same slot as text-a in the fixture, so use a role
        # clash with distinct slots to isolate the role-pairing rule.
        catalog = make_catalog()
        catalog["models"]["text-b"]["slot"] = "secondary"
        with self.assertRaisesRegex(modelctl.ModelctlError, "tested_pairs"):
            modelctl.validate_concurrency(["text-a", "text-b"], catalog)

    def test_same_role_with_tested_pair_allowed(self) -> None:
        catalog = make_catalog(tested_pairs=[{"models": ["text-a", "text-b"], "status": "passed"}])
        catalog["models"]["text-b"]["slot"] = "secondary"
        modelctl.validate_concurrency(["text-a", "text-b"], catalog)  # no raise

    def test_same_role_tested_pair_not_yet_passed_rejected(self) -> None:
        catalog = make_catalog(tested_pairs=[{"models": ["text-a", "text-b"], "status": "pending"}])
        catalog["models"]["text-b"]["slot"] = "secondary"
        with self.assertRaisesRegex(modelctl.ModelctlError, "tested_pairs"):
            modelctl.validate_concurrency(["text-a", "text-b"], catalog)

    def test_shared_slot_rejected_even_with_distinct_roles(self) -> None:
        catalog = make_catalog()
        catalog["models"]["vision-a"]["slot"] = "primary"  # collide with text-a
        with self.assertRaisesRegex(modelctl.ModelctlError, "slot"):
            modelctl.validate_concurrency(["text-a", "vision-a"], catalog)

    def test_more_than_two_models_rejected(self) -> None:
        with self.assertRaisesRegex(modelctl.ModelctlError, "at most two"):
            modelctl.validate_concurrency(["text-a", "vision-a", "gguf-a"], make_catalog())

    def test_plain_list_tested_pairs_entry_supported(self) -> None:
        catalog = make_catalog(tested_pairs=[["text-a", "text-b"]])
        catalog["models"]["text-b"]["slot"] = "secondary"
        modelctl.validate_concurrency(["text-a", "text-b"], catalog)  # no raise


class BudgetTests(unittest.TestCase):
    def test_fits_within_baseline(self) -> None:
        with mock.patch.object(modelctl, "load_baseline", return_value={"mem_available_gib": 120.0}):
            modelctl.check_budget(["text-a"], [], make_catalog())  # 60 + 10 <= 120, no raise

    def test_exceeds_baseline_raises(self) -> None:
        with mock.patch.object(modelctl, "load_baseline", return_value={"mem_available_gib": 60.0}):
            with self.assertRaisesRegex(modelctl.ModelctlError, "budget check failed"):
                modelctl.check_budget(["text-a"], [], make_catalog())  # 60 + 10 > 60

    def test_pair_budget_sums_and_fits_at_exact_boundary(self) -> None:
        with mock.patch.object(modelctl, "load_baseline", return_value={"mem_available_gib": 100.0}):
            # 60 + 30 + 10 reserve == 100 available -> fits exactly, no raise.
            modelctl.check_budget(["text-a", "vision-a"], [], make_catalog())

    def test_pair_budget_sums_and_exceeds(self) -> None:
        with mock.patch.object(modelctl, "load_baseline", return_value={"mem_available_gib": 99.0}):
            with self.assertRaisesRegex(modelctl.ModelctlError, "budget check failed"):
                modelctl.check_budget(["text-a", "vision-a"], [], make_catalog())

    def test_no_baseline_falls_back_to_current_plus_active_budgets(self) -> None:
        catalog = make_catalog()
        with (
            mock.patch.object(modelctl, "load_baseline", return_value=None),
            mock.patch.object(modelctl, "mem_available_gib", return_value=20.0),
        ):
            # current 20 GiB available + text-a's already-active 60 GiB budget = 80 effective.
            # Activating text-b (40) + reserve (10) = 50 <= 80 -> should pass.
            modelctl.check_budget(["text-b"], ["text-a"], catalog)

    def test_missing_budget_gib_raises(self) -> None:
        with mock.patch.object(modelctl, "load_baseline", return_value={"mem_available_gib": 200.0}):
            with self.assertRaisesRegex(modelctl.ModelctlError, "no budget_gib"):
                modelctl.check_budget(["unsized"], [], make_catalog())


class CatalogLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.models_file = Path(self.temp.name) / "models.yaml"
        self.experimental_file = Path(self.temp.name) / "experimental.yaml"
        self.patchers = [
            mock.patch.object(modelctl, "MODELS_FILE", self.models_file),
            mock.patch.object(modelctl, "EXPERIMENTAL_FILE", self.experimental_file),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_loads_defaults_and_marks_experimental(self) -> None:
        self.models_file.write_text(
            "defaults:\n  host_reserve_gib: 10\nmodels:\n  m1:\n    backend: vllm\n    slot: primary\n"
            "    role: text\n    boot: true\n    budget_gib: 50\n    served_model_name: m1\n"
            "    source: {hf_repo: org/m1, revision: abc}\ntested_pairs: []\n",
            encoding="utf-8",
        )
        self.experimental_file.write_text(
            "models:\n  m2:\n    backend: ds4\n    slot: primary\n    role: text\n    boot: false\n"
            "    budget_gib: 50\n    served_model_name: m2\n    source: {hf_repo: org/m2, revision: abc}\n",
            encoding="utf-8",
        )
        catalog = modelctl.load_catalog()
        self.assertFalse(catalog["models"]["m1"]["experimental"])
        self.assertTrue(catalog["models"]["m2"]["experimental"])
        self.assertEqual(catalog["defaults"]["host_reserve_gib"], 10)

    def test_missing_experimental_file_is_tolerated(self) -> None:
        self.models_file.write_text(
            "defaults:\n  host_reserve_gib: 10\nmodels:\n  m1:\n    backend: vllm\n    slot: primary\n"
            "    role: text\n    boot: true\n    budget_gib: 50\n    served_model_name: m1\n"
            "    source: {hf_repo: org/m1, revision: abc}\n",
            encoding="utf-8",
        )
        catalog = modelctl.load_catalog()
        self.assertEqual(set(catalog["models"]), {"m1"})

    def test_no_models_raises(self) -> None:
        self.models_file.write_text("defaults: {}\n", encoding="utf-8")
        with self.assertRaisesRegex(modelctl.ModelctlError, "no models defined"):
            modelctl.load_catalog()

    def test_boot_model_name_ignores_experimental_boot_flag(self) -> None:
        catalog = make_catalog()
        catalog["models"]["exp-model"]["boot"] = True  # experimental boot must not win
        self.assertEqual(modelctl.boot_model_name(catalog), "text-a")

    def test_get_model_unknown_raises(self) -> None:
        with self.assertRaisesRegex(modelctl.ModelctlError, "unknown model"):
            modelctl.get_model(make_catalog(), "does-not-exist")


class ActivateRollbackTests(unittest.TestCase):
    """Exercise the activate() transaction with a mocked wrapper-call layer
    (backend_start/backend_stop/gateway_reload/wait_healthy/run_sanity)."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patchers = [
            mock.patch.object(modelctl, "STATE_ROOT", root / "state"),
            mock.patch.object(modelctl, "LOCK_FILE", root / "state" / "modelctl.lock"),
            mock.patch.object(modelctl, "ACTIVE_FILE", root / "state" / "active.json"),
            mock.patch.object(modelctl, "load_catalog", return_value=make_catalog()),
            mock.patch.object(modelctl, "verify_activation_artifacts"),
            mock.patch.object(modelctl, "wait_gateway_ready"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.events: list[tuple[str, str]] = []

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def _record_start(self, name: str, *, experimental: bool) -> None:
        self.events.append(("start", name))

    def _record_stop(self, name: str, *, check: bool = True) -> None:
        self.events.append(("stop", name))

    def test_successful_activation_writes_mirror_state(self) -> None:
        with (
            mock.patch.object(modelctl, "backend_start", side_effect=self._record_start),
            mock.patch.object(modelctl, "backend_stop", side_effect=self._record_stop),
            mock.patch.object(modelctl, "wait_healthy"),
            mock.patch.object(modelctl, "run_sanity"),
            mock.patch.object(modelctl, "gateway_reload") as gateway_reload,
            mock.patch.object(modelctl, "check_budget"),
        ):
            result = modelctl.activate("text-a")
        self.assertEqual(result["active"], ["text-a"])
        gateway_reload.assert_called_once()
        self.assertEqual(modelctl.load_active_state()["active"], ["text-a"])
        self.assertIn(("start", "text-a"), self.events)

    def test_experimental_model_requires_flag(self) -> None:
        with self.assertRaisesRegex(modelctl.ModelctlError, "experimental"):
            modelctl.activate("exp-model")

    def test_experimental_model_with_flag_starts_with_flag(self) -> None:
        calls = []

        def record_start(name: str, *, experimental: bool) -> None:
            calls.append((name, experimental))

        with (
            mock.patch.object(modelctl, "backend_start", side_effect=record_start),
            mock.patch.object(modelctl, "backend_stop", side_effect=self._record_stop),
            mock.patch.object(modelctl, "wait_healthy"),
            mock.patch.object(modelctl, "run_sanity"),
            mock.patch.object(modelctl, "gateway_reload"),
            mock.patch.object(modelctl, "check_budget"),
        ):
            modelctl.activate("exp-model", experimental=True)
        self.assertIn(("exp-model", True), calls)

    def test_rollback_on_sanity_failure_restores_previous_state_and_order(self) -> None:
        modelctl.save_active_state(["text-a"])

        def record_start(name: str, *, experimental: bool) -> None:
            self.events.append(("start", name))

        def record_stop(name: str, *, check: bool = True) -> None:
            self.events.append(("stop", name))

        def failing_sanity(name: str, model: dict, *, full: bool) -> None:
            if name == "gguf-a":
                raise modelctl.ModelctlError("sanity failed")

        with (
            mock.patch.object(modelctl, "backend_start", side_effect=record_start),
            mock.patch.object(modelctl, "backend_stop", side_effect=record_stop),
            mock.patch.object(modelctl, "wait_healthy"),
            mock.patch.object(modelctl, "run_sanity", side_effect=failing_sanity),
            mock.patch.object(modelctl, "gateway_reload") as gateway_reload,
            mock.patch.object(modelctl, "check_budget"),
        ):
            with self.assertRaisesRegex(modelctl.ModelctlError, "rolled back"):
                modelctl.activate("gguf-a")

        # Rollback ordering: the newly-started model is stopped, the previous
        # model is restarted, and the gateway is reloaded to restore routes.
        self.assertEqual(
            self.events,
            [
                ("stop", "text-a"),   # to_stop during the (failed) forward transition
                ("start", "gguf-a"),  # to_start during the forward transition
                ("stop", "gguf-a"),   # rollback: stop what we started
                ("start", "text-a"),  # rollback: restart the previous set
            ],
        )
        # The sanity gate fails before the forward gateway-reload is ever
        # reached, so the only call is the one made while rolling back.
        gateway_reload.assert_called_once()
        self.assertEqual(modelctl.load_active_state()["active"], ["text-a"])

    def test_rollback_on_gateway_failure_restores_state(self) -> None:
        modelctl.save_active_state(["text-a"])

        def record_start(name: str, *, experimental: bool) -> None:
            self.events.append(("start", name))

        def record_stop(name: str, *, check: bool = True) -> None:
            self.events.append(("stop", name))

        with (
            mock.patch.object(modelctl, "backend_start", side_effect=record_start),
            mock.patch.object(modelctl, "backend_stop", side_effect=record_stop),
            mock.patch.object(modelctl, "wait_healthy"),
            mock.patch.object(modelctl, "run_sanity"),
            mock.patch.object(
                modelctl,
                "gateway_reload",
                side_effect=[modelctl.ModelctlError("gateway down"), None],
            ),
            mock.patch.object(modelctl, "check_budget"),
        ):
            with self.assertRaisesRegex(modelctl.ModelctlError, "rolled back"):
                modelctl.activate("gguf-a")

        self.assertEqual(modelctl.load_active_state()["active"], ["text-a"])
        self.assertIn(("stop", "gguf-a"), self.events)
        self.assertIn(("start", "text-a"), self.events)

    def test_deactivate_all_stops_everything(self) -> None:
        modelctl.save_active_state(["text-a"])
        with (
            mock.patch.object(modelctl, "backend_stop", side_effect=self._record_stop) as stop,
            mock.patch.object(modelctl, "gateway_reload"),
            mock.patch.object(modelctl, "wait_gateway_ready"),
        ):
            result = modelctl.deactivate("all")
        self.assertEqual(result["active"], [])
        stop.assert_called_once_with("text-a", check=False)
        self.assertEqual(modelctl.load_active_state()["active"], [])


class StateAndAtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patchers = [
            mock.patch.object(modelctl, "STATE_ROOT", root / "state"),
            mock.patch.object(modelctl, "ACTIVE_FILE", root / "state" / "active.json"),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_atomic_write_replaces_complete_file_no_temp_left_behind(self) -> None:
        target = modelctl.STATE_ROOT / "value.json"
        modelctl.atomic_write(target, '{"value": 1}\n')
        modelctl.atomic_write(target, '{"value": 2}\n')
        self.assertEqual(target.read_text(), '{"value": 2}\n')
        self.assertFalse(list(target.parent.glob(".value.json.*")))

    def test_default_state_when_missing(self) -> None:
        self.assertEqual(modelctl.load_active_state()["active"], [])

    def test_save_and_load_round_trip(self) -> None:
        modelctl.save_active_state(["text-a", "vision-a"])
        self.assertEqual(modelctl.load_active_state()["active"], ["text-a", "vision-a"])

    def test_corrupt_state_file_falls_back_to_default(self) -> None:
        modelctl.STATE_ROOT.mkdir(parents=True, exist_ok=True)
        modelctl.ACTIVE_FILE.write_text("not json", encoding="utf-8")
        self.assertEqual(modelctl.load_active_state()["active"], [])


class ArtifactVerificationTests(unittest.TestCase):
    def test_gguf_artifact_missing_fails(self) -> None:
        catalog = make_catalog()
        result = modelctl.verify_one("gguf-a", catalog)
        self.assertFalse(result["ok"])

    def test_gguf_artifact_sha_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gguf-a.gguf"
            path.write_bytes(b"not the real content")
            catalog = make_catalog()
            catalog["models"]["gguf-a"]["source"]["local_path"] = str(path)
            result = modelctl.verify_one("gguf-a", catalog)
            self.assertFalse(result["ok"])
            checks = {c["check"]: c["ok"] for c in result["checks"]}
            self.assertTrue(checks["artifact"])
            self.assertFalse(checks["sha256"])

    def test_gguf_artifact_matching_sha_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gguf-a.gguf"
            path.write_bytes(b"content")
            digest = modelctl.sha256_file(path)
            catalog = make_catalog()
            catalog["models"]["gguf-a"]["source"]["local_path"] = str(path)
            catalog["models"]["gguf-a"]["source"]["sha256"] = digest
            result = modelctl.verify_one("gguf-a", catalog)
            self.assertTrue(result["ok"])

    def test_hf_repo_model_with_unpinned_revision_fails(self) -> None:
        catalog = make_catalog()
        result = modelctl.verify_one("unsized", catalog)
        self.assertFalse(result["ok"])

    def test_verify_activation_artifacts_skips_hf_repo_models(self) -> None:
        # text-a is hf_repo-only (vllm); it has no local_path so it must never
        # be checked against the filesystem during activation.
        modelctl.verify_activation_artifacts(["text-a"], make_catalog())  # no raise


if __name__ == "__main__":
    unittest.main()
