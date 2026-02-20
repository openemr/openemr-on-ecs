from credential_rotation.secrets_manager import SecretsManagerSlots


def test_standby_slot_selection():
    assert SecretsManagerSlots.standby_slot("A") == "B"
    assert SecretsManagerSlots.standby_slot("B") == "A"
