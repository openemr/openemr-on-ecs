"""Secrets Manager slot helpers for credential rotation."""

from __future__ import annotations

import json
import secrets
import string
from dataclasses import dataclass
from typing import Any, Dict

import boto3


@dataclass
class SlotSecretState:
    secret_arn: str
    payload: Dict[str, Any]

    @property
    def active_slot(self) -> str:
        active = self.payload.get("active_slot")
        if active not in ("A", "B"):
            raise ValueError(f"Invalid or missing active_slot in {self.secret_arn}: {active}")
        return active

    def slot(self, name: str) -> Dict[str, Any]:
        data = self.payload.get(name)
        if not isinstance(data, dict):
            raise ValueError(f"Slot {name} missing in {self.secret_arn}")
        return data


class SecretsManagerSlots:
    def __init__(self, region: str):
        self._client = boto3.client("secretsmanager", region_name=region)

    def get_secret(self, secret_id: str) -> SlotSecretState:
        response = self._client.get_secret_value(SecretId=secret_id)
        payload = json.loads(response["SecretString"])
        return SlotSecretState(secret_arn=response["ARN"], payload=payload)

    def put_payload(self, secret_id: str, payload: Dict[str, Any]) -> None:
        self._client.put_secret_value(SecretId=secret_id, SecretString=json.dumps(payload, separators=(",", ":")))

    @staticmethod
    def standby_slot(active_slot: str) -> str:
        if active_slot == "A":
            return "B"
        if active_slot == "B":
            return "A"
        raise ValueError(f"Invalid active slot: {active_slot}")


def generate_password(length: int = 30) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
