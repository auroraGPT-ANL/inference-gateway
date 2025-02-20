echo "-------------------" >> /home/webportal/inference-gateway/cron_jobs/update_batch_status_output.log
date '+%d/%m/%Y_%H:%M:%S' >> /home/webportal/inference-gateway/cron_jobs/update_batch_status_output.log
echo "-------------------" >> /home/webportal/inference-gateway/cron_jobs/update_batch_status_error.log
date '+%d/%m/%Y_%H:%M:%S' >> /home/webportal/inference-gateway/cron_jobs/update_batch_status_error.log
/home/webportal/inference-gateway/.venv/bin/python /home/webportal/inference-gateway/manage.py update_batch_status
