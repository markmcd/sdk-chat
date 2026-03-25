import sys
import os
import json
from google import genai

def search_documents(display_name):
    client = genai.Client()

    # Load store name
    STORE_NAME_FILE = ".store_name"
    if not os.path.exists(STORE_NAME_FILE):
        print("Error: .store_name file not found.")
        return
        
    with open(STORE_NAME_FILE, "r") as f:
        store_name = f.read().strip()

    print(f"Searching for documents with display_name '{display_name}' in store '{store_name}'...\n")

    # List all documents in the store
    try:
        docs = list(client.file_search_stores.documents.list(parent=store_name))
    except Exception as e:
        print(f"Error listing documents: {e}")
        return

    found = False
    for doc in docs:
        if getattr(doc, 'display_name', None) == display_name:
            try:
                # Use model_dump_json for Pydantic-based SDK models
                if hasattr(doc, 'model_dump_json'):
                    print(json.dumps(json.loads(doc.model_dump_json()), indent=2))
                elif hasattr(doc, 'to_json_dict'):
                    print(json.dumps(doc.to_json_dict(), indent=2))
                else:
                    # Fallback to the dictionary method
                    print(json.dumps(doc.dict(), indent=2, default=str))
            except Exception as e:
                # Last resort fallback to printing the object
                print(doc)
            print("-" * 40)
            found = True

    if not found:
        # Extra check: sometimes display_name matches display_name on list but searching requires exact match
        print(f"No documents found with display_name '{display_name}'.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python search_docs.py <display_name>")
    else:
        search_documents(sys.argv[1])
