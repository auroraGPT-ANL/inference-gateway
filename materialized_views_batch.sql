-- a. Total Number of batch jobs
DROP MATERIALIZED VIEW IF EXISTS public.mv_batch_total_jobs CASCADE;
CREATE MATERIALIZED VIEW public.mv_batch_total_jobs AS
SELECT 
    COUNT(*) AS total_batch_jobs,
    COUNT(*) FILTER (WHERE status = 'completed') AS completed_batch_jobs,
    COUNT(*) FILTER (WHERE status = 'failed') AS failed_batch_jobs,
    COUNT(*) FILTER (WHERE status = 'pending') AS pending_batch_jobs,
    COUNT(*) FILTER (WHERE status = 'running') AS running_batch_jobs
FROM resource_server_batch;

-- b. Total number of successful batch requests (based on num_responses)
DROP MATERIALIZED VIEW IF EXISTS public.mv_batch_successful_requests CASCADE;
CREATE MATERIALIZED VIEW public.mv_batch_successful_requests AS
SELECT 
    SUM(
        (json_extract_path_text((result)::json, VARIADIC ARRAY['metrics', 'num_responses']))::numeric
    ) AS total_successful_requests
FROM resource_server_batch
WHERE status = 'completed'
AND result IS NOT NULL
AND result <> ''
AND (result)::json -> 'metrics' -> 'num_responses' IS NOT NULL;

-- c. Batch Requests per model
DROP MATERIALIZED VIEW IF EXISTS public.mv_batch_requests_per_model CASCADE;
CREATE MATERIALIZED VIEW public.mv_batch_requests_per_model AS
SELECT 
    model,
    COUNT(*) AS total_jobs,
    COUNT(*) FILTER (WHERE status = 'completed') AS completed_jobs,
    SUM(
        CASE
            WHEN status = 'completed' AND result IS NOT NULL AND result <> '' AND
                 (result)::json -> 'metrics' -> 'num_responses' IS NOT NULL
            THEN (json_extract_path_text((result)::json, VARIADIC ARRAY['metrics', 'num_responses']))::numeric
            ELSE 0
        END
    ) AS total_requests
FROM resource_server_batch
GROUP BY model
ORDER BY model;

-- d. Total number of batch users
DROP MATERIALIZED VIEW IF EXISTS public.mv_batch_unique_users CASCADE;
CREATE MATERIALIZED VIEW public.mv_batch_unique_users AS
SELECT 
    COUNT(DISTINCT username) AS unique_users,
    array_agg(DISTINCT username) AS user_list
FROM resource_server_batch;

-- e. Total number of tokens processed
DROP MATERIALIZED VIEW IF EXISTS public.mv_batch_total_tokens CASCADE;
CREATE MATERIALIZED VIEW public.mv_batch_total_tokens AS
SELECT 
    SUM(
        (json_extract_path_text((result)::json, VARIADIC ARRAY['metrics', 'total_tokens']))::numeric
    ) AS total_tokens_processed
FROM resource_server_batch
WHERE status = 'completed'
AND result IS NOT NULL
AND result <> ''
AND (result)::json -> 'metrics' -> 'total_tokens' IS NOT NULL;

-- f. Average latency per model
DROP MATERIALIZED VIEW IF EXISTS public.mv_batch_avg_latency CASCADE;
CREATE MATERIALIZED VIEW public.mv_batch_avg_latency AS
SELECT 
    model,
    AVG(
        (json_extract_path_text((result)::json, VARIADIC ARRAY['metrics', 'response_time']))::numeric
    ) AS avg_response_time_sec
FROM resource_server_batch
WHERE status = 'completed'
AND result IS NOT NULL
AND result <> ''
AND (result)::json -> 'metrics' -> 'response_time' IS NOT NULL
GROUP BY model
ORDER BY model;

-- g. Average throughput per model
DROP MATERIALIZED VIEW IF EXISTS public.mv_batch_avg_throughput CASCADE;
CREATE MATERIALIZED VIEW public.mv_batch_avg_throughput AS
SELECT 
    model,
    AVG(
        (json_extract_path_text((result)::json, VARIADIC ARRAY['metrics', 'throughput_tokens_per_second']))::numeric
    ) AS avg_throughput_tokens_per_sec
FROM resource_server_batch
WHERE status = 'completed'
AND result IS NOT NULL
AND result <> ''
AND (result)::json -> 'metrics' -> 'throughput_tokens_per_second' IS NOT NULL
GROUP BY model
ORDER BY model;

-- Daily batch usage (all time)
DROP MATERIALIZED VIEW IF EXISTS public.mv_batch_daily_usage CASCADE;
CREATE MATERIALIZED VIEW public.mv_batch_daily_usage AS
SELECT 
    date_trunc('day'::text, created_at) AS day,
    COUNT(*) AS batch_count,
    COUNT(*) FILTER (WHERE status = 'completed') AS completed_count,
    COUNT(*) FILTER (WHERE status = 'failed') AS failed_count,
    SUM(
        CASE
            WHEN status = 'completed' AND result IS NOT NULL AND result <> '' AND
                 (result)::json -> 'metrics' -> 'num_responses' IS NOT NULL
            THEN (json_extract_path_text((result)::json, VARIADIC ARRAY['metrics', 'num_responses']))::numeric
            ELSE 0
        END
    ) AS total_requests
FROM resource_server_batch
GROUP BY date_trunc('day'::text, created_at)
ORDER BY date_trunc('day'::text, created_at); 