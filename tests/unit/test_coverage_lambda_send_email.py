"""The non-multipart branch of the SES forwarding Lambda."""

import email
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_FILE = Path(__file__).resolve().parents[2] / "lambda" / "lambda_functions.py"

ENVIRONMENT = {
    "AWS_REGION": "us-west-2",
    "BUCKET_NAME": "ses-bucket",
    "SOURCE_NAME": "noreply@example.com",
    "FORWARD_TO": "admin@example.com",
    "SOURCE_ARN": "arn:aws:ses:us-west-2:123456789012:identity/example.com",
}

PLAIN_EMAIL = (
    "From: sender@example.com\r\n"
    "To: inbox@example.com\r\n"
    "Subject: Lab results: patient #42!\r\n"
    "Return-Path: <sender@example.com>\r\n"
    "Content-Type: text/plain\r\n"
    "\r\n"
    "Hello, this is a test."
)


@pytest.fixture
def lambda_module():
    sys.modules.pop("lambda_functions_send_email_cov", None)
    spec = importlib.util.spec_from_file_location("lambda_functions_send_email_cov", _LAMBDA_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@patch.dict("os.environ", ENVIRONMENT)
@patch("boto3.client")
def test_non_multipart_email_is_forwarded_as_eml_attachment(mock_boto_client, lambda_module):
    s3_client = MagicMock()
    ses_client = MagicMock()
    mock_boto_client.side_effect = lambda service, *args, **kwargs: s3_client if service == "s3" else ses_client
    s3_client.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=PLAIN_EMAIL.encode()))}

    result = lambda_module.send_email({"Records": [{"ses": {"mail": {"messageId": "msg-1"}}}]}, None)

    assert result["statusCode"] == 200
    s3_client.get_object.assert_called_once_with(Bucket="ses-bucket", Key="msg-1")
    kwargs = ses_client.send_raw_email.call_args.kwargs
    assert kwargs["Source"] == ENVIRONMENT["SOURCE_NAME"]
    assert kwargs["SourceArn"] == ENVIRONMENT["SOURCE_ARN"]

    forwarded = email.message_from_string(kwargs["RawMessage"]["Data"])
    assert forwarded["Subject"] == "Lab results: patient #42!"
    assert forwarded["From"] == "noreply@example.com"
    assert forwarded["To"] == "admin@example.com"
    assert forwarded["reply-to"] == "<sender@example.com>"
    body, attachment = forwarded.get_payload()
    assert body.get_payload(decode=True).decode() == "Hello, this is a test."
    assert body.get_content_type() == "text/plain"
    assert attachment.get_filename() == "Lab_results_patient_42_.eml"
    assert attachment.get_content_type() == "application/octet-stream"
    assert attachment.get_payload(decode=True) == PLAIN_EMAIL.encode()
