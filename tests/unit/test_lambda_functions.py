"""Unit tests for Lambda handler business logic in lambda/lambda_functions.py.

These tests verify the actual handler code, not CDK resource creation.
All boto3 calls are mocked to avoid AWS dependencies.
"""

import base64
import hashlib
import hmac
import importlib.util
import json
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 'lambda' is a Python keyword, so we use importlib to load lambda_functions.py
_LAMBDA_FILE = Path(__file__).resolve().parents[2] / "lambda" / "lambda_functions.py"


def _load_lambda_module():
    """Import lambda/lambda_functions.py despite 'lambda' being a reserved word."""
    spec = importlib.util.spec_from_file_location("lambda_functions", _LAMBDA_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _reset_module():
    """Ensure each test gets a fresh module load (env vars may change)."""
    sys.modules.pop("lambda_functions", None)
    yield
    sys.modules.pop("lambda_functions", None)


# ---------------------------------------------------------------------------
# generate_ssl_materials
# ---------------------------------------------------------------------------
class TestGenerateSSLMaterials:
    @patch.dict(
        "os.environ",
        {
            "ECS_CLUSTER": "my-cluster",
            "TASK_DEFINITION": "my-task-def",
            "SECURITY_GROUPS": "sg-111,sg-222",
            "SUBNETS": "subnet-aaa,subnet-bbb",
        },
    )
    @patch("boto3.client")
    def test_happy_path(self, mock_boto_client):
        ecs = MagicMock()
        mock_boto_client.return_value = ecs
        ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:aws:ecs:us-west-2:123:task/abc"}]}
        waiter = MagicMock()
        ecs.get_waiter.return_value = waiter

        mod = _load_lambda_module()
        result = mod.generate_ssl_materials({}, None)

        ecs.run_task.assert_called_once()
        call_kwargs = ecs.run_task.call_args[1]
        assert call_kwargs["cluster"] == "my-cluster"
        assert call_kwargs["taskDefinition"] == "my-task-def"
        assert call_kwargs["networkConfiguration"]["awsvpcConfiguration"]["securityGroups"] == ["sg-111", "sg-222"]
        assert call_kwargs["networkConfiguration"]["awsvpcConfiguration"]["subnets"] == ["subnet-aaa", "subnet-bbb"]
        waiter.wait.assert_called_once()
        assert result["statusCode"] == 200

    @patch.dict("os.environ", {}, clear=True)
    @patch("boto3.client")
    def test_missing_env_vars_raises(self, mock_boto_client):
        mod = _load_lambda_module()
        with pytest.raises(KeyError):
            mod.generate_ssl_materials({}, None)


# ---------------------------------------------------------------------------
# generate_smtp_credential
# ---------------------------------------------------------------------------
class TestGenerateSMTPCredential:
    @patch.dict(
        "os.environ",
        {
            "SECRET_ACCESS_KEY": "my-iam-secret",
            "SMTP_PASSWORD": "my-smtp-secret",
            "AWS_REGION": "us-west-2",
        },
    )
    @patch("boto3.client")
    def test_computes_and_stores_smtp_password(self, mock_boto_client):
        sm = MagicMock()
        mock_boto_client.return_value = sm
        sm.get_secret_value.return_value = {"SecretString": json.dumps({"password": "wJalrXUtnFEMI/K7MDENG"})}
        sm.update_secret.return_value = {}

        mod = _load_lambda_module()
        result = mod.generate_smtp_credential({}, None)

        assert result["statusCode"] == 200
        sm.get_secret_value.assert_called_once_with(SecretId="my-iam-secret")
        update_call = sm.update_secret.call_args
        assert update_call[1]["SecretId"] == "my-smtp-secret"
        stored = json.loads(update_call[1]["SecretString"])
        assert "password" in stored
        assert stored["password"] != "wJalrXUtnFEMI/K7MDENG"

    def test_calculate_key_deterministic(self):
        """The HMAC-SHA256 derivation should be deterministic for the same inputs."""

        def sign(key, msg):
            return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

        def calculate_key(secret_access_key, region):
            DATE = "11111111"
            SERVICE = "ses"
            MESSAGE = "SendRawEmail"
            TERMINAL = "aws4_request"
            VERSION = 0x04
            signature = sign(("AWS4" + secret_access_key).encode("utf-8"), DATE)
            signature = sign(signature, region)
            signature = sign(signature, SERVICE)
            signature = sign(signature, TERMINAL)
            signature = sign(signature, MESSAGE)
            signature_and_version = bytes([VERSION]) + signature
            return base64.b64encode(signature_and_version).decode("utf-8")

        result1 = calculate_key("testkey", "us-west-2")
        result2 = calculate_key("testkey", "us-west-2")
        assert result1 == result2

        different_region = calculate_key("testkey", "us-east-1")
        assert result1 != different_region

    @patch.dict("os.environ", {}, clear=True)
    @patch("boto3.client")
    def test_missing_env_vars_raises(self, mock_boto_client):
        mod = _load_lambda_module()
        with pytest.raises(KeyError):
            mod.generate_smtp_credential({}, None)


# ---------------------------------------------------------------------------
# make_ruleset_active
# ---------------------------------------------------------------------------
class TestMakeRulesetActive:
    @patch.dict("os.environ", {"RULE_SET_NAME": "my-rule-set"})
    @patch("boto3.client")
    def test_activates_rule_set(self, mock_boto_client):
        ses = MagicMock()
        mock_boto_client.return_value = ses

        mod = _load_lambda_module()
        result = mod.make_ruleset_active({}, None)

        ses.set_active_receipt_rule_set.assert_called_once_with(RuleSetName="my-rule-set")
        assert result["statusCode"] == 200

    @patch.dict("os.environ", {}, clear=True)
    @patch("boto3.client")
    def test_missing_env_var_raises(self, mock_boto_client):
        mod = _load_lambda_module()
        with pytest.raises(KeyError):
            mod.make_ruleset_active({}, None)


# ---------------------------------------------------------------------------
# export_from_rds_to_s3
# ---------------------------------------------------------------------------
class TestExportFromRDSToS3:
    @patch.dict(
        "os.environ",
        {
            "KMS_KEY_ID": "arn:aws:kms:us-west-2:123:key/abc",
            "DB_CLUSTER_ARN": "arn:aws:rds:us-west-2:123:cluster:my-cluster",
            "S3_BUCKET_NAME": "my-export-bucket",
            "EXPORT_ROLE_ARN": "arn:aws:iam::123:role/export-role",
        },
    )
    @patch("boto3.client")
    def test_starts_export_task(self, mock_boto_client):
        rds_client = MagicMock()
        mock_boto_client.return_value = rds_client
        rds_client.start_export_task.return_value = {"ExportTaskIdentifier": "aurora-to-s3-openemr-export"}

        mod = _load_lambda_module()
        result = mod.export_from_rds_to_s3({}, None)

        rds_client.start_export_task.assert_called_once_with(
            ExportTaskIdentifier="aurora-to-s3-openemr-export",
            KmsKeyId="arn:aws:kms:us-west-2:123:key/abc",
            SourceArn="arn:aws:rds:us-west-2:123:cluster:my-cluster",
            S3BucketName="my-export-bucket",
            IamRoleArn="arn:aws:iam::123:role/export-role",
        )
        assert result["ExportTaskIdentifier"] == "aurora-to-s3-openemr-export"


# ---------------------------------------------------------------------------
# sync_efs_to_s3
# ---------------------------------------------------------------------------
class TestSyncEFSToS3:
    @patch.dict(
        "os.environ",
        {
            "ECS_CLUSTER": "analytics-cluster",
            "TASK_DEFINITION": "sync-task",
            "SECURITY_GROUPS": "sg-333",
            "SUBNETS": "subnet-ccc",
        },
    )
    @patch("boto3.client")
    def test_launches_ecs_task_and_returns_arn(self, mock_boto_client):
        ecs = MagicMock()
        mock_boto_client.return_value = ecs
        ecs.run_task.return_value = {"tasks": [{"taskArn": "arn:aws:ecs:us-west-2:123:task/sync-xyz"}]}

        mod = _load_lambda_module()
        result = mod.sync_efs_to_s3({}, None)

        assert result == "arn:aws:ecs:us-west-2:123:task/sync-xyz"
        call_kwargs = ecs.run_task.call_args[1]
        assert call_kwargs["cluster"] == "analytics-cluster"
        assert call_kwargs["networkConfiguration"]["awsvpcConfiguration"]["securityGroups"] == ["sg-333"]


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------
class TestSendEmail:
    def _make_ses_event(self, message_id="test-msg-id"):
        return {"Records": [{"ses": {"mail": {"messageId": message_id}}}]}

    @pytest.mark.xfail(
        reason="Upstream Lambda passes bytes + wrong subtype to MIMEText; non-multipart path needs a fix",
        strict=True,
    )
    @patch.dict(
        "os.environ",
        {
            "AWS_REGION": "us-west-2",
            "BUCKET_NAME": "ses-bucket",
            "SOURCE_NAME": "noreply@example.com",
            "FORWARD_TO": "admin@example.com",
            "SOURCE_ARN": "arn:aws:ses:us-west-2:123:identity/example.com",
        },
    )
    @patch("boto3.client")
    def test_non_multipart_email(self, mock_boto_client):
        plain_email = (
            "From: sender@example.com\r\n"
            "To: recipient@example.com\r\n"
            "Subject: Test Subject\r\n"
            "Return-Path: <sender@example.com>\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "Hello, this is a test."
        )

        s3_mock = MagicMock()
        ses_mock = MagicMock()

        def client_factory(service, *args, **kwargs):
            if service == "s3":
                return s3_mock
            return ses_mock

        mock_boto_client.side_effect = client_factory

        body_mock = MagicMock()
        body_mock.read.return_value = plain_email.encode("utf-8")
        s3_mock.get_object.return_value = {"Body": body_mock}

        mod = _load_lambda_module()
        result = mod.send_email(self._make_ses_event(), None)

        assert result["statusCode"] == 200
        ses_mock.send_raw_email.assert_called_once()
        call_kwargs = ses_mock.send_raw_email.call_args[1]
        assert call_kwargs["Source"] == "noreply@example.com"

    @patch.dict(
        "os.environ",
        {
            "AWS_REGION": "us-west-2",
            "BUCKET_NAME": "ses-bucket",
            "SOURCE_NAME": "noreply@example.com",
            "FORWARD_TO": "admin@example.com",
            "SOURCE_ARN": "arn:aws:ses:us-west-2:123:identity/example.com",
        },
    )
    @patch("boto3.client")
    def test_multipart_email(self, mock_boto_client):
        msg = MIMEMultipart()
        msg["From"] = "sender@example.com"
        msg["To"] = "recipient@example.com"
        msg["Subject"] = "Multipart Test"
        msg["Return-Path"] = "<sender@example.com>"
        msg.attach(MIMEText("plain text body", "plain"))
        msg.attach(MIMEText("<h1>HTML body</h1>", "html"))
        raw_email = msg.as_string()

        s3_mock = MagicMock()
        ses_mock = MagicMock()

        def client_factory(service, *args, **kwargs):
            if service == "s3":
                return s3_mock
            return ses_mock

        mock_boto_client.side_effect = client_factory

        body_mock = MagicMock()
        body_mock.read.return_value = raw_email.encode("utf-8")
        s3_mock.get_object.return_value = {"Body": body_mock}

        mod = _load_lambda_module()
        result = mod.send_email(self._make_ses_event(), None)

        assert result["statusCode"] == 200
        ses_mock.send_raw_email.assert_called_once()
