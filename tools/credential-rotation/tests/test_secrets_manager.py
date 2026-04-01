"""Tests for secrets_manager module: slot selection, password generation, SlotSecretState."""

import pytest
from credential_rotation.secrets_manager import (
    SecretsManagerSlots,
    SlotSecretState,
    generate_password,
)


class TestStandbySlot:
    def test_a_returns_b(self):
        assert SecretsManagerSlots.standby_slot("A") == "B"

    def test_b_returns_a(self):
        assert SecretsManagerSlots.standby_slot("B") == "A"

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid active slot"):
            SecretsManagerSlots.standby_slot("C")


class TestSlotSecretState:
    def _make_state(self, active="A"):
        return SlotSecretState(
            secret_arn="arn:aws:secretsmanager:us-east-1:123:secret:test",
            payload={
                "active_slot": active,
                "A": {"host": "db-a", "username": "u_a", "password": "p_a", "dbname": "db", "port": "3306"},
                "B": {"host": "db-b", "username": "u_b", "password": "p_b", "dbname": "db", "port": "3306"},
            },
        )

    def test_active_slot_returns_correct(self):
        state = self._make_state("A")
        assert state.active_slot == "A"

    def test_active_slot_b(self):
        state = self._make_state("B")
        assert state.active_slot == "B"

    def test_invalid_active_slot_raises(self):
        state = SlotSecretState(secret_arn="arn", payload={"active_slot": "X"})
        with pytest.raises(ValueError, match="Invalid or missing active_slot"):
            _ = state.active_slot

    def test_missing_active_slot_raises(self):
        state = SlotSecretState(secret_arn="arn", payload={})
        with pytest.raises(ValueError):
            _ = state.active_slot

    def test_slot_returns_dict(self):
        state = self._make_state()
        slot_a = state.slot("A")
        assert slot_a["host"] == "db-a"

    def test_missing_slot_raises(self):
        state = self._make_state()
        with pytest.raises(ValueError, match="Slot C missing"):
            state.slot("C")


class TestGeneratePassword:
    def test_default_length(self):
        pw = generate_password()
        assert len(pw) == 30

    def test_custom_length(self):
        pw = generate_password(length=50)
        assert len(pw) == 50

    def test_alphanumeric_only(self):
        pw = generate_password(length=100)
        assert pw.isalnum()

    def test_uniqueness(self):
        passwords = {generate_password() for _ in range(100)}
        assert len(passwords) == 100
