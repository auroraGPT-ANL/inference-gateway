# Connecting to Globus Compute Backends

This guide describes how to connect the Gateway to existing backends powered by Globus Compute single-user endpoints.

## Endpoint Configuration

If you adopted our Globus Compute configuration, you can simply reuse the `GlobusComputeEndpoint` endpoint adaptor and add your entries to the `endpoints.json` file. Each entry should respect the following the data structure:

```json
{
    "model": "resource_server_async.endpoint",
    "pk": 1,
    "fields": {
        "endpoint_slug": "your-cluster-api-your-model-70b",
        "cluster": "your-cluster",
        "framework": "api",
        "model": "Your-Model-70B",
        "endpoint_adapter": "resource_server_async.endpoints.globus_compute.GlobusComputeEndpoint",
        "config": {
            "api_port": 8000,
            "endpoint_uuid": "your-backend-globus-compute-endpoint-uuid",
            "function_uuid": "your-backend-globus-compute-function-uuid",
            "batch_endpoint_uuid": "your-backend-globus-compute-batch-endpoint-uuid",
            "batch_function_uuid": "your-backend-globus-compute-batch-function-uuid"
        }
    }
}
```

## Cluster Configuration

If you adopted our Globus Compute configuration, you can simply reuse the `GlobusComputeCluster` cluster adaptor and add your entries to the `clusters.json` file. Each entry should respect the following the data structure:

```json
{
    "model": "resource_server_async.cluster",
    "pk": 1,
    "fields": {
        "cluster_name": "your-cluster",
        "cluster_adapter": "resource_server_async.clusters.globus_compute.GlobusComputeCluster",
        "frameworks": [
            "vllm"
        ],
        "openai_endpoints": [
            "chat/completions",
            "completions",
            "embeddings",
        ],
        "config": {
            "qstat_endpoint_uuid": "your-backend-globus-compute-qstat-endpoint-uuid",
            "qstat_function_uuid": "your-backend-globus-compute-qstat-function-uuid"
        }
    }
}
```
