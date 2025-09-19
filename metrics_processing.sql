-- =============================================================================
-- Request Metrics Batch Processing Setup
-- =============================================================================
-- This script sets up asynchronous batch processing for request metrics
-- instead of using expensive synchronous triggers.
--
-- Prerequisites:
-- - metrics_processed column added to resource_server_async_requestlog table
-- - Old trigger trg_upsert_requestmetrics removed
-- - Old function fn_upsert_requestmetrics removed
--
-- Deployment:
-- 1. Run this script: psql -d yourdb -f batch_processing_setup.sql
-- 2. Backfill existing data: SELECT backfill_existing_metrics(1000);
-- 3. Set up cron job: */30 * * * * psql -d yourdb -c "SELECT process_unprocessed_metrics(500);"
-- =============================================================================

-- Create index for fast unprocessed lookup
CREATE INDEX IF NOT EXISTS idx_requestlog_unprocessed 
ON resource_server_async_requestlog (metrics_processed, timestamp_compute_response) 
WHERE metrics_processed = FALSE AND timestamp_compute_response IS NOT NULL;

-- Create lightweight trigger to mark records for processing
CREATE OR REPLACE FUNCTION fn_mark_for_processing() RETURNS trigger AS $$
BEGIN
  -- Mark for processing when we get response data
  IF NEW.timestamp_compute_response IS NOT NULL AND 
     NEW.result IS NOT NULL AND 
     NEW.result != '' THEN
    NEW.metrics_processed := FALSE;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_mark_for_processing
BEFORE INSERT OR UPDATE OF result, timestamp_compute_response
ON resource_server_async_requestlog
FOR EACH ROW EXECUTE FUNCTION fn_mark_for_processing();

-- Main batch processor function
CREATE OR REPLACE FUNCTION process_unprocessed_metrics(batch_size INTEGER DEFAULT 1000)
RETURNS INTEGER AS $$
DECLARE
  processed_count INTEGER := 0;
  batch_ids UUID[];
BEGIN
  -- Get batch of unprocessed IDs
  SELECT ARRAY(
    SELECT rl.id 
    FROM resource_server_async_requestlog rl
    JOIN resource_server_async_accesslog al ON rl.access_log_id = al.id
    WHERE rl.metrics_processed = FALSE
      AND rl.timestamp_compute_response IS NOT NULL
      AND rl.result IS NOT NULL 
      AND rl.result != ''
      AND al.status_code >= 200 
      AND al.status_code < 300
      AND rl.result ~ '"total_tokens"\s*:\s*\d+'
    ORDER BY rl.timestamp_compute_response
    LIMIT batch_size
    FOR UPDATE SKIP LOCKED  -- Prevent conflicts with multiple workers
  ) INTO batch_ids;

  -- Exit if nothing to process
  IF array_length(batch_ids, 1) IS NULL THEN
    RETURN 0;
  END IF;

  -- Process the batch - insert/update metrics
  INSERT INTO resource_server_async_requestmetrics
    (request_id, cluster, framework, model, status_code,
     prompt_tokens, completion_tokens, total_tokens,
     response_time_sec, throughput_tokens_per_sec,
     timestamp_compute_request, timestamp_compute_response, created_at)
  SELECT 
    rl.id,
    rl.cluster,
    rl.framework, 
    rl.model,
    al.status_code,
    COALESCE((regexp_match(rl.result, '"prompt_tokens"\s*:\s*(\d+)'))[1]::bigint, NULL),
    COALESCE((regexp_match(rl.result, '"completion_tokens"\s*:\s*(\d+)'))[1]::bigint, NULL),
    COALESCE((regexp_match(rl.result, '"total_tokens"\s*:\s*(\d+)'))[1]::bigint, NULL),
    EXTRACT(EPOCH FROM (rl.timestamp_compute_response - rl.timestamp_compute_request)),
    COALESCE((regexp_match(rl.result, '"throughput_tokens_per_second"\s*:\s*([0-9.]+)'))[1]::double precision, NULL),
    rl.timestamp_compute_request,
    rl.timestamp_compute_response,
    NOW()
  FROM resource_server_async_requestlog rl
  JOIN resource_server_async_accesslog al ON rl.access_log_id = al.id
  WHERE rl.id = ANY(batch_ids)
  ON CONFLICT (request_id) DO UPDATE SET
     cluster = EXCLUDED.cluster,
     framework = EXCLUDED.framework,
     model = EXCLUDED.model,
     status_code = EXCLUDED.status_code,
     prompt_tokens = EXCLUDED.prompt_tokens,
     completion_tokens = EXCLUDED.completion_tokens,
     total_tokens = EXCLUDED.total_tokens,
     response_time_sec = EXCLUDED.response_time_sec,
     throughput_tokens_per_sec = EXCLUDED.throughput_tokens_per_sec,
     timestamp_compute_request = EXCLUDED.timestamp_compute_request,
     timestamp_compute_response = EXCLUDED.timestamp_compute_response;

  -- Mark batch as processed
  UPDATE resource_server_async_requestlog 
  SET metrics_processed = TRUE
  WHERE id = ANY(batch_ids);
  
  GET DIAGNOSTICS processed_count = ROW_COUNT;
  RETURN processed_count;
