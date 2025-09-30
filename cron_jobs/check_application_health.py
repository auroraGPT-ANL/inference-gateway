#!/usr/bin/env python3
"""
Application Health Check Script

This script checks the health of the Inference Gateway application by:
1. Checking Redis connectivity
2. Checking PostgreSQL connectivity  
3. Checking Globus Compute connectivity
4. Sending alerts if any component is unhealthy

Designed to run as a cron job every 5 minutes.
"""

import os
import sys
import json
import logging
import subprocess
from datetime import datetime

# Add parent directory to path to import Django modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'inference_gateway.settings')
import django
django.setup()

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from resource_server.models import Endpoint
import utils.globus_utils as globus_utils

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/tmp/application_health.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


class ApplicationHealthChecker:
    """Check health of application components"""
    
    def __init__(self):
        # Email configuration from environment
        self.alert_email_to = os.getenv('ALERT_EMAIL_TO', '').split()
    
    def check_redis(self) -> dict:
        """Check Redis connectivity"""
        try:
            # Try to set and get a test value
            test_key = "health_check_test"
            test_value = f"test_{datetime.now().timestamp()}"
            
            cache.set(test_key, test_value, 60)
            retrieved_value = cache.get(test_key)
            
            if retrieved_value == test_value:
                cache.delete(test_key)
                return {
                    'component': 'Redis',
                    'status': 'healthy',
                    'message': 'Redis connection successful'
                }
            else:
                return {
                    'component': 'Redis',
                    'status': 'unhealthy',
                    'error': 'Redis get/set test failed - values do not match'
                }
        except Exception as e:
            return {
                'component': 'Redis',
                'status': 'unhealthy',
                'error': f'Redis connection failed: {str(e)}'
            }
    
    def check_postgres(self) -> dict:
        """Check PostgreSQL connectivity"""
        try:
            # Try a simple database query
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                
                if result and result[0] == 1:
                    # Also check if we can query a table
                    endpoint_count = Endpoint.objects.count()
                    return {
                        'component': 'PostgreSQL',
                        'status': 'healthy',
                        'message': f'PostgreSQL connection successful (found {endpoint_count} endpoints)'
                    }
                else:
                    return {
                        'component': 'PostgreSQL',
                        'status': 'unhealthy',
                        'error': 'PostgreSQL query returned unexpected result'
                    }
        except Exception as e:
            return {
                'component': 'PostgreSQL',
                'status': 'unhealthy',
                'error': f'PostgreSQL connection failed: {str(e)}'
            }
    
    def check_globus_compute(self) -> dict:
        """Check Globus Compute connectivity"""
        try:
            # Try to create a Globus Compute client
            gcc = globus_utils.get_compute_client_from_globus_app()
            
            # Try to get executor
            gce = globus_utils.get_compute_executor(client=gcc)
            
            if gcc and gce:
                return {
                    'component': 'Globus Compute',
                    'status': 'healthy',
                    'message': 'Globus Compute client and executor initialized successfully'
                }
            else:
                return {
                    'component': 'Globus Compute',
                    'status': 'unhealthy',
                    'error': 'Failed to initialize Globus Compute client or executor'
                }
        except Exception as e:
            return {
                'component': 'Globus Compute',
                'status': 'unhealthy',
                'error': f'Globus Compute initialization failed: {str(e)}'
            }
    
    def check_all_components(self) -> dict:
        """Check health of all application components"""
        results = {
            'timestamp': datetime.now().isoformat(),
            'overall_status': 'healthy',
            'components': []
        }
        
        log.info("Checking Redis...")
        redis_result = self.check_redis()
        results['components'].append(redis_result)
        if redis_result['status'] != 'healthy':
            results['overall_status'] = 'unhealthy'
        
        log.info("Checking PostgreSQL...")
        postgres_result = self.check_postgres()
        results['components'].append(postgres_result)
        if postgres_result['status'] != 'healthy':
            results['overall_status'] = 'unhealthy'
        
        log.info("Checking Globus Compute...")
        globus_result = self.check_globus_compute()
        results['components'].append(globus_result)
        if globus_result['status'] != 'healthy':
            results['overall_status'] = 'unhealthy'
        
        return results
    
    def send_alert_email(self, results: dict):
        """Send email alert if application is unhealthy using sendmail"""
        if results['overall_status'] == 'healthy':
            log.info("All application components are healthy. No alert email needed.")
            return
        
        if not self.alert_email_to or not any(self.alert_email_to):
            log.warning("No alert email recipients configured. Skipping email.")
            return
        
        try:
            # Build email content
            unhealthy_components = [c for c in results['components'] if c['status'] != 'healthy']
            subject = f"🔴 Application Health Alert - {len(unhealthy_components)} Component(s) Unhealthy"
            
            email_content = f"""Subject: {subject}

Application Health Monitoring Alert
====================================

Timestamp: {results['timestamp']}
Overall Status: {results['overall_status'].upper()}

COMPONENT STATUS:
"""
            
            for component in results['components']:
                status_icon = "✓" if component['status'] == 'healthy' else "✗"
                email_content += f"\n{status_icon} {component['component']}: {component['status'].upper()}\n"
                
                if 'message' in component:
                    email_content += f"   Message: {component['message']}\n"
                if 'error' in component:
                    email_content += f"   Error: {component['error']}\n"
            
            email_content += """

---
This is an automated alert from the Inference Gateway Application Health Monitor.
"""
            
            # Write email content to temporary file
            email_file = '/tmp/application_health_alert_email.txt'
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
    
    def run(self):
        """Main health check"""
        log.info("=" * 80)
        log.info("Starting Application Health Check")
        log.info("=" * 80)
        
        try:
            results = self.check_all_components()
            
            # Log summary
            log.info("-" * 80)
            log.info(f"Overall Status: {results['overall_status'].upper()}")
            for component in results['components']:
                status_icon = "✓" if component['status'] == 'healthy' else "✗"
                log.info(f"  {status_icon} {component['component']}: {component['status']}")
            log.info("-" * 80)
            
            # Send alert email if needed
            self.send_alert_email(results)
            
            # Save results to file
            results_file = '/tmp/application_health_last_check.json'
            with open(results_file, 'w') as f:
                json.dump(results, f, indent=2)
            log.info(f"Results saved to {results_file}")
            
            return results
            
        except Exception as e:
            log.error(f"Error in health check: {e}", exc_info=True)
            raise


def main():
    """Main entry point"""
    checker = ApplicationHealthChecker()
    results = checker.run()
    
    # Exit with error code if application is unhealthy
    if results['overall_status'] != 'healthy':
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
