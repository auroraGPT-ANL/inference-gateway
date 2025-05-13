# Inference Globus Multi-User Endpoint

In order to benefit from external Jinja template files, make sure your Globus Compute Endpoint is upgraded to the latest version.

## Setup virtual environment

Create conda environment with Python 3.11.9 (in sync with CELS VM backend)
```bash
module use /soft/modulefiles
module load conda
conda create -n inference-mep python=3.11.9
```

Activate environment and install Globus Compute Endpoint
```bash
conda activate inference-mep
pip install globus-compute-endpoint==2.34.0
```

## Configure multi-user endpoint

Create the multi-user endpoint
```bash
globus-compute-endpoint configure sophia-inference-mep --multi-user
```

Test the multi-user endpoint
```bash
globus-compute-endpoint start sophia-inference-mep --debug
```

## Run multi-user endpoint in the background

Using `nohup`:
```bash
nohup globus-compute-endpoint start sophia-inference-mep & echo $! > ~/nohup_mep.pid
```

Check if the process is running:
```bash
mep_pid=$(cat ~/nohup_mep.pid)
ps $mep_pid
```

Stop the process:
```bash
kill <process_id>
```

Force kill if needed:
```bash
kill -9 <process_id>
```
