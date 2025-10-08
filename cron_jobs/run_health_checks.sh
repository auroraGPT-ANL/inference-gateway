#!/bin/bash
#
# Health Monitoring Runner Script
#
# This script runs both model health and application health checks.
# Useful for testing or manual execution.
#

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Change to project root
cd "$PROJECT_ROOT"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Check if virtual environment exists and activate it
if [ -d "venv/bin" ]; then
    print_status "$YELLOW" "Activating virtual environment..."
    source venv/bin/activate
elif [ -d ".venv/bin" ]; then
    print_status "$YELLOW" "Activating virtual environment..."
    source .venv/bin/activate
fi

# Determine Python command
if command -v python3 &> /dev/null; then
    PYTHON_CMD=python3
else
    PYTHON_CMD=python
fi

print_status "$YELLOW" "Using Python: $(which $PYTHON_CMD)"
print_status "$YELLOW" "Python version: $($PYTHON_CMD --version)"

# Parse command line arguments
CHECK_TYPE="${1:-all}"

echo ""
print_status "$YELLOW" "=================================="
print_status "$YELLOW" "Health Monitoring System"
print_status "$YELLOW" "=================================="
echo ""

# Function to run model health check
run_model_health() {
    print_status "$YELLOW" "Running Model Health Check..."
    echo "---"
    
    if $PYTHON_CMD cron_jobs/monitor_model_health.py; then
        print_status "$GREEN" "✓ Model health check completed successfully"
        
        # Show summary if results file exists
        if [ -f "/tmp/model_health_last_check.json" ]; then
            echo ""
            print_status "$YELLOW" "Summary:"
            
            # Try to use jq if available, otherwise use grep
            if command -v jq &> /dev/null; then
                healthy=$(cat /tmp/model_health_last_check.json | jq '.healthy_models | length')
                unhealthy=$(cat /tmp/model_health_last_check.json | jq '.unhealthy_models | length')
                print_status "$GREEN" "  Healthy models: $healthy"
                if [ "$unhealthy" -gt 0 ]; then
                    print_status "$RED" "  Unhealthy models: $unhealthy"
                else
                    print_status "$GREEN" "  Unhealthy models: $unhealthy"
                fi
            else
                cat /tmp/model_health_last_check.json | grep -E '"healthy_models"|"unhealthy_models"'
            fi
        fi
        return 0
    else
        print_status "$RED" "✗ Model health check failed"
        return 1
    fi
}

# Function to run application health check
run_application_health() {
    print_status "$YELLOW" "Running Application Health Check..."
    echo "---"
    
    if $PYTHON_CMD cron_jobs/check_application_health.py; then
        print_status "$GREEN" "✓ Application health check completed successfully"
        
        # Show summary if results file exists
        if [ -f "/tmp/application_health_last_check.json" ]; then
            echo ""
            print_status "$YELLOW" "Summary:"
            
            # Try to use jq if available
            if command -v jq &> /dev/null; then
                status=$(cat /tmp/application_health_last_check.json | jq -r '.overall_status')
                if [ "$status" == "healthy" ]; then
                    print_status "$GREEN" "  Overall status: $status"
                else
                    print_status "$RED" "  Overall status: $status"
                fi
                
                echo ""
                print_status "$YELLOW" "  Component Status:"
                cat /tmp/application_health_last_check.json | jq -r '.components[] | "    \(.component): \(.status)"'
            else
                cat /tmp/application_health_last_check.json | grep -E '"overall_status"|"component"|"status"'
            fi
        fi
        return 0
    else
        print_status "$RED" "✗ Application health check failed"
        return 1
    fi
}

# Run the appropriate checks
case "$CHECK_TYPE" in
    models)
        run_model_health
        exit_code=$?
        ;;
    application|app)
        run_application_health
        exit_code=$?
        ;;
    all|*)
        run_model_health
        model_exit=$?
        
        echo ""
        echo ""
        
        run_application_health
        app_exit=$?
        
        # Exit with error if either check failed
        if [ $model_exit -ne 0 ] || [ $app_exit -ne 0 ]; then
            exit_code=1
        else
            exit_code=0
        fi
        ;;
esac

echo ""
print_status "$YELLOW" "=================================="
echo ""

# Show log locations
print_status "$YELLOW" "Log files:"
echo "  Model health: /tmp/model_health_monitor.log"
echo "  Application health: /tmp/application_health.log"
echo ""
print_status "$YELLOW" "Results files:"
echo "  Model health: /tmp/model_health_last_check.json"
echo "  Application health: /tmp/application_health_last_check.json"

exit $exit_code
