import argparse
import datetime
import json
import os
import subprocess
import time
import yaml
import gitingest
from google import genai
from google.genai import types

STORE_NAME_FILE = ".store_name"
PACKAGES_DB = "packages.json"
PACKAGES_YAML = "packages.yaml"
SYSTEM_PROMPT_FILE = "system_prompt.txt"
FAILED_PACKAGES_FILE = "failed_packages.json"

def load_system_prompt():
    if os.path.exists(SYSTEM_PROMPT_FILE):
        with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "You are a helpful assistant."

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

def ingest(update=False, since=None, package=None):
    client = init_client()
    store_name = get_store(client)
    
    failed_packages = set()
    if os.path.exists(FAILED_PACKAGES_FILE):
        with open(FAILED_PACKAGES_FILE, "r") as f:
            try:
                failed_packages = set(json.load(f))
            except json.JSONDecodeError:
                pass
                
    # Sort PACKAGES to put failed ones at the end
    sorted_packages = sorted(PACKAGES, key=lambda p: p["package"] in failed_packages)
    
    if package:
        sorted_packages = [p for p in sorted_packages if p["package"] == package]
        if not sorted_packages:
            print(f"Error: Package '{package}' not found in {PACKAGES_YAML}")
            return
    
    since_dt = None
    if since:
        unit = since[-1]
        try:
            value = int(since[:-1])
            if unit == 'h':
                delta = datetime.timedelta(hours=value)
            elif unit == 'd':
                delta = datetime.timedelta(days=value)
            elif unit == 'm':
                delta = datetime.timedelta(minutes=value)
            else:
                raise ValueError("Unit must be h, d, or m")
            since_dt = datetime.datetime.now() - delta
        except Exception as e:
            print(f"Error parsing --since argument: {e}")
            exit(1)

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
    
    for pkg in sorted_packages:
        pkg_name = pkg["package"]
        
        # If it's already in the DB and we have a file_name, we're definitely done.
        # If we have a pending_operation_name, we should resume polling.
        if pkg_name in db:
            is_recent = False
            if update and since_dt and db[pkg_name].get("last_ingested"):
                try:
                    last_time = time.strptime(db[pkg_name]["last_ingested"])
                    last_dt = datetime.datetime.fromtimestamp(time.mktime(last_time))
                    if last_dt > since_dt:
                        is_recent = True
                except ValueError:
                    pass

            if not update or is_recent:
                if db[pkg_name].get("pending_operation_name") and not is_recent:
                    print(f"Resuming indexing for {pkg_name} from previous session...")
                    # Create a placeholder operation object
                    dummy_op = types.UploadToFileSearchStoreOperation(name=db[pkg_name]["pending_operation_name"])
                    op = client.operations.get(dummy_op)
                    pending_operations.append((pkg_name, pkg, op, None, db[pkg_name].get("file_name")))
                    continue
                elif db[pkg_name].get("file_name"):
                    if is_recent:
                        print(f"Skipping {pkg_name} (updated within {since}).")
                    else:
                        print(f"Skipping {pkg_name} (already indexed). Use --update to refresh.")
                    continue

        print(f"\n--- {'Updating' if update else 'Ingesting'} {pkg_name} ---")
        
        # If updating, delete the old file
        if update and pkg_name in db and db[pkg_name].get("file_name"):
            old_file_id = db[pkg_name]["file_name"]
            
            # Remove the document from the file search store
            if db[pkg_name].get("pending_operation_name"):
                op_name = db[pkg_name]["pending_operation_name"]
                # Document name corresponds to the operation name but with /documents/ instead of /operations/
                doc_name = op_name.replace("/operations/", "/documents/")
                print(f"Removing old version of {pkg_name} from store ({doc_name})...")
                try:
                    client.file_search_stores.documents.delete(
                        name=doc_name,
                        config={'force': True}
                    )
                except Exception as e:
                    print(f"Warning: Could not delete old document from store: {e}")

            print(f"Cleaning up temporary file ({old_file_id})...")
            try:
                client.files.delete(name=old_file_id)
            except Exception as e:
                print(f"Warning: Could not delete old temporary file: {e}")

        filename = f"{pkg_name}.txt"
        
        # Run gitingest
        print(f"Downloading and processing with gitingest from {pkg['url']}...")
        exclude_patterns = set(pkg["exclude"]) if "exclude" in pkg else None
        
        try:
            gitingest.ingest(
                source=pkg["url"],
                exclude_patterns=exclude_patterns,
                output=filename
            )
        except Exception as e:
            print(f"  FAILED to process {pkg_name} with gitingest: {e}")
            failed_packages.add(pkg_name)
            with open(FAILED_PACKAGES_FILE, "w") as f:
                json.dump(list(failed_packages), f, indent=2)
            continue
        
        # Read the file and prepend metadata
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
            
        metadata = f"Owner: {pkg['owner']}\nPackage: {pkg['package']}\nLanguage: {pkg['language']}\nURL: {pkg['url']}\n\n"
        content = metadata + content
        
        print(f"Uploading {pkg_name} to File Search Store {store_name}...")
        
        # Retry logic for upload
        max_retries = 3
        operation = None
        unique_filename = ""
        for attempt in range(max_retries):
            try:
                # Unique filename for upload, generated fresh for each attempt
                unique_filename = f"{int(time.time())}_{pkg_name}_attempt{attempt}.txt"
                with open(unique_filename, "w", encoding="utf-8") as f:
                    f.write(content)
                    
                # Step 1: Upload the file normally
                uploaded_file = client.files.upload(
                    file=unique_filename,
                    config={'display_name': pkg_name}
                )
                
                print(f"  File uploaded to {uploaded_file.name}. Waiting for file processing...")
                while uploaded_file.state == "PROCESSING":
                    print(".", end="", flush=True)
                    time.sleep(2)
                    uploaded_file = client.files.get(name=uploaded_file.name)
                print() # newline after dots
                
                if uploaded_file.state == "FAILED":
                    raise Exception(f"File processing failed for {uploaded_file.name}")
                
                # Step 2: Import the file into the File Search Store with metadata
                operation = client.file_search_stores.import_file(
                    file_search_store_name=store_name,
                    file_name=uploaded_file.name,
                    config={
                        'custom_metadata': [
                            {'key': 'owner', 'string_value': pkg['owner']},
                            {'key': 'package', 'string_value': pkg['package']},
                            {'key': 'language', 'string_value': pkg['language']},
                            {'key': 'url', 'string_value': pkg['url']}
                        ]
                    }
                )
                break
            except Exception as e:
                print(f"  Upload/Import attempt {attempt + 1} failed: {e}")
                # Clean up the failed attempt's file
                if os.path.exists(unique_filename):
                    os.remove(unique_filename)
                
                if attempt < max_retries - 1:
                    time.sleep(30) # Wait longer on 503 errors
                else:
                    print(f"  FAILED to upload {pkg_name} after {max_retries} attempts. Skipping.")
                    operation = None
                    
        if operation is None:
            failed_packages.add(pkg_name)
            with open(FAILED_PACKAGES_FILE, "w") as f:
                json.dump(list(failed_packages), f, indent=2)
            continue
            
        if pkg_name in failed_packages:
            failed_packages.remove(pkg_name)
            with open(FAILED_PACKAGES_FILE, "w") as f:
                json.dump(list(failed_packages), f, indent=2)
                    
        # IMMEDIATELY save the operation name to the local DB so we don't lose it if we crash
        pkg_entry = pkg.copy()
        pkg_entry["pending_operation_name"] = operation.name
        pkg_entry["file_name"] = uploaded_file.name
        pkg_entry["last_ingested"] = time.ctime()
        db[pkg_name] = pkg_entry
        with open(PACKAGES_DB, "w") as f:
            json.dump(list(db.values()), f, indent=2)

        pending_operations.append((pkg_name, pkg, operation, unique_filename, uploaded_file.name))
        
    if pending_operations:
        print("\nWaiting for all indexing operations to complete on the Gemini backend...")
        for pkg_name, pkg, operation, unique_filename, uploaded_file_name in pending_operations:
            print(f"Waiting for {pkg_name} to finish indexing...")
            while not operation.done:
                time.sleep(5)
                print(f"  Still indexing {pkg_name}...")
                operation = client.operations.get(operation)
                
            print(f"Finished indexing {pkg_name}.")

            # Cleanup the unique file
            if unique_filename and os.path.exists(unique_filename):
                os.remove(unique_filename)

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
    system_prompt = load_system_prompt()
    try:
        response = client.models.generate_content(
            model="gemini-3-pro-preview",
            contents=query,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
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

def run_ingest():
    parser = argparse.ArgumentParser(description="Ingest repositories and build index")
    parser.add_argument("--update", action="store_true", help="Force update of existing packages")
    parser.add_argument("--since", type=str, help="Only update packages older than this (e.g., 24h, 1d)")
    parser.add_argument("--package", type=str, help="Only ingest/update this specific package")
    args = parser.parse_args()
    ingest(update=args.update, since=args.since, package=args.package)

def run_ask():
    parser = argparse.ArgumentParser(description="Ask a question against the built index")
    parser.add_argument("query", type=str, help="The question to ask")
    args = parser.parse_args()
    ask(args.query)

def clean(delete=False):
    client = init_client()
    if not os.path.exists(STORE_NAME_FILE):
        print("No store found.")
        return
        
    with open(STORE_NAME_FILE, "r") as f:
        store_name = f.read().strip()
        
    db = {}
    if os.path.exists(PACKAGES_DB):
        with open(PACKAGES_DB, "r") as f:
            try:
                existing_list = json.load(f)
                db = {pkg["package"]: pkg for pkg in existing_list}
            except json.JSONDecodeError:
                db = {}
                
    active_doc_names = set()
    active_display_names = set()
    for pkg in db.values():
        if pkg.get("pending_operation_name"):
            op_name = pkg["pending_operation_name"]
            doc_name = op_name.replace("/operations/", "/documents/")
            active_doc_names.add(doc_name)
        if pkg.get("file_name"):
            file_id = pkg["file_name"].split("/")[-1]
            active_display_names.add(file_id)
            
    print(f"Checking store {store_name} for orphaned documents...")
    all_docs = list(client.file_search_stores.documents.list(parent=store_name))
    
    orphaned_docs = []
    for doc in all_docs:
        if doc.name in active_doc_names:
            continue
        display_name = getattr(doc, 'display_name', '')
        if display_name in active_display_names:
            continue
        orphaned_docs.append(doc)
    
    kept_count = len(all_docs) - len(orphaned_docs)
    
    if not orphaned_docs:
        print(f"No orphaned documents found. Keeping {kept_count} active documents.")
        return
        
    print(f"Keeping {kept_count} active documents.")
    print(f"Found {len(orphaned_docs)} orphaned documents:")
    for doc in orphaned_docs:
        print(f" - {doc.name} ({getattr(doc, 'display_name', 'Unknown')})")
        
    if delete:
        print("\nDeleting orphaned documents...")
        for doc in orphaned_docs:
            try:
                client.file_search_stores.documents.delete(name=doc.name, config={'force': True})
                print(f"Deleted {doc.name}")
            except Exception as e:
                print(f"Failed to delete {doc.name}: {e}")
    else:
        print("\nRun with --delete to remove them.")

def run_clean():
    parser = argparse.ArgumentParser(description="Check for and optionally delete orphaned documents in the store")
    parser.add_argument("--delete", action="store_true", help="Delete orphaned documents")
    args = parser.parse_args()
    clean(delete=args.delete)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI SDK Document Ingestion and Query App")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    ingest_parser = subparsers.add_parser("ingest", help="Ingest repositories and build index")
    ingest_parser.add_argument("--update", action="store_true", help="Force update of existing packages")
    ingest_parser.add_argument("--since", type=str, help="Only update packages older than this (e.g., 24h, 1d)")
    ingest_parser.add_argument("--package", type=str, help="Only ingest/update this specific package")
    
    ask_parser = subparsers.add_parser("ask", help="Ask a question against the built index")
    ask_parser.add_argument("query", type=str, help="The question to ask")

    clean_parser = subparsers.add_parser("clean", help="Check for and optionally delete orphaned documents in the store")
    clean_parser.add_argument("--delete", action="store_true", help="Delete orphaned documents")
    
    args = parser.parse_args()
    
    if args.command == "ingest":
        ingest(update=args.update, since=args.since, package=args.package)
    elif args.command == "ask":
        ask(args.query)
    elif args.command == "clean":
        clean(delete=args.delete)
