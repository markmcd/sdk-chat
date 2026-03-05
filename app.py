import argparse
import json
import os
import subprocess
import time
import yaml
from google import genai
from google.genai import types

STORE_NAME_FILE = ".store_name"
PACKAGES_DB = "packages.json"
PACKAGES_YAML = "packages.yaml"

def load_packages():
    if not os.path.exists(PACKAGES_YAML):
        print(f"Error: {PACKAGES_YAML} not found.")
        exit(1)
    with open(PACKAGES_YAML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

PACKAGES = load_packages()

def init_client():
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable is not set.")
        exit(1)
    return genai.Client()

def get_store(client):
    if os.path.exists(STORE_NAME_FILE):
        with open(STORE_NAME_FILE, "r") as f:
            store_name = f.read().strip()
            # Verify it exists
            try:
                client.file_search_stores.get(name=store_name)
                return store_name
            except Exception:
                pass # Doesn't exist, create a new one

    print("Creating new File Search Store...")
    store = client.file_search_stores.create(
        config={'display_name': 'AI_SDK_Index'}
    )
    with open(STORE_NAME_FILE, "w") as f:
        f.write(store.name)
    return store.name

def ingest(update=False):
    client = init_client()
    store_name = get_store(client)
    
    # Load existing DB
    db = {}
    if os.path.exists(PACKAGES_DB):
        with open(PACKAGES_DB, "r") as f:
            try:
                existing_list = json.load(f)
                db = {pkg["package"]: pkg for pkg in existing_list}
            except json.JSONDecodeError:
                db = {}
    
    pending_operations = []
    
    for pkg in PACKAGES:
        pkg_name = pkg["package"]
        
        # If it's already in the DB and we have a file_name, we're definitely done.
        # If we have a pending_operation_name, we should resume polling.
        if pkg_name in db and not update:
            if db[pkg_name].get("file_name"):
                print(f"Skipping {pkg_name} (already indexed). Use --update to refresh.")
                continue
            elif db[pkg_name].get("pending_operation_name"):
                print(f"Resuming indexing for {pkg_name} from previous session...")
                # Create a placeholder operation object
                dummy_op = types.UploadToFileSearchStoreOperation(name=db[pkg_name]["pending_operation_name"])
                op = client.operations.get(dummy_op)
                pending_operations.append((pkg_name, pkg, op, None))
                continue

        print(f"\n--- {'Updating' if update else 'Ingesting'} {pkg_name} ---")
        
        # If updating, delete the old file
        if update and pkg_name in db and db[pkg_name].get("file_name"):
            old_file_id = db[pkg_name]["file_name"]
            print(f"Removing old version of {pkg_name} ({old_file_id})...")
            try:
                client.files.delete(name=old_file_id)
            except Exception as e:
                print(f"Warning: Could not delete old file: {e}")

        filename = f"{pkg_name}.txt"
        
        # Run gitingest
        print(f"Downloading and processing with gitingest from {pkg['url']}...")
        subprocess.run(["gitingest", pkg["url"], "-o", filename], check=True)
        
        # Read the file and prepend metadata
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
            
        metadata = f"Owner: {pkg['owner']}\nPackage: {pkg['package']}\nLanguage: {pkg['language']}\nURL: {pkg['url']}\n\n"
        content = metadata + content
        
        print(f"Uploading {pkg_name} to File Search Store {store_name}...")
        
        # Unique filename for upload
        unique_filename = f"{int(time.time())}_{pkg_name}.txt"
        with open(unique_filename, "w", encoding="utf-8") as f:
            f.write(content)

        # Retry logic for upload
        max_retries = 3
        operation = None
        for attempt in range(max_retries):
            try:
                operation = client.file_search_stores.upload_to_file_search_store(
                    file=unique_filename,
                    file_search_store_name=store_name,
                    config={'display_name': pkg_name}
                )
                break
            except Exception as e:
                print(f"  Upload attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(10)
                else:
                    raise e
        
        # IMMEDIATELY save the operation name to the local DB so we don't lose it if we crash
        pkg_entry = pkg.copy()
        pkg_entry["pending_operation_name"] = operation.name
        pkg_entry["last_ingested"] = time.ctime()
        db[pkg_name] = pkg_entry
        with open(PACKAGES_DB, "w") as f:
            json.dump(list(db.values()), f, indent=2)

        pending_operations.append((pkg_name, pkg, operation, unique_filename))
        
    if pending_operations:
        print("\nWaiting for all indexing operations to complete on the Gemini backend...")
        for pkg_name, pkg, operation, unique_filename in pending_operations:
            print(f"Waiting for {pkg_name} to finish indexing...")
            while not operation.done:
                time.sleep(5)
                print(f"  Still indexing {pkg_name}...")
                operation = client.operations.get(operation)
                
            print(f"Finished indexing {pkg_name}.")

            # Cleanup the unique file
            if os.path.exists(unique_filename):
                os.remove(unique_filename)

            # Extract file name from the operation result
            uploaded_file_name = None
            # Based on typical google-genai response structure for this operation
            if hasattr(operation, 'response') and operation.response:
                 if hasattr(operation.response, 'name'):
                     uploaded_file_name = operation.response.name
            
            # Update local DB entry
            pkg_entry = pkg.copy()
            pkg_entry["file_name"] = uploaded_file_name
            pkg_entry["last_ingested"] = time.ctime()
            db[pkg_name] = pkg_entry
        
    with open(PACKAGES_DB, "w") as f:
        json.dump(list(db.values()), f, indent=2)
        
    print("\nIngestion process complete!")

def ask(query):
    client = init_client()
    if not os.path.exists(STORE_NAME_FILE):
        print("No store found. Please run 'python app.py ingest' first.")
        return
        
    with open(STORE_NAME_FILE, "r") as f:
        store_name = f.read().strip()
        
    print(f"Querying store {store_name}...\n")
    try:
        response = client.models.generate_content(
            model="gemini-3-pro-preview",
            contents=query,
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(
                        file_search=types.FileSearch(
                            file_search_store_names=[store_name]
                        )
                    )
                ]
            )
        )
        print("Response:")
        print(response.text)
    except Exception as e:
        print(f"Error querying model: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI SDK Document Ingestion and Query App")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    ingest_parser = subparsers.add_parser("ingest", help="Ingest repositories and build index")
    ingest_parser.add_argument("--update", action="store_true", help="Force update of existing packages")
    
    ask_parser = subparsers.add_parser("ask", help="Ask a question against the built index")
    ask_parser.add_argument("query", type=str, help="The question to ask")
    
    args = parser.parse_args()
    
    if args.command == "ingest":
        ingest(update=args.update)
    elif args.command == "ask":
        ask(args.query)
