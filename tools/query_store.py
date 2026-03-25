import os
import json
import requests

def query_store():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is not set.")
        exit(1)

    store_name_file = ".store_name"
    if not os.path.exists(store_name_file):
        print("Error: .store_name file not found.")
        return

    with open(store_name_file, "r") as f:
        store_name = f.read().strip()

    prompt = "show me how to make a client for letta to connect to Gemini"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={api_key}"
    
    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "tools": [{
            "fileSearch": {
                "fileSearchStoreNames": [store_name]
            }
        }]
    }

    print(f"Querying store: {store_name} via direct REST call...")
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        
        # Print the candidates text
        if "candidates" in data and len(data["candidates"]) > 0:
            text = data["candidates"][0]["content"]["parts"][0].get("text", "")
            print("\n--- Response ---\n")
            print(text)
        else:
            print("No response text found.")
            
        print("\n--- Raw ---\n")
        print(json.dumps(data, indent=2))
            
    except Exception as e:
        print(f"Error querying store: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"Response text: {e.response.text}")

if __name__ == "__main__":
    query_store()
