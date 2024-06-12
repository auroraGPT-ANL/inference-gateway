#!/bin/bash

curl --location '140.221.70.43:5005/llm/v1/completions' \
--header 'Content-Type: application/json' \
--data '{"model": "mistral-7B-instruct-v0.3", "temperature": 0.2, "prompt": "List all proteins that interact with RAD51", "n_probs": 1}'


curl --location '140.221.70.43:5005/llm/v1/completions' \
--header 'Content-Type: application/json' \
--data '{"model": "meta-llama-3-8B-instruct", "temperature": 0.2, "prompt": "List all proteins that interact with RAD51", "n_probs": 1}'

curl --location '140.221.70.43:5005/llm/v1/completions' \
--header 'Content-Type: application/json' \
--data '{"model": "meta-llama-3-70B-instruct", "temperature": 0.2, "prompt": "List all proteins that interact with RAD51", "n_probs": 1}'