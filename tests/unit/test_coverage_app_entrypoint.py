"""Tests for the CDK entry point helpers and main() wiring in app.py."""

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import aws_cdk as cdk
import pytest

_APP_FILE = Path(__file__).resolve().parents[2] / "app.py"
RUN_ID = "run-abc123"


@pytest.fixture
def app_module():
    spec = importlib.util.spec_from_file_location("openemr_cdk_app_entrypoint", _APP_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestStackIdForLiveE2E:
    def test_normal_deployment_keeps_historical_name(self, app_module):
        assert app_module.stack_id_for_live_e2e(None) == "OpenemrEcsStack"

    def test_live_run_uses_hashed_isolated_name(self, app_module):
        expected = "OpenemrE2E-" + hashlib.sha256(RUN_ID.encode("utf-8")).hexdigest()[:12]
        assert app_module.stack_id_for_live_e2e(RUN_ID) == expected

    @pytest.mark.parametrize("run_id", ["short", "UPPERCASE-run", "-leading-hyphen", "x" * 49, "under_score1"])
    def test_rejects_malformed_run_ids(self, app_module, run_id):
        with pytest.raises(ValueError, match="6-48 lowercase"):
            app_module.stack_id_for_live_e2e(run_id)


class TestAssertLiveE2ERunnerContext:
    def test_no_run_id_is_always_allowed(self, app_module, monkeypatch):
        monkeypatch.delenv(app_module.LIVE_E2E_RUNNER_ENVIRONMENT, raising=False)
        assert app_module.assert_live_e2e_runner_context(None) is None

    def test_matching_runner_environment_is_allowed(self, app_module, monkeypatch):
        monkeypatch.setenv(app_module.LIVE_E2E_RUNNER_ENVIRONMENT, RUN_ID)
        assert app_module.assert_live_e2e_runner_context(RUN_ID) is None

    @pytest.mark.parametrize("runner_value", [None, "other-run-id"])
    def test_rejects_run_id_outside_guarded_runner(self, app_module, monkeypatch, runner_value):
        if runner_value is None:
            monkeypatch.delenv(app_module.LIVE_E2E_RUNNER_ENVIRONMENT, raising=False)
        else:
            monkeypatch.setenv(app_module.LIVE_E2E_RUNNER_ENVIRONMENT, runner_value)
        with pytest.raises(ValueError, match="reserved for tools.live_e2e"):
            app_module.assert_live_e2e_runner_context(RUN_ID)


class TestBindLiveE2EAvailabilityZoneContext:
    def _bind(self, app_module, app, **overrides):
        kwargs = {
            "live_e2e_run_id": RUN_ID,
            "raw_availability_zones": '["us-west-2a", "us-west-2b"]',
            "account": "123456789012",
            "region": "us-west-2",
        }
        kwargs.update(overrides)
        app_module.bind_live_e2e_availability_zone_context(app, **kwargs)

    def test_normal_deployment_sets_no_context(self, app_module):
        app = cdk.App()
        self._bind(app_module, app, live_e2e_run_id=None, raw_availability_zones=None)
        assert app.node.try_get_context("availability-zones:account=123456789012:region=us-west-2") is None

    def test_zones_without_run_id_are_rejected(self, app_module):
        with pytest.raises(ValueError, match="reserved for live E2E runs"):
            self._bind(app_module, cdk.App(), live_e2e_run_id=None)

    @pytest.mark.parametrize(
        ("account", "region"),
        [(None, "us-west-2"), ("12345", "us-west-2"), ("123456789012", None)],
    )
    def test_requires_concrete_account_and_region(self, app_module, account, region):
        with pytest.raises(ValueError, match="concrete AWS account and Region"):
            self._bind(app_module, cdk.App(), account=account, region=region)

    def test_rejects_non_json_zones(self, app_module):
        with pytest.raises(ValueError, match="must be a JSON array"):
            self._bind(app_module, cdk.App(), raw_availability_zones="us-west-2a,us-west-2b")

    @pytest.mark.parametrize(
        "zones",
        [
            '{"a": "us-west-2a"}',
            '["us-west-2a"]',
            '["us-west-2a", "us-west-2a"]',
            '["us-east-1a", "us-east-1b"]',
            '["us-west-2a", 7]',
            '["us-west-2-lax-1a", "us-west-2b"]',
        ],
    )
    def test_rejects_invalid_zone_lists(self, app_module, zones):
        with pytest.raises(ValueError, match="two unique standard Availability Zones"):
            self._bind(app_module, cdk.App(), raw_availability_zones=zones)

    @pytest.mark.parametrize("zones", ['["us-west-2a", "us-west-2c"]', ["us-west-2a", "us-west-2c"]])
    def test_binds_validated_zones_to_cdk_provider_cache_key(self, app_module, zones):
        app = cdk.App()
        self._bind(app_module, app, raw_availability_zones=zones)
        assert app.node.try_get_context("availability-zones:account=123456789012:region=us-west-2") == [
            "us-west-2a",
            "us-west-2c",
        ]


class TestMain:
    def _run_main(self, app_module, monkeypatch, tmp_path, context):
        real_app = cdk.App
        created = {}

        def make_app():
            created["app"] = real_app(outdir=str(tmp_path), context=context)
            return created["app"]

        stack_cls = MagicMock()
        monkeypatch.setattr(app_module.cdk, "App", make_app)
        monkeypatch.setattr(app_module, "OpenemrEcsStack", stack_cls)
        monkeypatch.setenv("CDK_DEFAULT_ACCOUNT", "123456789012")
        monkeypatch.setenv("CDK_DEFAULT_REGION", "us-west-2")
        app_module.main()
        return created["app"], stack_cls

    def test_normal_synthesis_uses_historical_stack_and_cli_environment(self, app_module, monkeypatch, tmp_path):
        app, stack_cls = self._run_main(app_module, monkeypatch, tmp_path, {})

        stack_cls.assert_called_once()
        args, kwargs = stack_cls.call_args
        assert args == (app, "OpenemrEcsStack")
        assert kwargs["stack_name"] == "OpenemrEcsStack"
        assert kwargs["env"].account == "123456789012"
        assert kwargs["env"].region == "us-west-2"
        assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["version"]

    def test_live_e2e_synthesis_binds_zones_and_isolates_stack(self, app_module, monkeypatch, tmp_path):
        monkeypatch.setenv(app_module.LIVE_E2E_RUNNER_ENVIRONMENT, RUN_ID)
        context = {"live_e2e_run_id": RUN_ID, "live_e2e_availability_zones": '["us-west-2a", "us-west-2b"]'}

        app, stack_cls = self._run_main(app_module, monkeypatch, tmp_path, context)

        expected_id = app_module.stack_id_for_live_e2e(RUN_ID)
        args, kwargs = stack_cls.call_args
        assert args == (app, expected_id)
        assert kwargs["stack_name"] == expected_id
        assert app.node.try_get_context("availability-zones:account=123456789012:region=us-west-2") == [
            "us-west-2a",
            "us-west-2b",
        ]

    def test_live_e2e_context_outside_runner_fails_before_stack_creation(self, app_module, monkeypatch, tmp_path):
        monkeypatch.delenv(app_module.LIVE_E2E_RUNNER_ENVIRONMENT, raising=False)
        with pytest.raises(ValueError, match="reserved for tools.live_e2e"):
            self._run_main(app_module, monkeypatch, tmp_path, {"live_e2e_run_id": RUN_ID})