END;
$$ LANGUAGE plpgsql;

-- Monitoring function to check unprocessed records
CREATE OR REPLACE FUNCTION get_unprocessed_metrics_count()
RETURNS TABLE(
  total_unprocessed BIGINT,
  oldest_unprocessed TIMESTAMP WITH TIME ZONE,
  newest_unprocessed TIMESTAMP WITH TIME ZONE
) AS $$
BEGIN
  RETURN QUERY
  SELECT 
    COUNT(*),
    MIN(rl.timestamp_compute_response),
    MAX(rl.timestamp_compute_response)
  FROM resource_server_async_requestlog rl
  JOIN resource_server_async_accesslog al ON rl.access_log_id = al.id
  WHERE rl.metrics_processed = FALSE
    AND rl.timestamp_compute_response IS NOT NULL
    AND rl.result IS NOT NULL 
    AND rl.result != ''
    AND al.status_code >= 200 
    AND al.status_code < 300
    AND rl.result ~ '"total_tokens"\s*:\s*\d+';
END;
$$ LANGUAGE plpgsql;

-- One-time backfill function for existing data
CREATE OR REPLACE FUNCTION backfill_existing_metrics(batch_size INTEGER DEFAULT 5000)
RETURNS INTEGER AS $$
DECLARE
  total_processed INTEGER := 0;
  batch_processed INTEGER;
BEGIN
  RAISE NOTICE 'Starting backfill process...';
  
  -- First, mark all existing records as unprocessed if they have the required data
  UPDATE resource_server_async_requestlog 
  SET metrics_processed = FALSE
  WHERE metrics_processed IS NULL
    AND timestamp_compute_response IS NOT NULL
    AND result IS NOT NULL 
    AND result != '';
  
  RAISE NOTICE 'Marked existing records as unprocessed. Processing in batches of %...', batch_size;
  
  -- Process in batches to avoid overwhelming the system
  LOOP
    SELECT process_unprocessed_metrics(batch_size) INTO batch_processed;
    total_processed := total_processed + batch_processed;
    
    -- Log progress
    IF batch_processed > 0 THEN
      RAISE NOTICE 'Processed % records (total: %)', batch_processed, total_processed;
    END IF;
    
    -- Exit when no more to process
    EXIT WHEN batch_processed = 0;
    
    -- Small delay to prevent overwhelming the system
    PERFORM pg_sleep(0.1);
  END LOOP;
  
  RAISE NOTICE 'Backfill complete. Total processed: %', total_processed;
  RETURN total_processed;
END;
$$ LANGUAGE plpgsql;

-- Monitoring view for easy status checking
CREATE OR REPLACE VIEW v_metrics_processing_status AS
SELECT 
  COUNT(*) FILTER (WHERE metrics_processed = FALSE) as unprocessed_count,
  COUNT(*) FILTER (WHERE metrics_processed = TRUE) as processed_count,
  COUNT(*) as total_count,
  ROUND(
    (COUNT(*) FILTER (WHERE metrics_processed = TRUE)::NUMERIC / 
     NULLIF(COUNT(*), 0) * 100), 2
  ) as processed_percentage,
  MIN(timestamp_compute_response) FILTER (WHERE metrics_processed = FALSE) as oldest_unprocessed,
  MAX(timestamp_compute_response) FILTER (WHERE metrics_processed = FALSE) as newest_unprocessed
FROM resource_server_async_requestlog 
WHERE timestamp_compute_response IS NOT NULL;

-- =============================================================================
-- USAGE INSTRUCTIONS
-- =============================================================================
/*

AFTER RUNNING THIS SCRIPT:

1. Backfill existing data (run once):
   SELECT backfill_existing_metrics(1000);

2. Set up cron job for ongoing processing:
   Add to crontab: crontab -e
   */30 * * * * psql -d yourdb -c "SELECT process_unprocessed_metrics(500);" >/dev/null 2>&1

3. Monitor the system:
   SELECT * FROM v_metrics_processing_status;
   SELECT * FROM get_unprocessed_metrics_count();

4. Manual processing if needed:
   SELECT process_unprocessed_metrics(1000);

5. For high-volume periods, run multiple workers:
   - SKIP LOCKED prevents conflicts between workers
   - Adjust batch_size based on system capacity (100-2000)

PERFORMANCE TUNING:
- For light load: Run every 1-2 minutes with batch_size=200
- For heavy load: Run every 30 seconds with batch_size=500-1000
- For very heavy load: Run multiple workers with different batch sizes

MONITORING QUERIES:
- Current backlog: SELECT unprocessed_count FROM v_metrics_processing_status;
- Processing lag: SELECT oldest_unprocessed FROM v_metrics_processing_status;
- Success rate: SELECT processed_percentage FROM v_metrics_processing_status;

*/