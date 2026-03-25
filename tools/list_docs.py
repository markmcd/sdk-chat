import os
import json
from google import genai

def init_client():
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable is not set.")
        exit(1)
    return genai.Client()

def list_documents():
    client = init_client()
    store_name_file = ".store_name"
    
    if not os.path.exists(store_name_file):
        print("Error: .store_name file not found.")
        return

    with open(store_name_file, "r") as f:
        store_name = f.read().strip()

    print(f"Listing documents for store: {store_name}")
    try:
        # Use the list method on documents. Parent is the store name.
        docs = list(client.file_search_stores.documents.list(parent=store_name))
        if not docs:
            print("No documents found in the store.")
        else:
            for doc in docs:
                display_name = getattr(doc, 'display_name', 'No display name')
                print(f"- {display_name} ({doc.name})")
    except Exception as e:
        print(f"Error listing documents: {e}")

if __name__ == "__main__":
    list_documents()
