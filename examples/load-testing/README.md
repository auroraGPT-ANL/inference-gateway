# Locust for load testing
An open source load testing tool.
Define user behaviour with Python code, and swarm your system with millions of simultaneous users.

# Installation
```bash
pip install locust
```

# How to use
* From the current folder i.e. the location where `locustfile.py` exists run
```bash
locust
```

* Head to `http://127.0.0.1:8089/` on your browser

* Values I usually put in each run for the following
 Number of users: 5
 Ramp up: 1
 Host: `https://data-portal-dev.cels.anl.gov/resource_server/sophia/vllm`
 Advanced Options: Run time: 10m
 Start!!

 I do combinations of No of users and ramp up as follows `5 1, 10 5, 50 10, 100 10, 200 50, 500 100, 1000 100`

* Look at Charts and then Download report after 10 minutes when runs stop.

mkdir -p load-testing/datasets
mkdir -p load-testing/results

