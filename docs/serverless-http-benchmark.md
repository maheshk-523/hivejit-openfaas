# Serverless HTTP Latency Benchmark

Use this for deployed functions where the only stable interface is an HTTP
endpoint: OpenFaaS routes, Cloud Run services, Lambda function URLs, Azure
Functions HTTP triggers, or local gateway URLs.

## Invoke And Graph

```bash
python3 scripts/http_invoke_latency.py \
  --url http://127.0.0.1:8080/function/profilecache \
  --label "OpenFaaS profilecache HTTP latency" \
  --requests 100 \
  --warmup 5 \
  --csv results/openfaas-profilecache.csv \
  --summary results/openfaas-profilecache-summary.json \
  --svg docs/figures/openfaas-profilecache-latency.svg
```

For POST handlers:

```bash
python3 scripts/http_invoke_latency.py \
  --url https://example.lambda-url.us-east-1.on.aws/ \
  --method POST \
  --header "Content-Type: application/json" \
  --body '{"scenario":"serve-hot"}' \
  --requests 100
```

## Graph Existing CSV

```bash
python3 scripts/http_invoke_latency.py \
  --from-csv openfaas_lusearch.csv \
  --label "OpenFaaS DaCapo lusearch HTTP latency" \
  --summary docs/figures/openfaas-lusearch-summary.json \
  --svg docs/figures/openfaas-lusearch-latency.svg
```

The CSV must include at least:

```text
invocation,latency_ms,status
```

Additional columns such as `response_bytes` and `error` are preserved when the
script performs the HTTP run itself.

## Current OpenFaaS Example

The repository's existing `openfaas_lusearch.csv` has been rendered here:

```text
docs/figures/openfaas-lusearch-latency.svg
docs/figures/openfaas-lusearch-summary.json
```

That historical run recorded 15 HTTP invocations with p50 `39.5 ms`, p95
`52.7 ms`, and status `500` for every invocation. The graph is still useful for
latency shape, but the function error status should be fixed before treating it
as a successful serverless benchmark.
