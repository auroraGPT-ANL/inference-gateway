-- Materialized Views Export
-- Exported on Tue Mar 18 12:20:16 AM CDT 2025

DROP MATERIALIZED VIEW IF EXISTS public.mv_model_throughput CASCADE;
CREATE MATERIALIZED VIEW public.mv_model_throughput AS
 WITH parsed_throughput AS (          SELECT e.model,             (json_extract_path_text((r.result)::json, VARIADIC ARRAY['throughput_tokens_per_second'::text]))::numeric AS throughput            FROM (resource_server_log r              JOIN resource_server_endpoint e ON (((e.endpoint_slug)::text = (r.endpoint_slug)::text)))           WHERE ((r.response_status = 200) AND (r.result IS NOT NULL) AND (r.result <> ''::text) AND (((r.result)::json ->> 'throughput_tokens_per_second'::text) IS NOT NULL))         )  SELECT parsed_throughput.model,     round(avg(parsed_throughput.throughput), 2) AS avg_throughput,     count(*) AS successful_requests_with_throughput    FROM parsed_throughput   WHERE (parsed_throughput.throughput IS NOT NULL)   GROUP BY parsed_throughput.model   ORDER BY parsed_throughput.model;;

DROP MATERIALIZED VIEW IF EXISTS public.mv_model_throughput_new CASCADE;
CREATE MATERIALIZED VIEW public.mv_model_throughput_new AS
 WITH parsed_throughput AS (          SELECT e.model,             (json_extract_path_text((r.result)::json, VARIADIC ARRAY['throughput_tokens_per_second'::text]))::numeric AS throughput            FROM (resource_server_log r              JOIN resource_server_endpoint e ON (((e.endpoint_slug)::text = (r.endpoint_slug)::text)))           WHERE ((r.response_status = 200) AND (r.result IS NOT NULL) AND (r.result <> ''::text) AND (((r.result)::json ->> 'throughput_tokens_per_second'::text) IS NOT NULL))         )  SELECT parsed_throughput.model,     round(avg(parsed_throughput.throughput), 2) AS avg_throughput,     count(*) AS successful_requests_with_throughput    FROM parsed_throughput   WHERE (parsed_throughput.throughput IS NOT NULL)   GROUP BY parsed_throughput.model   ORDER BY parsed_throughput.model;;

DROP MATERIALIZED VIEW IF EXISTS public.mv_model_requests CASCADE;
CREATE MATERIALIZED VIEW public.mv_model_requests AS
 SELECT e.model,     count(*) AS total_requests,     count(*) FILTER (WHERE ((r.response_status = 200) OR (r.response_status IS NULL))) AS successful_requests,     count(*) FILTER (WHERE (NOT ((r.response_status = 200) OR (r.response_status IS NULL)))) AS failed_requests    FROM (resource_server_log r      JOIN resource_server_endpoint e ON (((e.endpoint_slug)::text = (r.endpoint_slug)::text)))   GROUP BY e.model;;

DROP MATERIALIZED VIEW IF EXISTS public.mv_overall_stats CASCADE;
CREATE MATERIALIZED VIEW public.mv_overall_stats AS
 SELECT count(*) AS total_requests,     count(*) FILTER (WHERE ((resource_server_log.response_status = 200) OR (resource_server_log.response_status IS NULL))) AS successful_requests,     count(*) FILTER (WHERE (NOT ((resource_server_log.response_status = 200) OR (resource_server_log.response_status IS NULL)))) AS failed_requests,     count(DISTINCT resource_server_log.username) AS total_users    FROM resource_server_log;;

DROP MATERIALIZED VIEW IF EXISTS public.mv_user_details CASCADE;
CREATE MATERIALIZED VIEW public.mv_user_details AS
 SELECT DISTINCT resource_server_log.name,     resource_server_log.username    FROM resource_server_log;;

DROP MATERIALIZED VIEW IF EXISTS public.mv_model_latency CASCADE;
CREATE MATERIALIZED VIEW public.mv_model_latency AS
 SELECT e.model,     avg((r.timestamp_response - r.timestamp_receive)) AS avg_latency    FROM (resource_server_log r      JOIN resource_server_endpoint e ON (((e.endpoint_slug)::text = (r.endpoint_slug)::text)))   WHERE ((r.response_status = 200) OR (r.response_status IS NULL))   GROUP BY e.model;;

DROP MATERIALIZED VIEW IF EXISTS public.mv_users_per_model CASCADE;
CREATE MATERIALIZED VIEW public.mv_users_per_model AS
 SELECT e.model,     count(DISTINCT r.username) AS user_count    FROM (resource_server_log r      JOIN resource_server_endpoint e ON (((e.endpoint_slug)::text = (r.endpoint_slug)::text)))   GROUP BY e.model;;

DROP MATERIALIZED VIEW IF EXISTS public.mv_weekly_usage CASCADE;
CREATE MATERIALIZED VIEW public.mv_weekly_usage AS
 SELECT date_trunc('week'::text, resource_server_log.timestamp_receive) AS week_start,     count(*) AS request_count    FROM resource_server_log   GROUP BY (date_trunc('week'::text, resource_server_log.timestamp_receive))   ORDER BY (date_trunc('week'::text, resource_server_log.timestamp_receive));;

DROP MATERIALIZED VIEW IF EXISTS public.mv_daily_usage_2_weeks CASCADE;
CREATE MATERIALIZED VIEW public.mv_daily_usage_2_weeks AS
 SELECT date_trunc('day'::text, resource_server_log.timestamp_receive) AS day,     count(*) AS request_count    FROM resource_server_log   WHERE (resource_server_log.timestamp_receive >= (CURRENT_DATE - '14 days'::interval))   GROUP BY (date_trunc('day'::text, resource_server_log.timestamp_receive))   ORDER BY (date_trunc('day'::text, resource_server_log.timestamp_receive));;

DROP MATERIALIZED VIEW IF EXISTS public.mv_requests_per_user CASCADE;
CREATE MATERIALIZED VIEW public.mv_requests_per_user AS
 SELECT resource_server_log.name,     resource_server_log.username,     count(*) AS total_requests,     count(*) FILTER (WHERE ((resource_server_log.response_status = 200) OR (resource_server_log.response_status IS NULL))) AS successful_requests,     count(*) FILTER (WHERE (NOT ((resource_server_log.response_status = 200) OR (resource_server_log.response_status IS NULL)))) AS failed_requests    FROM resource_server_log   GROUP BY resource_server_log.name, resource_server_log.username;;
