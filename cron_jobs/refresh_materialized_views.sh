#/bin/bash
psql -U dataportaldev -d inferencegateway -c "REFRESH MATERIALIZED VIEW mv_overall_stats;
                                          REFRESH MATERIALIZED VIEW mv_user_details;
                                          REFRESH MATERIALIZED VIEW mv_model_requests;
                                          REFRESH MATERIALIZED VIEW mv_model_latency;
                                          REFRESH MATERIALIZED VIEW mv_users_per_model;
                                          REFRESH MATERIALIZED VIEW mv_model_throughput;
                                          REFRESH MATERIALIZED VIEW mv_weekly_usage;
                                          REFRESH MATERIALIZED VIEW mv_daily_usage_2_weeks;
                                          REFRESH MATERIALIZED VIEW mv_requests_per_user;"