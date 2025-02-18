cd /home/webportal/inference-gateway
source .venv/bin/activate
echo "-------------------" >> /home/webportal/inference-gateway/cron_jobs/cron_logs.txt
date '+%d/%m/%Y_%H:%M:%S' >> /home/webportal/inference-gateway/cron_jobs/cron_logs.txt
echo "executing update_batch_status" >> /home/webportal/inference-gateway/cron_jobs/cron_logs.txt
python3 manage.py update_batch_status
