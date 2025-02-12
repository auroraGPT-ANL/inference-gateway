# Setting Cron Jobs for Inference VM

### Update Batch Status

This is to periodically update the status of all ongoing batch jobs, and add the results in the database while they are available from the Globus server (which deletes task results after 3 days).

On the VM, as the `webportal` user, set a crontab with the following command:
```bash
crontab -e
```
and include the following line to execute the update command every minute:
```bash
*/1 * * * * /home/webportal/inference-gateway/cron_jobs/update_batch_status.sh
```