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


class ModelctlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patchers = [
            mock.patch.object(modelctl, "DATA_ROOT", root),
            mock.patch.object(modelctl, "STATE_ROOT", root / "state"),
            mock.patch.object(modelctl, "GGUF_ROOT", root / "models" / "gguf"),
            mock.patch.object(modelctl, "HF_ROOT", root / "huggingface"),
            mock.patch.object(modelctl, "LOCK_FILE", root / "state" / "modelctl.lock"),
            mock.patch.object(modelctl, "STATE_FILE", root / "state" / "active.json"),
            mock.patch.object(modelctl, "ROUTES_FILE", root / "state" / "litellm-config.yaml"),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_routes_contain_only_active_models_and_default_alias(self) -> None:
        rendered = modelctl.render_routes(["qwen3.6-35b"], "qwen3.6-35b")
        self.assertIn('model_name: "qwen3.6-35b"', rendered)
        self.assertIn('model_name: "local/default"', rendered)
        self.assertNotIn("qwen3.5-4b-gguf", rendered)
        self.assertNotIn("deepseek-v4-flash", rendered)

    def test_pending_pair_behaves_exclusively(self) -> None:
        desired = modelctl.desired_activation(["qwen3.6-35b"], "qwen3.5-4b-gguf")
        self.assertEqual(desired, ["qwen3.5-4b-gguf"])

    def test_passed_pair_can_run_together(self) -> None:
        policy = json.loads(json.dumps(modelctl.POLICY))
        policy["tested_allowlist"][0]["status"] = "passed"
        with mock.patch.object(modelctl, "POLICY", policy):
            desired = modelctl.desired_activation(["qwen3.6-35b"], "qwen3.5-4b-gguf")
        self.assertEqual(desired, ["qwen3.6-35b", "qwen3.5-4b-gguf"])

    def test_ds4_is_always_exclusive(self) -> None:
        desired = modelctl.desired_activation(
            ["qwen3.6-35b", "qwen3.5-4b-gguf"], "deepseek-v4-flash"
        )
        self.assertEqual(desired, ["deepseek-v4-flash"])

    def test_deactivating_ds4_restores_qwen(self) -> None:
        modelctl.save_state(
            {
                "schema": 1,
                "active": ["deepseek-v4-flash"],
                "default": "deepseek-v4-flash",
            }
        )
        with mock.patch.object(modelctl, "transition", return_value={}) as transition:
            modelctl.deactivate("deepseek-v4-flash")
        transition.assert_called_once_with(["qwen3.6-35b"], "qwen3.6-35b")

    def test_budget_reserves_eight_gib(self) -> None:
        tiny = {"MemTotal": 116 * 1024**3, "MemAvailable": 116 * 1024**3}
        with mock.patch.object(modelctl, "meminfo", return_value=tiny):
            with self.assertRaisesRegex(modelctl.ModelctlError, "reserve exceeds"):
                modelctl.check_budget(["deepseek-v4-flash"], check_available=False)

    def test_over_budget_transition_preserves_previous_state(self) -> None:
        previous = {
            "schema": 1,
            "active": ["qwen3.6-35b"],
            "default": "qwen3.6-35b",
        }
        modelctl.save_state(previous)
        with mock.patch.object(
            modelctl,
            "check_budget",
            side_effect=modelctl.ModelctlError("over budget"),
        ):
            with self.assertRaisesRegex(modelctl.ModelctlError, "over budget"):
                modelctl.transition(["deepseek-v4-flash"], "deepseek-v4-flash")
        self.assertEqual(modelctl.load_state()["active"], ["qwen3.6-35b"])

    def test_atomic_write_replaces_complete_file(self) -> None:
        target = Path(self.temp.name) / "state" / "value.json"
        modelctl.atomic_write(target, '{"value": 1}\n')
        modelctl.atomic_write(target, '{"value": 2}\n')
        self.assertEqual(target.read_text(), '{"value": 2}\n')
        self.assertFalse(list(target.parent.glob(".value.json.*")))

    def test_gateway_failure_restores_previous_state_and_routes(self) -> None:
        previous = {
            "schema": 1,
            "active": ["qwen3.6-35b"],
            "default": "qwen3.6-35b",
        }
        modelctl.save_state(previous)
        modelctl.atomic_write(modelctl.ROUTES_FILE, "old-routes\n")
        events: list[tuple[str, str]] = []

        def start(name: str) -> None:
            events.append(("start", name))

        def stop(name: str) -> None:
            events.append(("stop", name))

        with (
            mock.patch.object(modelctl, "verify_one", return_value={"ok": True}),
            mock.patch.object(modelctl, "check_budget"),
            mock.patch.object(modelctl, "backend_start", side_effect=start),
            mock.patch.object(modelctl, "backend_stop", side_effect=stop),
            mock.patch.object(modelctl, "wait_healthy"),
            mock.patch.object(modelctl, "url_ok", return_value=True),
            mock.patch.object(
                modelctl,
                "gateway_reload",
                side_effect=[modelctl.ModelctlError("gateway failed"), None],
            ),
        ):
            with self.assertRaisesRegex(modelctl.ModelctlError, "previous state was restored"):
                modelctl.transition(["qwen3.5-4b-gguf"], "qwen3.5-4b-gguf")

        restored = modelctl.load_state()
        self.assertEqual(restored["active"], ["qwen3.6-35b"])
        self.assertEqual(restored["default"], "qwen3.6-35b")
        self.assertEqual(modelctl.ROUTES_FILE.read_text(), "old-routes\n")
        self.assertIn(("stop", "qwen3.6-35b"), events)
        self.assertIn(("stop", "qwen3.5-4b-gguf"), events)
        self.assertIn(("start", "qwen3.6-35b"), events)

    def test_transition_reconciles_persisted_active_backend_after_reboot(self) -> None:
        modelctl.save_state(
            {
                "schema": 1,
                "active": ["qwen3.6-35b"],
                "default": "qwen3.6-35b",
            }
        )
        with (
            mock.patch.object(modelctl, "verify_one", return_value={"ok": True}),
            mock.patch.object(modelctl, "check_budget"),
            mock.patch.object(modelctl, "backend_start") as start,
            mock.patch.object(modelctl, "wait_healthy"),
            mock.patch.object(modelctl, "gateway_reload"),
        ):
            modelctl.transition(["qwen3.6-35b"], "qwen3.6-35b")
        start.assert_called_once_with("qwen3.6-35b")


if __name__ == "__main__":
    unittest.main()
