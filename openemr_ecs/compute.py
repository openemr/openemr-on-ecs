"""Compute infrastructure: ECS cluster and Fargate services."""

from typing import Optional

from aws_cdk import (
    Duration,
    RemovalPolicy,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_efs as efs
from aws_cdk import aws_elasticloadbalancingv2 as elb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from cdk_nag import NagSuppressions
from constructs import Construct

from .utils import get_resource_suffix, is_true


class ComputeComponents:
    """Creates and manages ECS compute infrastructure.

    This class handles:
    - ECS cluster creation
    - Fargate task definitions
    - OpenEMR service deployment
    - Container health checks
    - Autoscaling configuration
    - SSL/TLS certificate setup in containers
    """

    def __init__(self, scope: Construct):
        """Initialize compute components.

        Args:
            scope: The CDK construct scope
        """
        self.scope = scope
        self.ecs_cluster: Optional[ecs.Cluster] = None
        self.log_group: Optional[logs.LogGroup] = None
        self.kms_key: Optional[kms.Key] = None
        self.ecs_exec_group: Optional[logs.LogGroup] = None
        self.exec_bucket: Optional[s3.Bucket] = None

    def create_ecs_cluster(self, vpc: ec2.Vpc, db_instance, context: dict, region: str) -> tuple:
        """Create the ECS cluster with optional Exec support and CloudWatch insights.

        Args:
            vpc: The VPC for the ECS cluster
            db_instance: Database instance (for dependency)
            context: CDK context dictionary
            region: AWS region

        Returns:
            Tuple of (ecs_cluster, log_group, kms_key, ecs_exec_group, exec_bucket)
        """
        suffix = get_resource_suffix(context)

        if is_true(context.get("enable_ecs_exec")):
            # Create KMS key for ECS Exec encryption
            # Create KMS key for ECS logs and S3 encryption
            # Set removal policy to DESTROY to schedule key deletion when stack is deleted
            # KMS keys have a mandatory 7-30 day waiting period before actual deletion
            self.kms_key = kms.Key(
                self.scope,
                f"KmsKey-{suffix}",
                enable_key_rotation=True,
                removal_policy=RemovalPolicy.DESTROY,
                pending_window=Duration.days(7),  # Minimum waiting period before key deletion
            )
            self.kms_key.grant_encrypt_decrypt(iam.ServicePrincipal(f"logs.{region}.amazonaws.com"))
            self.kms_key.grant_encrypt_decrypt(iam.ServicePrincipal("s3.amazonaws.com"))

            # Create log group for ECS Exec
            self.ecs_exec_group = logs.LogGroup(self.scope, f"LogGroup-{suffix}", encryption_key=self.kms_key)

            # Create S3 bucket for ECS Exec logs
            self.exec_bucket = s3.Bucket(
                self.scope,
                f"EcsExecBucket-{suffix}",
                auto_delete_objects=True,
                removal_policy=RemovalPolicy.DESTROY,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                encryption_key=self.kms_key,
                enforce_ssl=True,
                versioned=True,
            )

            # Add suppressions for ECS Exec bucket (temporary command output storage)
            NagSuppressions.add_resource_suppressions(
                self.exec_bucket,
                [
                    {
                        "id": "AwsSolutions-S1",
                        "reason": "ECS Exec bucket stores temporary command outputs - server access logging not needed for ephemeral debugging content",
                    },
                    {
                        "id": "HIPAA.Security-S3BucketLoggingEnabled",
                        "reason": "ECS Exec bucket stores temporary command outputs - server access logging not needed for ephemeral debugging content",
                    },
                    {
                        "id": "HIPAA.Security-S3BucketReplicationEnabled",
                        "reason": "ECS Exec bucket stores temporary command outputs - replication not needed for ephemeral debugging content",
                    },
                ],
            )

            # Create cluster with ECS Exec enabled
            self.ecs_cluster = ecs.Cluster(
                self.scope,
                f"ecs-cluster-{suffix}",
                vpc=vpc,
                container_insights_v2=ecs.ContainerInsights.ENHANCED,
                enable_fargate_capacity_providers=True,
                execute_command_configuration=ecs.ExecuteCommandConfiguration(
                    kms_key=self.kms_key,
                    log_configuration=ecs.ExecuteCommandLogConfiguration(
                        cloud_watch_log_group=self.ecs_exec_group,
                        cloud_watch_encryption_enabled=True,
                        s3_bucket=self.exec_bucket,
                        s3_encryption_enabled=True,
                        s3_key_prefix="exec-command-output",
                    ),
                    logging=ecs.ExecuteCommandLogging.OVERRIDE,
                ),
            )
        else:
            # Create cluster without ECS Exec
            self.ecs_cluster = ecs.Cluster(
                self.scope,
                f"ecs-cluster-{suffix}",
                vpc=vpc,
                container_insights_v2=ecs.ContainerInsights.ENHANCED,
                enable_fargate_capacity_providers=True,
            )

        # Add dependency so cluster is not created before the database
        self.ecs_cluster.node.add_dependency(db_instance)

        # Create log group for container logging with KMS encryption
        kms_key = self.scope.kms_keys.central_key
        self.log_group = logs.LogGroup(
            self.scope,
            "log-group",
            retention=logs.RetentionDays.ONE_WEEK,
            encryption_key=kms_key,
        )

        return (self.ecs_cluster, self.log_group, self.kms_key, self.ecs_exec_group, self.exec_bucket)

    def create_openemr_service(
        self,
        ecs_cluster: ecs.Cluster,
        log_group: logs.LogGroup,
        alb: elb.ApplicationLoadBalancer,
        certificate,
        vpc: ec2.Vpc,
        db_instance,
        db_secret,
        password_secret,
        valkey_endpoint,
        php_valkey_tls_variable,
        mysql_ssl_ca_variable,
        mysql_ssl_enabled_variable,
        mysql_port_var,
        swarm_mode,
        efs_volume_configuration_for_sites_folder: ecs.EfsVolumeConfiguration,
        efs_volume_configuration_for_ssl_folder: ecs.EfsVolumeConfiguration,
        sites_efs: efs.FileSystem,
        ssl_efs: efs.FileSystem,
        valkey_sec_group: ec2.SecurityGroup,
        ecs_task_sec_group: ec2.SecurityGroup,
        openemr_version: str,
        container_port: int,
        context: dict,
        exec_bucket=None,
        ecs_exec_group=None,
        site_addr_oath=None,
        activate_rest_api=None,
        activate_fhir_service=None,
        portal_onsite_two_address=None,
        portal_onsite_two_enable=None,
        ccda_alt_service_enable=None,
        rest_portal_api=None,
        smtp_password=None,
        smtp_user=None,
        smtp_host=None,
        smtp_port=None,
        smtp_secure=None,
        patient_reminder_sender_email=None,
        patient_reminder_sender_name=None,
        practice_return_email_path=None,
    ) -> ecs_patterns.ApplicationLoadBalancedFargateService:
        """Create the ECS Fargate service running OpenEMR.

        This method creates the core application service including:
        - Fargate task definition with OpenEMR container
        - Application Load Balancer integration
        - EFS volume mounts for shared storage and SSL certificates
        - Environment variables and secrets from AWS Secrets Manager
        - Health checks and autoscaling configuration
        - SSL/TLS certificate setup for MySQL and Redis connections

        Args:
            ecs_cluster: The ECS cluster
            log_group: CloudWatch log group for containers
            alb: Application Load Balancer
            certificate: ACM certificate (required)
            vpc: The VPC
            db_instance: RDS database cluster
            db_secret: Database credentials secret
            password_secret: OpenEMR admin password secret
            valkey_endpoint: Valkey endpoint SSM parameter
            php_valkey_tls_variable: PHP Valkey TLS SSM parameter
            mysql_ssl_ca_variable: MySQL SSL CA path SSM parameter
            mysql_ssl_enabled_variable: MySQL SSL enabled SSM parameter
            mysql_port_var: MySQL port SSM parameter
            swarm_mode: Swarm mode SSM parameter
            efs_volume_configuration_for_sites_folder: EFS volume config for sites
            efs_volume_configuration_for_ssl_folder: EFS volume config for SSL
            openemr_version: OpenEMR container version
            container_port: Container port (443 for HTTPS)
            context: CDK context dictionary
            site_addr_oath: Site address OAuth SSM parameter (optional)
            activate_rest_api: REST API activation SSM parameter (optional)
            activate_fhir_service: FHIR service activation SSM parameter (optional)
            portal_onsite_two_address: Patient portal address SSM parameter (optional)
            portal_onsite_two_enable: Patient portal enable SSM parameter (optional)
            ccda_alt_service_enable: CCDA service enable SSM parameter (optional)
            rest_portal_api: REST portal API SSM parameter (optional)
            smtp_password: SMTP password secret (optional)
            smtp_user: SMTP user SSM parameter (optional)
            smtp_host: SMTP host SSM parameter (optional)
            smtp_port: SMTP port SSM parameter (optional)
            smtp_secure: SMTP secure SSM parameter (optional)
            patient_reminder_sender_email: Patient reminder email SSM parameter (optional)
            patient_reminder_sender_name: Patient reminder name SSM parameter (optional)
            practice_return_email_path: Practice return email path SSM parameter (optional)

        Returns:
            The created ApplicationLoadBalancedFargateService
        """
        # Create OpenEMR task definition with configurable CPU and memory
        # Defaults: 2048 CPU units (2 vCPU), 4096 MiB memory
        # These can be customized in cdk.json based on workload requirements
        # Validation is performed in stack.py before this function is called
        cpu_value = context.get("openemr_service_fargate_cpu", 2048)
        memory_value = context.get("openemr_service_fargate_memory", 4096)
        # Handle None values explicitly (if key exists but is None)
        fargate_cpu = int(cpu_value if cpu_value is not None else 2048)
        fargate_memory = int(memory_value if memory_value is not None else 4096)

        openemr_fargate_task_definition = ecs.FargateTaskDefinition(
            self.scope,
            "OpenEMRFargateTaskDefinition",
            cpu=fargate_cpu,
            memory_limit_mib=fargate_memory,
            runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64),
        )

        # Add volumes to task definition
        openemr_fargate_task_definition.add_volume(
            name="SitesFolderVolume", efs_volume_configuration=efs_volume_configuration_for_sites_folder
        )
        openemr_fargate_task_definition.add_volume(
            name="SslFolderVolume", efs_volume_configuration=efs_volume_configuration_for_ssl_folder
        )

        # Container startup command that sets up SSL/TLS certificates for secure connections.
        #
        # This script performs the following critical operations:
        # 1. Downloads RDS CA certificate bundle from AWS trust store (required for MySQL SSL)
        # 2. Downloads Amazon Root CA for Redis/Valkey TLS connections
        # 3. Creates necessary certificate directories with proper permissions
        # 4. Copies MySQL CA certificate to OpenEMR's certificates directory
        # 5. Sets correct file permissions (744) required by OpenEMR
        # 6. Runs openemr.sh to configure and start the application
        #
        # IMPORTANT: The MySQL certificate MUST be available before OpenEMR attempts to connect,
        # otherwise you'll get "ERROR 3159 (HY000): Connections using insecure transport are prohibited"
        # because require_secure_transport=ON is set in the RDS parameter group.
        #
        # OpenEMR automatically detects and uses SSL when the certificate exists at:
        # /var/www/localhost/htdocs/openemr/sites/default/documents/certificates/mysql-ca
        # Define the container startup script commands in a readable list.
        # These commands run sequentially and must all succeed (set -e).
        # They handle environment preparation, certificate management, and application launch.
        # This script is designed to be idempotent and fail-fast with clear error messages.
        startup_commands = [
            # Enable immediate exit on any command failure for robust error handling
            "set -e",
            # Enable verbose output for better debugging
            "set -x",
            # --- Logging Setup ---
            # Add timestamp prefix to all echo statements for better log correlation
            'log() { echo "[$(date +%Y-%m-%d\\ %H:%M:%S)] $*"; }',
            'log "=== OpenEMR Container Startup Script ==="',
            'log "Starting container initialization..."',
            # --- Working Directory Verification ---
            # Verify we're in the correct working directory before proceeding
            'cd /var/www/localhost/htdocs/openemr || { log "ERROR: Failed to change to OpenEMR directory"; exit 1; }',
            'if [ "$PWD" != "/var/www/localhost/htdocs/openemr" ]; then',
            '  log "ERROR: Working directory verification failed. Expected /var/www/localhost/htdocs/openemr, got $PWD"',
            "  exit 1",
            "fi",
            'log "Working directory verified: $PWD"',
            # --- Apache User Verification ---
            # Verify apache user exists before attempting chown operations
            "if ! id apache >/dev/null 2>&1; then",
            '  log "ERROR: Apache user does not exist in container image"',
            "  exit 1",
            "fi",
            'log "Apache user verified"',
            # --- Persistence & EFS Initialization ---
            # If the shared EFS sites/default directory is missing or uninitialized,
            # restore the pristine skeleton from the container image.
            # Verify rsync source exists before attempting restore
            'log "Checking EFS sites directory initialization..."',
            "if [ ! -d /var/www/localhost/htdocs/openemr/sites/default ] || [ ! -f /var/www/localhost/htdocs/openemr/sites/default/sqlconf.php ]; then",
            '  log "EFS sites directory missing or uninitialized, restoring from image..."',
            "  if [ ! -d /swarm-pieces/sites ]; then",
            '    log "ERROR: Source directory /swarm-pieces/sites not found in container image"',
            "    exit 1",
            "  fi",
            "  rsync --owner --group --perms --recursive --links --verbose /swarm-pieces/sites /var/www/localhost/htdocs/openemr/ || {",
            '    log "ERROR: Failed to restore site skeleton from image"',
            "    exit 1",
            "  }",
            '  log "Site skeleton restored successfully"',
            "else",
            '  log "EFS sites directory already initialized"',
            "fi",
            # --- Directory Setup ---
            # Create necessary directory structure for certificates and documents
            # Verify directories were created successfully
            'log "Creating certificate directories..."',
            "mkdir -p /var/www/localhost/htdocs/openemr/sites/default/documents/certificates /root/certs/redis /root/certs/mysql/server || {",
            '  log "ERROR: Failed to create certificate directories"',
            "  exit 1",
            "}",
            "for dir in /var/www/localhost/htdocs/openemr/sites/default/documents/certificates /root/certs/redis /root/certs/mysql/server; do",
            '  if [ ! -d "$dir" ]; then',
            '    log "ERROR: Directory $dir was not created"',
            "    exit 1",
            "  fi",
            "done",
            'log "Certificate directories created and verified"',
            # --- Redis/Valkey TLS Support ---
            # Download the Amazon Root CA required for ElastiCache Valkey TLS connections
            # Add timeout, verify download, and check file integrity
            'log "Downloading Amazon Root CA1 for Redis/Valkey TLS..."',
            'REDIS_CA_URL="https://www.amazontrust.com/repository/AmazonRootCA1.pem"',
            'REDIS_CA_PATH="/root/certs/redis/redis-ca"',
            'if [ ! -f "$REDIS_CA_PATH" ] || [ ! -s "$REDIS_CA_PATH" ]; then',
            '  curl -f --max-time 30 --connect-timeout 10 --retry 3 --retry-delay 2 --retry-connrefused --cacert /swarm-pieces/ssl/certs/ca-certificates.crt -o "$REDIS_CA_PATH" "$REDIS_CA_URL" || {',
            '    log "ERROR: Failed to download Redis CA certificate from $REDIS_CA_URL"',
            "    exit 1",
            "  }",
            '  if [ ! -f "$REDIS_CA_PATH" ] || [ ! -s "$REDIS_CA_PATH" ]; then',
            '    log "ERROR: Redis CA certificate file is missing or empty after download"',
            "    exit 1",
            "  fi",
            "  # Validate certificate is a reasonable size (Amazon Root CA should be ~1-5KB)",
            '  CERT_SIZE=$(wc -c < "$REDIS_CA_PATH")',
            '  if [ "$CERT_SIZE" -lt 500 ] || [ "$CERT_SIZE" -gt 10000 ]; then',
            '    log "ERROR: Redis CA certificate size ($CERT_SIZE bytes) is outside expected range (500-10000 bytes)"',
            "    exit 1",
            "  fi",
            "  # Validate certificate is valid PEM format",
            '  if ! head -n 1 "$REDIS_CA_PATH" | grep -q "BEGIN CERTIFICATE" 2>/dev/null; then',
            '    log "ERROR: Redis CA certificate does not appear to be valid PEM format"',
            "    exit 1",
            "  fi",
            '  log "Redis CA certificate downloaded successfully ($CERT_SIZE bytes) and validated"',
            "else",
            '  log "Redis CA certificate already exists, skipping download"',
            "fi",
            'chown apache "$REDIS_CA_PATH" || { log "ERROR: Failed to set ownership on Redis CA certificate"; exit 1; }',
            'log "Redis CA certificate ready"',
            # --- MySQL/RDS SSL Support ---
            # Download the RDS CA bundle required for Aurora MySQL SSL connections
            # Add timeout, verify download, and check file integrity
            'log "Downloading RDS CA bundle for MySQL SSL..."',
            'MYSQL_CA_URL="https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"',
            'MYSQL_CA_PATH="/root/certs/mysql/server/mysql-ca"',
            'if [ ! -f "$MYSQL_CA_PATH" ] || [ ! -s "$MYSQL_CA_PATH" ]; then',
            '  curl -f --max-time 30 --connect-timeout 10 --retry 3 --retry-delay 2 --retry-connrefused --cacert /swarm-pieces/ssl/certs/ca-certificates.crt -o "$MYSQL_CA_PATH" "$MYSQL_CA_URL" || {',
            '    log "ERROR: Failed to download MySQL CA certificate from $MYSQL_CA_URL"',
            "    exit 1",
            "  }",
            '  if [ ! -f "$MYSQL_CA_PATH" ] || [ ! -s "$MYSQL_CA_PATH" ]; then',
            '    log "ERROR: MySQL CA certificate file is missing or empty after download"',
            "    exit 1",
            "  fi",
            "  # Validate certificate bundle is a reasonable size (RDS CA bundle should be ~100-500KB)",
            '  CERT_SIZE=$(wc -c < "$MYSQL_CA_PATH")',
            '  if [ "$CERT_SIZE" -lt 10000 ] || [ "$CERT_SIZE" -gt 1000000 ]; then',
            '    log "ERROR: MySQL CA certificate bundle size ($CERT_SIZE bytes) is outside expected range (10000-1000000 bytes)"',
            "    exit 1",
            "  fi",
            "  # Validate certificate bundle contains valid PEM format",
            '  if ! head -n 1 "$MYSQL_CA_PATH" | grep -q "BEGIN CERTIFICATE" 2>/dev/null; then',
            '    log "ERROR: MySQL CA certificate bundle does not appear to be valid PEM format"',
            "    exit 1",
            "  fi",
            '  log "MySQL CA certificate bundle downloaded successfully ($CERT_SIZE bytes) and validated"',
            "else",
            '  log "MySQL CA certificate already exists, skipping download"',
            "fi",
            'chown apache "$MYSQL_CA_PATH" || { log "ERROR: Failed to set ownership on MySQL CA certificate"; exit 1; }',
            # --- Certificate Deployment ---
            # Place the MySQL CA where OpenEMR's logic automatically detects and uses it
            # Verify copy operation and file permissions
            'log "Deploying MySQL CA certificate to OpenEMR certificates directory..."',
            'OPENEMR_CA_PATH="/var/www/localhost/htdocs/openemr/sites/default/documents/certificates/mysql-ca"',
            'cp "$MYSQL_CA_PATH" "$OPENEMR_CA_PATH" || {',
            '  log "ERROR: Failed to copy MySQL CA certificate to OpenEMR directory"',
            "  exit 1",
            "}",
            'chown apache "$OPENEMR_CA_PATH" || { log "ERROR: Failed to set ownership on OpenEMR MySQL CA certificate"; exit 1; }',
            'chmod 744 "$OPENEMR_CA_PATH" || { log "ERROR: Failed to set permissions on OpenEMR MySQL CA certificate"; exit 1; }',
            'if [ ! -f "$OPENEMR_CA_PATH" ] || [ ! -r "$OPENEMR_CA_PATH" ]; then',
            '  log "ERROR: OpenEMR MySQL CA certificate is missing or not readable after deployment"',
            "  exit 1",
            "fi",
            'log "MySQL CA certificate deployed successfully"',
            # --- OpenEMR Bootstrap Reliability (RDS TLS + idempotent retries) ---
            # OpenEMR 8.0.0's devtools library hard-codes `mariadb --skip-ssl` for setting globals.
            # With Aurora's `require_secure_transport=ON`, that causes repeated failures AFTER a partial install,
            # which then leads to "Table already exists" on the next attempt.
            # We patch the library at container startup to remove `--skip-ssl` so the client negotiates TLS.
            'log "Applying OpenEMR bootstrap reliability fixes (RDS TLS + retry safety)..."',
            # Ensure OpenEMR's auto_configure opcache file cache doesn't fail on retries
            'if [ -d "/tmp/php-file-cache" ]; then',
            '  log "Removing stale /tmp/php-file-cache from prior attempt"',
            '  rm -rf "/tmp/php-file-cache" 2>/dev/null || true',
            "fi",
            # Ensure the RDS CA bundle is in the system trust store (helps CLI + PHP DB connections)
            "if command -v update-ca-certificates >/dev/null 2>&1; then",
            '  RDS_CA_DST="/usr/local/share/ca-certificates/rds-global-bundle.crt"',
            '  (cp "$MYSQL_CA_PATH" "$RDS_CA_DST" 2>/dev/null || cp "$OPENEMR_CA_PATH" "$RDS_CA_DST" 2>/dev/null || true)',
            '  update-ca-certificates >/dev/null 2>&1 || log "WARNING: update-ca-certificates failed; relying on app-provided CA paths"',
            "else",
            '  log "WARNING: update-ca-certificates not available; relying on app-provided CA paths"',
            "fi",
            # Patch devtools library to remove explicit `--skip-ssl` flags that break RDS secure transport
            'if [ -f "/root/devtoolsLibrary.source" ]; then',
            '  if grep -q -- "--skip-ssl" /root/devtoolsLibrary.source 2>/dev/null; then',
            '    log "Patching /root/devtoolsLibrary.source: removing --skip-ssl to allow TLS to RDS"',
            "    sed -i 's/ --skip-ssl//g' /root/devtoolsLibrary.source 2>/dev/null || true",
            "  fi",
            "fi",
            # --- Verification ---
            # Verify all critical files and directories exist before proceeding
            'log "Verifying critical files and directories..."',
            'for path in "$REDIS_CA_PATH" "$MYSQL_CA_PATH" "$OPENEMR_CA_PATH" /var/www/localhost/htdocs/openemr/sites/default; do',
            '  if [ ! -e "$path" ]; then',
            '    log "ERROR: Critical path missing: $path"',
            "    exit 1",
            "  fi",
            "done",
            'log "All critical paths verified"',
            # --- Final Preparation ---
            'log "Performing final preparation steps..."',
            # Ensure the primary startup script exists and is executable
            "if [ ! -f ./openemr.sh ]; then",
            '  log "ERROR: openemr.sh not found in working directory"',
            "  exit 1",
            "fi",
            'chmod +x ./openemr.sh || { log "ERROR: Failed to make openemr.sh executable"; exit 1; }',
            'log "openemr.sh is executable"',
            # Add a cron job for graceful Apache restarts (maintenance best practice)
            # Only add if not already present (idempotent)
            'if ! grep -q "httpd -k graceful" /etc/crontabs/root 2>/dev/null; then',
            '  echo "1 23  *   *   *   httpd -k graceful" >> /etc/crontabs/root',
            '  log "Added Apache graceful restart cron job"',
            "else",
            '  log "Apache graceful restart cron job already exists"',
            "fi",
            'log "=== Container initialization complete ==="',
            # --- EFS Mount Verification ---
            # Verify EFS volumes are properly mounted and writable before proceeding
            # This catches mount issues early and prevents silent failures during setup
            'log "Verifying EFS mount points are writable..."',
            'EFS_SITES_TEST_FILE="/var/www/localhost/htdocs/openemr/sites/.efs_write_test"',
            'EFS_SSL_TEST_FILE="/etc/ssl/.efs_write_test"',
            "# Test sites EFS writability",
            'if touch "$EFS_SITES_TEST_FILE" 2>/dev/null && rm -f "$EFS_SITES_TEST_FILE" 2>/dev/null; then',
            '  log "EFS sites mount verified (writable)"',
            "else",
            '  log "ERROR: EFS sites mount is not writable. Cannot proceed with setup."',
            "  exit 1",
            "fi",
            "# Test SSL EFS writability",
            'if touch "$EFS_SSL_TEST_FILE" 2>/dev/null && rm -f "$EFS_SSL_TEST_FILE" 2>/dev/null; then',
            '  log "EFS SSL mount verified (writable)"',
            "else",
            '  log "ERROR: EFS SSL mount is not writable. Cannot proceed with setup."',
            "  exit 1",
            "fi",
            'log "All EFS mounts verified and writable"',
            # --- Database Readiness Check ---
            # Proactively verify database is reachable before OpenEMR attempts setup
            # This uses exponential backoff to avoid overwhelming the database during startup
            # and provides better error messages if database is not ready
            'log "Checking database connectivity..."',
            'if [ -z "$MYSQL_HOST" ] || [ -z "$MYSQL_ROOT_PASS" ]; then',
            '  log "WARNING: Database credentials not available for readiness check, will rely on OpenEMR retry logic"',
            "else",
            "  DB_READY=0",
            "  MAX_ATTEMPTS=30",
            "  INITIAL_DELAY=2",
            "  CURRENT_DELAY=$INITIAL_DELAY",
            "  for attempt in $(seq 1 $MAX_ATTEMPTS); do",
            "    # Use mysqladmin ping if available, otherwise fall back to nc (netcat)",
            "    if command -v mysqladmin >/dev/null 2>&1; then",
            "      # Build mysqladmin command with SSL (always required for RDS)",
            "      # MariaDB's mysqladmin uses --ssl instead of --ssl-mode=REQUIRED",
            "      # Use double quotes for variable expansion and escape inner quotes",
            '      MYSQLADMIN_CMD="mysqladmin ping -h \\"$MYSQL_HOST\\" -u \\"$MYSQL_ROOT_USER\\" -p\\"$MYSQL_ROOT_PASS\\""',
            "      # Always add SSL options for RDS connections (MariaDB-compatible syntax)",
            '      if [ -n "$MYSQL_CA_PATH" ] && [ -f "$MYSQL_CA_PATH" ]; then',
            '        MYSQLADMIN_CMD="$MYSQLADMIN_CMD --ssl --ssl-ca=\\"$MYSQL_CA_PATH\\""',
            "      else",
            '        MYSQLADMIN_CMD="$MYSQLADMIN_CMD --ssl"',
            "      fi",
            '      if eval "$MYSQLADMIN_CMD" 2>&1; then',
            '        log "Database connectivity verified (attempt $attempt/$MAX_ATTEMPTS)"',
            "        DB_READY=1",
            "        break",
            "      fi",
            '    elif command -v nc >/dev/null 2>&1 && [ -n "$MYSQL_PORT" ]; then',
            "      # Fallback: check if port is open (does not verify database is ready, but better than nothing)",
            '      if nc -z -w 3 "$MYSQL_HOST" "${MYSQL_PORT:-3306}" 2>/dev/null; then',
            '        log "Database port is reachable (attempt $attempt/$MAX_ATTEMPTS), assuming ready"',
            "        DB_READY=1",
            "        break",
            "      fi",
            "    else",
            '      log "WARNING: Neither mysqladmin nor nc available for database readiness check"',
            "      DB_READY=1  # Assume ready if we cannot check",
            "      break",
            "    fi",
            '    if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then',
            '      log "Database not ready yet, waiting ${CURRENT_DELAY}s before retry (attempt $attempt/$MAX_ATTEMPTS)..."',
            "      sleep $CURRENT_DELAY",
            "      # Exponential backoff: double delay each attempt, max 60s",
            "      CURRENT_DELAY=$((CURRENT_DELAY * 2))",
            '      if [ "$CURRENT_DELAY" -gt 60 ]; then',
            "        CURRENT_DELAY=60",
            "      fi",
            "    fi",
            "  done",
            '  if [ "$DB_READY" -eq 0 ]; then',
            '    log "WARNING: Database readiness check failed after $MAX_ATTEMPTS attempts"',
            '    log "Database may still be initializing. OpenEMR will retry setup automatically."',
            "  fi",
            "fi",
            # --- Valkey/Redis Connectivity Check ---
            # Verify Valkey cluster is reachable if Redis server is configured
            # This catches connectivity issues early and provides better error messages
            'log "Checking Valkey/Redis connectivity..."',
            'if [ -z "$REDIS_SERVER" ] || [ "$REDIS_SERVER" = "null" ]; then',
            '  log "Valkey/Redis not configured, skipping connectivity check"',
            "else",
            "  # Extract host and port from REDIS_SERVER (format: host:port or host)",
            '  REDIS_HOST="${REDIS_SERVER%%:*}"',
            '  REDIS_PORT="${REDIS_SERVER##*:}"',
            "  # If no colon found, REDIS_PORT will equal REDIS_HOST (entire string), use default port",
            '  if [ "$REDIS_PORT" = "$REDIS_HOST" ] || [ -z "$REDIS_PORT" ]; then',
            '    REDIS_PORT="6379"  # Default Redis port',
            "  fi",
            "  if command -v nc >/dev/null 2>&1; then",
            '    if nc -z -w 3 "$REDIS_HOST" "$REDIS_PORT" 2>/dev/null; then',
            '      log "Valkey/Redis connectivity verified ($REDIS_HOST:$REDIS_PORT)"',
            "    else",
            '      log "WARNING: Valkey/Redis not reachable at $REDIS_HOST:$REDIS_PORT"',
            '      log "Application may have degraded cache functionality, but will continue startup"',
            "    fi",
            "  else",
            '    log "WARNING: nc (netcat) not available for Valkey/Redis connectivity check"',
            "  fi",
            "fi",
            # --- Stale Leader File Cleanup ---
            # Clean up stale docker-leader files that may have been left by failed containers.
            # This addresses the issue where a leader container fails during auto_configure.php,
            # leaving a stale docker-leader file and partially initialized database. When the
            # leader retries or another container becomes leader, auto_configure.php fails with
            # "Table already exists" errors because some tables were created before the failure.
            #
            # Strategy: Check if docker-leader file is older than a threshold (indicating a
            # failed/stuck leader), and if docker-completed doesn't exist, clean it up.
            'log "Checking for stale docker-leader files..."',
            'LEADER_FILE="/var/www/localhost/htdocs/openemr/sites/docker-leader"',
            'COMPLETED_FILE="/var/www/localhost/htdocs/openemr/sites/docker-completed"',
            # If docker-completed exists, setup was successful and we should proceed normally
            'if [ -f "$COMPLETED_FILE" ]; then',
            '  log "Setup completed successfully (docker-completed file exists), proceeding normally"',
            # If docker-leader exists but docker-completed does not, check if it\'s stale
            'elif [ -f "$LEADER_FILE" ]; then',
            '  log "docker-leader file exists but docker-completed does not, checking if leader is stale..."',
            # Use stat to get file modification time (works on Alpine Linux with busybox stat)
            # busybox stat uses -c %Y format (seconds since epoch)
            '  LEADER_MTIME=$(stat -c %Y "$LEADER_FILE" 2>/dev/null || stat -f %m "$LEADER_FILE" 2>/dev/null || echo "")',
            '  if [ -n "$LEADER_MTIME" ] && [ "$LEADER_MTIME" != "0" ]; then',
            "    CURRENT_TIME=$(date +%s)",
            "    AGE_SECONDS=$((CURRENT_TIME - LEADER_MTIME))",
            # Consider leader file stale if it\'s older than 20 minutes (1200 seconds).
            # OpenEMR setup typically completes in 5-15 minutes, so if a leader file is >20min old
            # and docker-completed doesn't exist, the leader likely failed. This provides reasonable
            # recovery time while still allowing healthy setups (which usually complete in 5-8 minutes) to finish.
            '    if [ "$AGE_SECONDS" -gt 1200 ]; then',
            '      log "WARNING: Stale docker-leader file detected (${AGE_SECONDS}s old, >20min). Leader likely failed mid-setup."',
            "      log \"This can cause 'Table already exists' errors. Cleaning up stale leader file...\"",
            '      rm -f "$LEADER_FILE" || log "WARNING: Failed to remove stale leader file, continuing anyway"',
            '      log "Stale leader file cleaned up. Another container can now become leader and handle partial database state."',
            "    else",
            '      log "docker-leader file is recent (${AGE_SECONDS}s old), waiting for leader to complete setup..."',
            "    fi",
            "  else",
            "    # If we can't determine file age (unlikely on Alpine), log but don't remove.",
            "    # The openemr.sh script will handle waiting for completion.",
            '    log "Could not determine age of docker-leader file. Will rely on openemr.sh timeout handling."',
            "  fi",
            "else",
            '  log "No docker-leader file found, this container may become the leader"',
            "fi",
            'log "Handing over to openemr.sh..."',
            # --- Application Launch ---
            # Hand over control to the main OpenEMR entrypoint script
            # Use exec to replace shell process (better signal handling)
            # Note: We cannot trap errors after exec, so cleanup must happen before this point
            "exec ./openemr.sh",
        ]
        # Join commands with newlines instead of && to preserve multi-line shell constructs
        # set -e at the beginning ensures the script exits on any error
        command_array = ["\n".join(startup_commands)]

        # Define secrets
        secrets = {
            "MYSQL_ROOT_USER": ecs.Secret.from_secrets_manager(db_secret, "username"),
            "MYSQL_ROOT_PASS": ecs.Secret.from_secrets_manager(db_secret, "password"),
            "MYSQL_USER": ecs.Secret.from_secrets_manager(db_secret, "username"),
            "MYSQL_PASS": ecs.Secret.from_secrets_manager(db_secret, "password"),
            "MYSQL_HOST": ecs.Secret.from_secrets_manager(db_secret, "host"),
            "MYSQL_PORT": ecs.Secret.from_ssm_parameter(mysql_port_var),
            "MYSQL_SSL_CA": ecs.Secret.from_ssm_parameter(mysql_ssl_ca_variable),
            "MYSQL_SSL": ecs.Secret.from_ssm_parameter(mysql_ssl_enabled_variable),
            "OE_USER": ecs.Secret.from_secrets_manager(password_secret, "username"),
            "OE_PASS": ecs.Secret.from_secrets_manager(password_secret, "password"),
            "REDIS_SERVER": ecs.Secret.from_ssm_parameter(valkey_endpoint),
            "REDIS_TLS": ecs.Secret.from_ssm_parameter(php_valkey_tls_variable),
            "SWARM_MODE": ecs.Secret.from_ssm_parameter(swarm_mode),
        }

        # Add optional secrets based on context
        if is_true(context.get("activate_openemr_apis")) or is_true(context.get("enable_patient_portal")):
            if site_addr_oath:
                secrets["OPENEMR_SETTING_site_addr_oath"] = ecs.Secret.from_ssm_parameter(site_addr_oath)

        if is_true(context.get("activate_openemr_apis")):
            if activate_rest_api:
                secrets["OPENEMR_SETTING_rest_api"] = ecs.Secret.from_ssm_parameter(activate_rest_api)
            if activate_fhir_service:
                secrets["OPENEMR_SETTING_rest_fhir_api"] = ecs.Secret.from_ssm_parameter(activate_fhir_service)

        if is_true(context.get("enable_patient_portal")):
            if portal_onsite_two_address:
                secrets["OPENEMR_SETTING_portal_onsite_two_address"] = ecs.Secret.from_ssm_parameter(
                    portal_onsite_two_address
                )
            if portal_onsite_two_enable:
                secrets["OPENEMR_SETTING_portal_onsite_two_enable"] = ecs.Secret.from_ssm_parameter(
                    portal_onsite_two_enable
                )
            if ccda_alt_service_enable:
                secrets["OPENEMR_SETTING_ccda_alt_service_enable"] = ecs.Secret.from_ssm_parameter(
                    ccda_alt_service_enable
                )
            if rest_portal_api:
                secrets["OPENEMR_SETTING_rest_portal_api"] = ecs.Secret.from_ssm_parameter(rest_portal_api)

        if is_true(context.get("configure_ses")):
            if smtp_password:
                secrets["OPENEMR_SETTING_SMTP_PASS"] = ecs.Secret.from_secrets_manager(smtp_password, "password")
            if smtp_user:
                secrets["OPENEMR_SETTING_SMTP_USER"] = ecs.Secret.from_ssm_parameter(smtp_user)
            if smtp_host:
                secrets["OPENEMR_SETTING_SMTP_HOST"] = ecs.Secret.from_ssm_parameter(smtp_host)
            if smtp_port:
                secrets["OPENEMR_SETTING_SMTP_PORT"] = ecs.Secret.from_ssm_parameter(smtp_port)
            if smtp_secure:
                secrets["OPENEMR_SETTING_SMTP_SECURE"] = ecs.Secret.from_ssm_parameter(smtp_secure)
            if patient_reminder_sender_email:
                secrets["OPENEMR_SETTING_patient_reminder_sender_email"] = ecs.Secret.from_ssm_parameter(
                    patient_reminder_sender_email
                )
            if patient_reminder_sender_name:
                secrets["OPENEMR_SETTING_patient_reminder_sender_name"] = ecs.Secret.from_ssm_parameter(
                    patient_reminder_sender_name
                )
            if practice_return_email_path:
                secrets["OPENEMR_SETTING_practice_return_email_path"] = ecs.Secret.from_ssm_parameter(
                    practice_return_email_path
                )

        # Add OpenEMR container definition
        openemr_container = openemr_fargate_task_definition.add_container(
            "OpenEMRContainer",
            logging=ecs.LogDriver.aws_logs(stream_prefix="ecs/openemr", log_group=log_group),
            port_mappings=[ecs.PortMapping(container_port=container_port)],
            essential=True,
            container_name="openemr",
            working_directory="/var/www/localhost/htdocs/openemr",
            entry_point=["/bin/sh", "-c"],
            command=command_array,
            environment={"MYSQL_DATABASE": "openemr"},
            health_check=ecs.HealthCheck(
                # During first-boot installs, OpenEMR may take >5 minutes to become responsive.
                # Do NOT let ECS restart the container during setup; ALB health checks will still gate traffic.
                command=[
                    "CMD-SHELL",
                    (
                        # Health check strategy:
                        # - If setup is NOT complete, do not fail the container health check (prevents restart loops).
                        # - If setup appears "stuck" (docker-initiated is older than 20 minutes and docker-completed is missing),
                        #   fail the health check so ECS recycles the task.
                        # - Once docker-completed exists, enforce a real HTTPS probe.
                        "COMPLETED=/var/www/localhost/htdocs/openemr/sites/docker-completed; "
                        "INIT=/var/www/localhost/htdocs/openemr/sites/docker-initiated; "
                        'if [ ! -f "$COMPLETED" ]; then '
                        '  if [ -f "$INIT" ]; then '
                        '    MTIME=$(stat -c %Y "$INIT" 2>/dev/null || stat -f %m "$INIT" 2>/dev/null || echo ""); '
                        '    if [ -n "$MTIME" ] && [ "$MTIME" != "0" ]; then '
                        "      NOW=$(date +%s); AGE=$((NOW - MTIME)); "
                        '      if [ "$AGE" -gt 1200 ]; then exit 1; fi; '
                        "    fi; "
                        "  fi; "
                        "  exit 0; "
                        "fi; "
                        f"curl -f -k https://localhost:{container_port}/ >/dev/null 2>&1"
                    ),
                ],
                # Keep checks responsive; install gating logic prevents thrash.
                start_period=Duration.seconds(120),
                interval=Duration.seconds(60),
                timeout=Duration.seconds(10),
                retries=3,
            ),
            image=ecs.ContainerImage.from_registry(f"openemr/openemr:{openemr_version}"),
            secrets=secrets,
        )

        # Suppress inline policy warnings for execution and task roles (after container creates DefaultPolicies)
        NagSuppressions.add_resource_suppressions(
            openemr_fargate_task_definition.execution_role.node.find_child("DefaultPolicy").node.find_child("Resource"),
            [
                {
                    "id": "HIPAA.Security-IAMNoInlinePolicy",
                    "reason": "Inline policy required for ECS task execution permissions (ECR, CloudWatch Logs, Secrets Manager)",
                }
            ],
        )

        # Suppress ECS task definition environment variable warning
        # Environment variables contain non-sensitive configuration only
        NagSuppressions.add_resource_suppressions(
            openemr_fargate_task_definition,
            [
                {
                    "id": "AwsSolutions-ECS2",
                    "reason": "Environment variables contain non-sensitive configuration (MYSQL_DATABASE). Secrets are properly injected from Secrets Manager (database password, SMTP credentials, admin password)",
                },
            ],
        )

        # Create mount points for EFS
        efs_mount_point_for_sites_folder = ecs.MountPoint(
            container_path="/var/www/localhost/htdocs/openemr/sites/",
            read_only=False,
            source_volume="SitesFolderVolume",
        )

        efs_mount_point_for_ssl_folder = ecs.MountPoint(
            container_path="/etc/ssl/", read_only=False, source_volume="SslFolderVolume"
        )

        # Add mount points to container definition
        openemr_container.add_mount_points(efs_mount_point_for_sites_folder, efs_mount_point_for_ssl_folder)

        # Create proxy service with load balancer
        # Attach the pre-configured ECS task security group that has access to database, valkey, and EFS
        #
        # End-to-End Encryption Architecture:
        # - Client → ALB: HTTPS with ACM certificate (automatically issued/renewed when route53_domain provided)
        # - ALB → Containers: HTTPS with self-signed certificates (containers handle TLS termination)
        # - The OpenEMR container always serves HTTPS on port 443 (with self-signed certificates from EFS)
        #
        # Certificate is required (enforced in validation) - enables automatic certificate management via ACM:
        # - Automatic issuance when route53_domain is provided
        # - Automatic DNS validation via Route53
        # - Automatic renewal by ACM (as long as DNS records remain)
        #
        # When certificate is provided: ALB terminates HTTPS with ACM cert, then re-encrypts to containers
        # Note: Certificate should always be present due to validation requirement
        if certificate:
            openemr_service = ecs_patterns.ApplicationLoadBalancedFargateService(
                self.scope,
                "OpenEMRFargateLBService",
                certificate=certificate,
                min_healthy_percent=100,
                cluster=ecs_cluster,
                desired_count=context.get("openemr_service_fargate_minimum_capacity", 2),
                # Allow OpenEMR first-boot install to complete before ECS starts judging ELB target health.
                # 20 minutes aligns with our stale-leader cleanup and “stuck install” threshold.
                health_check_grace_period=Duration.seconds(1200),
                load_balancer=alb,
                open_listener=False,
                target_protocol=elb.ApplicationProtocol.HTTPS,
                task_definition=openemr_fargate_task_definition,
                task_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                security_groups=[ecs_task_sec_group],
            )
        else:
            # This should never happen - certificate is required (enforced in validation)
            # But provide a clear error if it does
            raise ValueError(
                "Certificate is required for HTTPS (end-to-end encryption). "
                "This should have been caught during validation. "
                "Please provide either 'route53_domain' or 'certificate_arn' in cdk.json context."
            )

        # Add dependency on SSL materials creation (passed from stack)
        # This will be handled in the main stack

        # Add availability zone rebalancing
        # This allows better recovery if an availability zone goes down
        # Documentation: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-rebalancing.html
        openemr_service_cfn = openemr_service.service.node.default_child
        openemr_service_cfn.add_property_override("AvailabilityZoneRebalancing", "ENABLED")  # type: ignore

        # Fail fast on bad deployments: automatically rollback if a deployment can't stabilize.
        # Note: Some CDK versions do not expose a typed helper on the service construct, so we
        # apply the CloudFormation property override directly.
        openemr_service_cfn.add_property_override(
            "DeploymentConfiguration.DeploymentCircuitBreaker",
            {"Enable": True, "Rollback": True},
        )

        # Configure health check - container always serves HTTPS on port 443
        openemr_service.target_group.configure_health_check(
            protocol=elb.Protocol.HTTPS,
            path="/",
            port=str(container_port),
            healthy_http_codes="302",
            healthy_threshold_count=2,
            unhealthy_threshold_count=5,
            interval=Duration.seconds(60),
            timeout=Duration.seconds(10),
        )

        # Configure ECS Exec if enabled
        if is_true(context.get("enable_ecs_exec")) and exec_bucket and ecs_exec_group:
            openemr_service_cfn.add_property_override("EnableExecuteCommand", "True")  # type: ignore
            openemr_fargate_task_definition.task_role.add_to_policy(  # type: ignore
                iam.PolicyStatement(
                    actions=[
                        "ssmmessages:CreateControlChannel",
                        "ssmmessages:CreateDataChannel",
                        "ssmmessages:OpenControlChannel",
                        "ssmmessages:OpenDataChannel",
                    ],
                    resources=["*"],
                )
            )
            openemr_fargate_task_definition.task_role.add_to_policy(  # type: ignore
                iam.PolicyStatement(
                    actions=["s3:PutObject", "s3:GetEncryptionConfiguration"],
                    resources=[exec_bucket.bucket_arn, f"{exec_bucket.bucket_arn}/*"],
                )
            )
            openemr_fargate_task_definition.task_role.add_to_policy(  # type: ignore
                iam.PolicyStatement(actions=["logs:DescribeLogGroups"], resources=["*"])
            )
            openemr_fargate_task_definition.task_role.add_to_policy(  # type: ignore
                iam.PolicyStatement(
                    actions=["logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"],
                    resources=[ecs_exec_group.log_group_arn],
                )
            )

            # Suppress inline policy warnings for task role (after grants create DefaultPolicy)
            NagSuppressions.add_resource_suppressions(
                openemr_fargate_task_definition.task_role.node.find_child("DefaultPolicy").node.find_child("Resource"),
                [
                    {
                        "id": "AwsSolutions-IAM5",
                        "reason": "Wildcard permissions required for ECS Exec S3 logging and SSM operations",
                        "appliesTo": ["Resource::*", "Resource::<EcsExecBucketx0l01fA925CEBD.Arn>/*"],
                    },
                    {
                        "id": "HIPAA.Security-IAMNoInlinePolicy",
                        "reason": "Inline policy required for least-privilege ECS task permissions specific to this OpenEMR deployment",
                    },
                ],
            )

        # Configure autoscaling
        # Handle None values explicitly (if key exists but is None, use defaults)
        min_capacity_value = context.get("openemr_service_fargate_minimum_capacity", 2)
        max_capacity_value = context.get("openemr_service_fargate_maximum_capacity", 100)
        min_capacity = int(min_capacity_value if min_capacity_value is not None else 2)
        max_capacity = int(max_capacity_value if max_capacity_value is not None else 100)

        scalable_target = openemr_service.service.auto_scale_task_count(
            min_capacity=min_capacity, max_capacity=max_capacity
        )

        # Handle None values for autoscaling percentages
        cpu_percentage_value = context.get("openemr_service_fargate_cpu_autoscaling_percentage", 40)
        memory_percentage_value = context.get("openemr_service_fargate_memory_autoscaling_percentage", 40)
        cpu_percentage = int(cpu_percentage_value if cpu_percentage_value is not None else 40)
        memory_percentage = int(memory_percentage_value if memory_percentage_value is not None else 40)

        scalable_target.scale_on_cpu_utilization("CpuScaling", target_utilization_percent=cpu_percentage)

        scalable_target.scale_on_memory_utilization("MemoryScaling", target_utilization_percent=memory_percentage)

        # Note: Database, Valkey/Redis, and EFS security group rules are configured in stack.py
        # BEFORE service creation. The ECS task security group (ecs_task_sec_group) is pre-configured
        # with ingress rules allowing access to database (port 3306), Valkey (port 6379), and EFS (port 2049).
        # This security group is attached to the service above via the security_groups parameter.

        return openemr_service
