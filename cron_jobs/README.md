# Setting Cron Jobs for Inference VM

### Update Batch Status

This is to periodically update the status of all ongoing batch jobs, and add the results in the database while they are available from the Globus server (which deletes task results after 3 days).

On the VM, as the `webportal` user, add a crontab with the following command:
```bash
crontab -e
```
and include the following line to execute the update command every 10 minutes:
```bash
*/10 * * * * /home/webportal/inference-gateway/cron_jobs/update_batch_status.sh >> /home/webportal/inference-gateway/cron_jobs/update_batch_status_output.log 2>> /home/webportal/inference-gateway/cron_jobs/update_batch_status_error.log
```

Make sure you set execution permission:
```bash
chmod u+x /home/webportal/inference-gateway/cron_jobs/update_batch_status.sh
```

### Dashboard

This is to periodically update the materialized views for the dashboard.

On the VM, as the `webportal` user, add a crontab with the following command:
```bash
crontab -e
```
and include the following line to execute the update command every 4 hours:
```bash
0 */4 * * * /home/webportal/inference-gateway/cron_jobs/refresh_materialized_views.sh
```

Make sure you set execution permission:
```bash
chmod u+x /home/webportal/inference-gateway/cron_jobs/refresh_materialized_views.sh
```
