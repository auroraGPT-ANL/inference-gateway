#!/usr/bin/env python3
"""
Model Health Monitoring Script

This script monitors the health of all running models by:
1. Querying qstat to get list of running models
2. Checking the /health endpoint for each model
3. Sending email alerts if models are unhealthy (timeout > 60s or non-200 response)

Designed to run as a cron job every 5 minutes.
"""

import os
import sys
import json
import asyncio
import logging
import subprocess
from datetime import datetime
from typing import List, Dict, Tuple

# Add parent directory to path to import Django modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'inference_gateway.settings')
import django
django.setup()

from django.conf import settings
from asgiref.sync import sync_to_async
from resource_server.models import Endpoint
import utils.globus_utils as globus_utils

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/model_health_monitor.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


class ModelHealthMonitor:
    """Monitor health of running models and send alerts"""
    
    def __init__(self):
        # Email configuration from environment
        self.alert_email_to = os.getenv('ALERT_EMAIL_TO', '').split()
        
        # Clusters to monitor (default to sophia)
        self.clusters_to_monitor = os.getenv('CLUSTERS_TO_MONITOR', 'sophia').split(',')
        
        log.info("Using existing vLLM function for health checks (supports GET /health)")
        
    async def get_running_models(self, cluster: str) -> List[Dict]:
        """Get list of running models from qstat"""
        try:
            # Get Globus Compute client and executor
            gcc = globus_utils.get_compute_client_from_globus_app()
            gce = globus_utils.get_compute_executor(client=gcc)
            
            # Import the function from utils
            from resource_server_async.utils import get_qstat_details
            
            # Get qstat details
            result, task_uuid, error_message, error_code = await get_qstat_details(
                cluster, gcc=gcc, gce=gce, timeout=60
            )
            
            if error_message:
                log.error(f"Error getting qstat for {cluster}: {error_message}")
                return []
            
            # Parse the result
            qstat_data = json.loads(result)
            
            # Extract running models
            running_models = []
            if 'running' in qstat_data:
                for job in qstat_data['running']:
                    if job.get('Model Status') == 'running' and 'Models' in job:
                        # Split comma-separated models
                        models_list = [m.strip() for m in job['Models'].split(',') if m.strip()]
                        for model in models_list:
                            running_models.append({
                                'cluster': cluster,
                                'framework': job.get('Framework', 'vllm'),
                                'model': model,
                                'job_id': job.get('Job ID', ''),
                                'host': job.get('Host Name', '')
                            })
            
            log.info(f"Found {len(running_models)} running models on {cluster}")
            return running_models
            
        except Exception as e:
            log.error(f"Exception getting running models for {cluster}: {e}")
            return []
    
    async def get_endpoint_info(self, model: str) -> Tuple[str, str, int]:
        """Get endpoint UUID, function UUID, and API port for a model from database"""
        try:
            # Use sync_to_async to query Django ORM from async context
            get_endpoint = sync_to_async(Endpoint.objects.get)
            endpoint = await get_endpoint(model=model)
            return endpoint.endpoint_uuid, endpoint.function_uuid, endpoint.api_port
        except Endpoint.DoesNotExist:
            log.warning(f"No endpoint found in database for model: {model}")
            return None, None, None
        except Exception as e:
            log.error(f"Error querying endpoint for model {model}: {e}")
            return None, None, None
    
    async def check_model_health(self, model_info: Dict, endpoint_uuid: str, function_uuid: str, api_port: int) -> Dict:
        """Check health of a single model using the existing vLLM function with /health endpoint"""
        model_name = model_info['model']
        
        try:
            # Get Globus Compute client and executor
            gcc = globus_utils.get_compute_client_from_globus_app()
            gce = globus_utils.get_compute_executor(client=gcc)
            
            # Prepare parameters to call /health endpoint using existing vLLM function
            # The vLLM function detects "health" in endpoint and makes GET request to /health
            health_params = {
                'model_params': {
                    'openai_endpoint': 'health',
                    'api_port': api_port,
                    'model': model_name
                }
            }
            
            log.info(f"Checking health for {model_name} on port {api_port} (endpoint: {endpoint_uuid})")
            
            # Submit health check task with 90 second timeout (60s for health + 30s buffer)
            result, task_uuid, error_message, error_code = await globus_utils.submit_and_get_result(
                gce, 
                endpoint_uuid, 
                function_uuid,
                resources_ready=True,
                data=health_params,
                timeout=90
            )
            
            if error_message:
                log.error(f"Health check failed for {model_name}: {error_message}")
                return {
                    'model': model_name,
                    'cluster': model_info['cluster'],
                    'job_id': model_info['job_id'],
                    'host': model_info['host'],
                    'api_port': api_port,
                    'healthy': False,
                    'error': error_message,
                    'task_uuid': task_uuid
                }
            
            # Parse the result from vLLM function
            # The result should contain response_time and status information
            try:
                health_result = json.loads(result)
                
                # Check if the health endpoint returned successfully
                # A successful health check should have response_time and no errors
                is_healthy = (
                    'response_time' in health_result and
                    health_result.get('response_time', 999) < 60 and
                    'error' not in health_result
                )
                
                return {
                    'model': model_name,
                    'cluster': model_info['cluster'],
                    'job_id': model_info['job_id'],
                    'host': model_info['host'],
                    'api_port': api_port,
                    'healthy': is_healthy,
                    'response_time': health_result.get('response_time'),
                    'status_code': health_result.get('status_code', 200) if is_healthy else None,
                    'response_body': str(health_result)[:200]  # Truncate for logging
                }
            except (json.JSONDecodeError, KeyError) as e:
                log.error(f"Error parsing health check result for {model_name}: {e}")
                return {
                    'model': model_name,
                    'cluster': model_info['cluster'],
                    'job_id': model_info['job_id'],
                    'host': model_info['host'],
                    'api_port': api_port,
                    'healthy': False,
                    'error': f'Failed to parse health check result: {str(e)}'
                }
            
        except asyncio.TimeoutError:
            log.error(f"Timeout checking health for {model_name}")
            return {
                'model': model_name,
                'cluster': model_info['cluster'],
                'job_id': model_info['job_id'],
                'host': model_info['host'],
                'api_port': api_port,
                'healthy': False,
                'error': 'Health check timed out after 90 seconds'
            }
        except Exception as e:
            log.error(f"Exception checking health for {model_name}: {e}")
            return {
                'model': model_name,
                'cluster': model_info['cluster'],
                'job_id': model_info['job_id'],
                'host': model_info['host'],
                'api_port': api_port,
                'healthy': False,
                'error': f'Exception: {str(e)}'
            }
    
    async def monitor_all_models(self) -> Dict:
        """Monitor health of all running models across all clusters"""
        all_results = {
            'timestamp': datetime.now().isoformat(),
            'clusters_checked': [],
            'healthy_models': [],
            'unhealthy_models': []
        }
        
        # Get running models for each cluster
        for cluster in self.clusters_to_monitor:
            cluster = cluster.strip()
            log.info(f"Checking cluster: {cluster}")
            all_results['clusters_checked'].append(cluster)
            
            running_models = await self.get_running_models(cluster)
            
            # Check health of each model
            for model_info in running_models:
                endpoint_uuid, function_uuid, api_port = await self.get_endpoint_info(model_info['model'])
                
                if not endpoint_uuid or not function_uuid or not api_port:
                    log.warning(f"Skipping {model_info['model']} - no endpoint configuration found")
                    all_results['unhealthy_models'].append({
                        'model': model_info['model'],
                        'cluster': cluster,
                        'error': 'No endpoint configuration found in database'
                    })
                    continue
                
                # Check health using existing vLLM function with /health endpoint
                health_result = await self.check_model_health(model_info, endpoint_uuid, function_uuid, api_port)
                
                if health_result.get('healthy'):
                    all_results['healthy_models'].append(health_result)
                    log.info(f"✓ {health_result['model']} is healthy (response time: {health_result.get('response_time', 'N/A')}s)")
                else:
                    all_results['unhealthy_models'].append(health_result)
                    log.warning(f"✗ {health_result['model']} is UNHEALTHY: {health_result.get('error', 'Unknown error')}")
        
        return all_results
    
    def send_alert_email(self, results: Dict):
        """Send email alert if there are unhealthy models using sendmail"""
        if not results['unhealthy_models']:
            log.info("All models are healthy. No alert email needed.")
            return
        
        if not self.alert_email_to or not any(self.alert_email_to):
            log.warning("No alert email recipients configured. Skipping email.")
            return
        
        try:
            # Build email content
            subject = f"⚠️ Model Health Alert - {len(results['unhealthy_models'])} Unhealthy Model(s)"
            
            email_content = f"""Subject: {subject}

Model Health Monitoring Alert
==============================

Timestamp: {results['timestamp']}
Clusters Checked: {', '.join(results['clusters_checked'])}

UNHEALTHY MODELS ({len(results['unhealthy_models'])}):
"""
            
            for model in results['unhealthy_models']:
                email_content += f"""
---
Model: {model.get('model', 'Unknown')}
Cluster: {model.get('cluster', 'Unknown')}
Job ID: {model.get('job_id', 'Unknown')}
Host: {model.get('host', 'Unknown')}
Status Code: {model.get('status_code', 'N/A')}
Response Time: {model.get('response_time', 'N/A')}
Error: {model.get('error', 'Unknown error')}
"""
            
            email_content += f"""

HEALTHY MODELS ({len(results['healthy_models'])}):
"""
            for model in results['healthy_models']:
                email_content += f"  ✓ {model.get('model')} (response time: {model.get('response_time', 'N/A')}s)\n"
            
            email_content += """

---
This is an automated alert from the Inference Gateway Model Health Monitor.
"""
            
            # Write email content to temporary file
            email_file = '/tmp/model_health_alert_email.txt'
            with open(email_file, 'w') as f:
                f.write(email_content)
            
            # Send email using sendmail
            recipients = ' '.join(self.alert_email_to)
            result = subprocess.run(
                f'sendmail {recipients} < {email_file}',
                shell=True,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                log.info(f"Alert email sent to {recipients}")
            else:
                log.error(f"Failed to send alert email: {result.stderr}")
            
            # Clean up temp file
            try:
                os.remove(email_file)
            except:
                pass
            
        except Exception as e:
            log.error(f"Failed to send alert email: {e}")
    
    async def run(self):
        """Main monitoring loop"""
        log.info("=" * 80)
        log.info("Starting Model Health Monitoring")
        log.info("=" * 80)
        
        try:
            results = await self.monitor_all_models()
            
            # Log summary
            log.info("-" * 80)
            log.info("SUMMARY:")
            log.info(f"  Healthy models: {len(results['healthy_models'])}")
            log.info(f"  Unhealthy models: {len(results['unhealthy_models'])}")
            log.info("-" * 80)
            
            # Send alert email if needed
            self.send_alert_email(results)
            
            # Save results to file
            results_file = '/tmp/model_health_last_check.json'
            with open(results_file, 'w') as f:
                json.dump(results, f, indent=2)
            log.info(f"Results saved to {results_file}")
            
            return results
            
        except Exception as e:
            log.error(f"Error in monitoring run: {e}", exc_info=True)
            raise


def main():
    """Main entry point"""
    monitor = ModelHealthMonitor()
    results = asyncio.run(monitor.run())
    
    # Exit with error code if there are unhealthy models
    if results['unhealthy_models']:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
