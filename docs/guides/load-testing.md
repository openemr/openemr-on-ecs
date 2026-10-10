# Load testing

This guide shows you how to check how your deployment performs under load, with the included
script or with the `siege` tool, and summarizes the results from the project's own earlier testing.
It's for anyone sizing a deployment or checking that a change didn't hurt performance.

> [!WARNING]
> The web application firewall (AWS WAF) in front of OpenEMR blocks any single IP address that
> sends more than **2,000 requests in 5 minutes**. A load test from one computer passes that limit
> quickly, after which every request gets `403 Forbidden` and the test fails. Read
> [Working with the rate limit](#working-with-the-rate-limit) before you start.

## Before you begin

- A deployed stack, and your computer's IP address allowed in `security_group_ip_range_ipv4` (see
  [Configuration](configuration.md#who-can-reach-openemr-ip-access)).
- The AWS CLI configured for the stack's account and Region.
- Python 3. The script installs the `requests` package if it's missing.
- **Cost:** heavy load can trigger autoscaling, which adds Fargate tasks and Aurora capacity while
  the test runs. Keep tests short.
- **Time:** about 2 minutes for the default script run.

> [!CAUTION]
> Don't load test a production system that people are using. Use a separate test stack.

## Working with the rate limit

The firewall's rate rule counts requests per IP address over a rolling 5-minute window. Your
options:

- **Smoke test only.** Keep the total under 2,000 requests per 5 minutes (about 6 requests per
  second). That checks the site responds, but isn't a real load test.
- **Spread the load.** Run the test from several machines with different public IP addresses, each
  staying under the limit. Every address must be allowed by your IP access settings.
- **Temporarily relax the rule on a test stack.** In the AWS WAF console, find the stack's web ACL
  and change the rate-limiting rule's action to **Count** while you test. This is a manual change
  outside CloudFormation; your next `cdk deploy` puts the rule back. Never do this on a production
  stack.

The firewall also blocks requests whose user agent contains `bot`, `scraper`, `crawler`, or
`spider`, so don't set your load tool's user agent to anything like that.

## Run the automated load test

1. Run the script with your stack name:

   ```bash
   ./scripts/load-test.sh OpenemrEcsStack
   ```

   The script:
   - gets the application URL from the stack's `ApplicationURL` output (or `LoadBalancerDNS` if
     that's missing);
   - waits until the site responds (up to 30 tries, 2 seconds apart);
   - sends requests to the OpenEMR home page for 10 seconds to warm up;
   - runs the test with several concurrent workers; and
   - reports the success rate, response times (average, median, 95th and 99th percentile, minimum,
     and maximum), requests per second, and a summary of errors.

2. Read the result. The test **passes** when at least 95% of requests succeed (an HTTP status from
   200 to 399, after following redirects) **and** the actual requests per second reach at least 80%
   of the target. The script exits with `0` on a pass and `1` on a failure. A pass ends with:

   ```text
   ✓ Load test PASSED
   ```

To change the load, set environment variables before running it:

```bash
export DURATION=120            # test length in seconds (default 60)
export CONCURRENT_USERS=100    # concurrent workers (default 50)
export REQUESTS_PER_SECOND=200 # target requests per second (default 100)
export WARMUP_TIME=10          # warm-up length in seconds (default 10)
./scripts/load-test.sh OpenemrEcsStack
```

The Region comes from `AWS_REGION`, then your AWS CLI profile, then `us-east-1`.

> [!NOTE]
> `REQUESTS_PER_SECOND` isn't a strict cap. Each worker pauses `1 / REQUESTS_PER_SECOND` seconds
> between its requests, so the real rate depends on the number of workers and how fast OpenEMR
> responds, and is often well above the target. The script also skips TLS certificate checks.

More script details are in [`scripts/README.md`](../../scripts/README.md).

## Run a manual load test with siege

[siege](https://github.com/JoeDog/siege) is a simple HTTP load-testing tool. On a Mac:

1. Install [Homebrew](https://brew.sh/):

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. Install the tools:

   ```bash
   brew install watch
   brew install siege
   ```

3. Get your application URL (the `ApplicationURL` output) and run siege with 255 concurrent users
   for 60 minutes:

   ```bash
   APP_URL=$(aws cloudformation describe-stacks --stack-name OpenemrEcsStack \
     --query "Stacks[0].Outputs[?OutputKey=='ApplicationURL'].OutputValue" --output text)
   watch -n0 siege -c 255 "$APP_URL" -t60m
   ```

4. While it runs, watch the CloudWatch metrics for the load balancer, the ECS service, Valkey
   (ElastiCache), and Aurora.

## Results from earlier testing

The project's original load test used the siege command above. These results come from an earlier
version of the architecture, before the firewall's per-IP rate limit was in place; a single
computer can't reproduce them against today's stack without the changes described in
[Working with the rate limit](#working-with-the-rate-limit).

- The load balancer comfortably served **more than 4,000 requests per second**, with active
  connections peaking above 1,300.
- No response took longer than **0.8 seconds**.
- Average CPU use peaked at **18.6%** and memory at **30.4%**. ECS autoscaling didn't need to add
  any tasks, so Fargate cost stayed at its base rate during the test.
- Aurora and ElastiCache performed well: database capacity (ACU) use and average read and write
  latency stayed low.
- The OpenEMR web interface stayed just as responsive during the test.

Load balancer metrics:

![Load balancer request and connection metrics during the test](../images/load_balancer_metrics.png)

![More load balancer metrics during the test](../images/load_balancer_metrics_2.png)

OpenEMR service CPU and memory:

![ECS CPU and memory utilization during the test](../images/load_testing_cpu_and_memory_metrics.png)

Redis on ElastiCache (now Valkey):

![ElastiCache metrics during the test](../images/elasticache_metrics.png)

Aurora:

![Aurora metrics during the test](../images/rds_metrics.png)

## If something goes wrong

| Problem | What to do |
|---|---|
| Many `HTTP 403` errors partway through | You hit the firewall's rate limit. See [Working with the rate limit](#working-with-the-rate-limit). The block lifts once your rate drops below the limit. |
| `Application did not become ready` | Your IP address isn't allowed, or the service isn't healthy yet. Check `curl -k <ApplicationURL>` from the same computer. |
| `Could not retrieve application URL` | Check the stack name and Region. |
| Failed requests with timeouts | OpenEMR is overloaded. Check the CPU and memory metrics, and consider larger tasks or a higher minimum task count (see [Configuration](configuration.md#server-size-and-automatic-scaling)). |

## Related

- [Configuration](configuration.md#server-size-and-automatic-scaling): task size and autoscaling
- [Optional features](optional-features.md#monitoring-alarms-and-email-alerts): alarms for high CPU
  and slow responses
- [Costs](../reference/costs.md)
- [Architecture](../reference/architecture.md)
