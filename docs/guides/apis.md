# REST and FHIR APIs

This guide shows you how to turn on OpenEMR's REST and FHIR APIs and walk through getting your
first access token with the included test script. It's for developers who want to connect other
applications to OpenEMR.

OpenEMR documents the APIs themselves in its
[FHIR API guide](https://github.com/openemr/openemr/blob/master/FHIR_README.md) and
[REST API guide](https://github.com/openemr/openemr/blob/master/API_README.md).

## Before you begin

- A deployed stack with a certificate (`route53_domain` or `certificate_arn`; every deployment
  has one). See [HTTPS and DNS](https-and-dns.md).
- Your computer's IP address is allowed to reach OpenEMR (see
  [Configuration](configuration.md#who-can-reach-openemr-ip-access)).
- Python 3 with the `requests` package and `tkinter` (the script reads the authorization code from
  your clipboard with `tkinter`). On macOS with Homebrew Python, install `python-tk`; on
  Debian/Ubuntu, `python3-tk`.
- The OpenEMR `admin` password. It's in the Secrets Manager secret named in the
  `OpenEMRPasswordSecretARN` stack output.
- **Cost:** none beyond normal usage.
- **Time:** about 15 minutes.

## Step 1: Turn on the APIs

1. In `cdk.json`, set:

   ```json
   "activate_openemr_apis": "true",
   ```

2. Deploy:

   ```bash
   node_modules/.bin/cdk deploy
   ```

This turns on OpenEMR's own settings for the REST API and the FHIR API, and sets OpenEMR's base
URL for OAuth2 (`site_addr_oath`) to the `ApplicationURL`. There's no separate API endpoint or
load balancer path; the APIs are served by OpenEMR at the same address as the web interface.

## Step 2: Find your OpenEMR address

You need the hostname people use to reach OpenEMR, without `https://`. Use the host part of the
`ApplicationURL` stack output (for example `openemr.example.com`):

```bash
aws cloudformation describe-stacks --stack-name OpenemrEcsStack \
  --query "Stacks[0].Outputs[?OutputKey=='ApplicationURL'].OutputValue" --output text
```

The outputs are also in the CloudFormation console,

![CloudFormation console outputs](../images/ConsoleOutputALBDNS.png)

and printed in your terminal at the end of `cdk deploy`:

![Terminal output at the end of cdk deploy](../images/TerminalOutputALBDNS.png)

> [!TIP]
> Use the `ApplicationURL` host rather than the raw load balancer name (`LoadBalancerDNS`) when
> you have a domain. OAuth2 tokens are issued for OpenEMR's configured base URL, which is the
> `ApplicationURL`.

## Step 3: Register an API client

1. From the repository root, run the script with your hostname:

   ```bash
   cd scripts
   python api_endpoint_test.py openemr.example.com
   ```

   ![Running the API test script](../images/RunningPythonScript.png)

2. The script registers a new client application called `TestApp` and prints its details. At the
   end it says `Enable the client with the ID above. Push enter to continue.` Don't press Enter
   yet.

   ![First output from the script, ending with the enable prompt](../images/1stOutputFromScript.png)

   > [!NOTE]
   > The script skips TLS certificate checks (`verify=False`) and prints the client secret. Use it
   > only for testing, and treat its output as sensitive.

## Step 4: Enable the client in OpenEMR

New API clients are disabled until an administrator enables them.

1. Copy the `client_id` value from the script's output:

   ![The client ID in the script output](../images/OutputFromScriptClientID.png)

2. Log in to OpenEMR as `admin` and open the **API Clients** menu:

   ![The API Clients menu in OpenEMR](../images/APIClientsMenu.png)

3. Find the registration whose Client ID matches:

   ![Finding the matching client registration](../images/FindingCorrectClientID.png)

4. Choose **Edit** next to it, then **Enable Client**:

   ![The Enable Client button](../images/EnableClientButton.png)

   Success looks like the registration showing as enabled:

   ![The client registration shown as enabled](../images/ClientEnabled.png)

5. Go back to the terminal and press **Enter**. The script prints an authorization URL:

   ![Second output from the script, with the authorization URL](../images/2ndOutputFromScript.png)

## Step 5: Create a test patient (if you have none)

If OpenEMR has no patients yet, add a fake one for testing. Open the new patient menu,

![The menu for adding a new patient](../images/AddNewPatient.png)

fill in some test data, and choose **Create New Patient**:

![Creating a new test patient](../images/CreateNewPatient.png)

## Step 6: Authorize and get a token

> [!IMPORTANT]
> The next steps are time-sensitive. The authorization code you get is short-lived and must be
> exchanged for an access token quickly. The access token then works for much longer. Read
> through all of this step before starting it.

1. Open the authorization URL from step 4 in your browser. Log in with the `admin` user and
   password:

   ![The OpenEMR OAuth2 login prompt](../images/LoginPrompt.png)

2. Select your test patient:

   ![Selecting a patient during authorization](../images/SelectPatient.png)

   That takes you to the authorization page:

   ![Top of the authorization page](../images/TopOfAuthorizationPage.png)

3. Scroll to the bottom and choose **Authorize**:

   ![The Authorize button](../images/ClickAuthorize.png)

4. You'll land on a **403 Forbidden** page. That's expected. Look at the address bar and copy
   everything after `?code=` up to (not including) `&state=`:

   ![403 Forbidden page with the code in the address bar](../images/403Forbidden.png)

5. Back in the terminal, press **Enter**. The script says
   `Copy the code in the redirect link and then press enter.` You've already copied it, so press
   **Enter** again. The script prints the code it read from your clipboard,

   ![The authorization code shown in the terminal](../images/CodeInTerminal.png)

   followed by a response containing an `access_token` (and a `refresh_token`):

   ![The token response with an access token](../images/Success.png)

You've now registered a client, enabled it, authorized it, and received a token you can use to
make authenticated API calls.

## Step 7: Call the API

Pass the token in an `Authorization` header. For example, to list patients through FHIR:

```bash
TOKEN='<access_token from the script>'
curl -H "Authorization: Bearer $TOKEN" https://openemr.example.com/apis/default/fhir/Patient
```

The script asks for FHIR scopes (`api:fhir` plus `patient/...` and `user/...` read scopes), so its
token is for FHIR endpoints. For OpenEMR's standard REST API, register a client that requests the
REST scopes described in OpenEMR's [REST API guide](https://github.com/openemr/openemr/blob/master/API_README.md).

## If something goes wrong

| Problem | What to do |
|---|---|
| The script hangs or can't connect | Your IP address isn't allowed. Check `security_group_ip_range_ipv4` in [Configuration](configuration.md#who-can-reach-openemr-ip-access). |
| Registration returns an error about the API being disabled | Make sure `activate_openemr_apis` is `"true"` and you've deployed. |
| `invalid_client` or "client not enabled" | Enable the client in **API Clients** (step 4). |
| `invalid_grant` when exchanging the code | The code expired or was copied wrongly. Run the script again and move faster through step 6. |
| `ModuleNotFoundError: No module named '_tkinter'` | Install `tkinter` for your Python (see [Before you begin](#before-you-begin)). |
| `403 Forbidden` on API calls that used to work | The web application firewall blocks an IP address that sends more than 2,000 requests in 5 minutes, and blocks user agents containing `bot`, `scraper`, `crawler`, or `spider`. Slow down your client and use a different user agent. |

## Related

- [OpenEMR FHIR API guide](https://github.com/openemr/openemr/blob/master/FHIR_README.md)
- [OpenEMR REST API guide](https://github.com/openemr/openemr/blob/master/API_README.md)
- [Optional features](optional-features.md#patient-portal): the patient portal, which also uses
  OAuth2
- [Configuration](configuration.md)
