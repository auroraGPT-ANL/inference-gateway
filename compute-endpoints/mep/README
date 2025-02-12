# Inference Globus Multi-User Endpoint

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
