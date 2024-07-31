from locust import HttpUser, task, between
import json
import random
#import os
#import socket
#hostname = socket.gethostname()
#os.environ['no_proxy'] = hostname
f = open("access_token.txt", "r")
auth_token = f.read().strip()
class VLLMUser(HttpUser):
    wait_time = between(1, 3)  # Wait 1-3 seconds between tasks
    # @task
    # def list_endpoints(self):
    #     headers = {
    #         "Content-Type": "application/json",
    #         "Authorization": f"Bearer {auth_token}"  # Replace with actual API key if required
    #     }
        
    #     with self.client.get("/list-endpoints", headers=headers, catch_response=True) as response:
    #         if not response.status_code == 200:
    #             response.failure(f"Got unexpected response code: {response.status_code}, {response.text}")

    @task
    def chat_completion(self):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_token}"  # Replace with actual API key if required
        }
        
        # List of sample prompts
        prompts = [
            "Explain the concept of machine learning in simple terms.",
            "What are the main differences between Python and JavaScript?",
            "Write a short story about a robot learning to paint.",
            "Describe the process of photosynthesis.",
            "What are the key features of a good user interface design?"
        ]
        
        payload = {
            "model": "meta-llama/Meta-Llama-3-8B-Instruct",  # Adjust this to match your vllm model
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": random.choice(prompts)}
            ],
            "temperature": 0.7,
            "max_tokens": 150
        }

        with self.client.post("/v1/chat/completions", json=payload, headers=headers, catch_response=True, timeout=None) as response:
            if not response.status_code == 200:
                response.failure(f"Got unexpected response code: {response.status_code}, {response.text}")

    @task
    def text_completion(self):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_token}"  # Replace with actual API key if required
        }
        
        prompts = [
            "The capital of France is",
            "Machine learning is a subset of",
            "The three primary colors are",
            "In computer science, API stands for",
            "The main function of a CPU is to"
        ]
        
        payload = {
            "model": "meta-llama/Meta-Llama-3-8B-Instruct",  # Adjust this to match your vllm model
            "prompt": random.choice(prompts),
            "temperature": 0.7,
            "max_tokens": 50
        }
        
        with self.client.post("/v1/completions", json=payload, headers=headers, catch_response=True, timeout=None) as response:
            if not response.status_code == 200:
                response.failure(f"Got unexpected response code: {response.status_code}, {response.text}")
