"""Tests for app_refresh module: force_new_ecs_deployment."""

from unittest.mock import MagicMock, patch

from credential_rotation.app_refresh import force_new_ecs_deployment


class TestForceNewEcsDeployment:
    @patch("credential_rotation.app_refresh.boto3.client")
    def test_calls_update_service_and_waits(self, mock_client_ctor):
        ecs = MagicMock()
        mock_client_ctor.return_value = ecs

        force_new_ecs_deployment("us-east-1", "my-cluster", "my-service")

        mock_client_ctor.assert_called_once_with("ecs", region_name="us-east-1")
        ecs.update_service.assert_called_once_with(cluster="my-cluster", service="my-service", forceNewDeployment=True)
        ecs.get_waiter.assert_called_once_with("services_stable")
        waiter = ecs.get_waiter.return_value
        waiter.wait.assert_called_once_with(
            cluster="my-cluster",
            services=["my-service"],
            WaiterConfig={"Delay": 15, "MaxAttempts": 120},
        )

    @patch("credential_rotation.app_refresh.boto3.client")
    def test_propagates_update_service_error(self, mock_client_ctor):
        ecs = MagicMock()
        mock_client_ctor.return_value = ecs
        ecs.update_service.side_effect = Exception("AccessDenied")

        try:
            force_new_ecs_deployment("us-west-2", "c", "s")
            assert False, "Expected exception"
        except Exception as e:
            assert "AccessDenied" in str(e)

        ecs.get_waiter.assert_not_called()

    @patch("credential_rotation.app_refresh.boto3.client")
    def test_propagates_waiter_timeout(self, mock_client_ctor):
        ecs = MagicMock()
        mock_client_ctor.return_value = ecs
        waiter = MagicMock()
        ecs.get_waiter.return_value = waiter
        waiter.wait.side_effect = Exception("Max attempts exceeded")

        try:
            force_new_ecs_deployment("eu-west-1", "c", "s")
            assert False, "Expected exception"
        except Exception as e:
            assert "Max attempts" in str(e)
