from locust import HttpUser, task, between
import json
import random
import os
#import socket
#hostname = socket.gethostname()
#os.environ['no_proxy'] = hostname
auth_token = "AgoblrnM85957XVoQEaprbDJgN6O1o2K3DQvGvzJYzrm9jJ650TgC26vex5ElD7Y50g0PgeqPm8oMOI9OeylNIm18Mk"
class VLLMUser(HttpUser):
    wait_time = between(1, 3)  # Wait 1-3 seconds between tasks

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
        response = self.client.post("/v1/chat/completions", json=payload, headers=headers)
        
        if response.status_code == 200:
            # You can add additional checks here
            pass
        else:
            response.failure(f"Got unexpected response code: {response.status_code}")

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
        
        response = self.client.post("/v1/completions", json=payload, headers=headers)
        
        if response.status_code == 200:
            # You can add additional checks here
            pass
        else:
            response.failure(f"Got unexpected response code: {response.status_code}")