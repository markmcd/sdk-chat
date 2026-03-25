import os
import json
import sys
import argparse
from google import genai

def init_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable is not set.")
        sys.exit(1)
    return genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})

def get_package_metadata(package_name):
    client = init_client()
    
    # 1. Load from packages.json
    packages_file = "packages.json"
    if not os.path.exists(packages_file):
        print(f"Error: {packages_file} not found.")
        return

    with open(packages_file, "r") as f:
        packages = json.load(f)
    
    pkg_data = next((p for p in packages if p["package"] == package_name), None)
    if not pkg_data:
        print(f"Error: Package '{package_name}' not found in {packages_file}.")
        # Try a partial match if exact match fails
        suggestions = [p["package"] for p in packages if package_name.lower() in p["package"].lower()]
        if suggestions:
            print(f"Did you mean one of these? {', '.join(suggestions)}")
        return

    print(f"--- Metadata for package: {package_name} (from packages.json) ---")
    print(json.dumps(pkg_data, indent=2))

    # 2. Get File metadata if file_name exists
    file_name = pkg_data.get("file_name")
    if file_name:
        print(f"\n--- File Metadata (from API: {file_name}) ---")
        try:
            file_meta = client.files.get(name=file_name)
            # Filter out some noise if necessary, but user said "everything but contents"
            # file_meta is a File object
            meta_dict = {
                "name": file_meta.name,
                "display_name": file_meta.display_name,
                "mime_type": file_meta.mime_type,
                "size_bytes": file_meta.size_bytes,
                "create_time": str(file_meta.create_time),
                "update_time": str(file_meta.update_time),
                "expiration_time": str(file_meta.expiration_time),
                "sha256_hash": file_meta.sha256_hash,
                "state": file_meta.state
            }
            print(json.dumps(meta_dict, indent=2))
        except Exception as e:
            print(f"Error fetching file metadata: {e}")

    # 3. Check File Search Store if .store_name exists
    store_name_file = ".store_name"
    if os.path.exists(store_name_file):
        with open(store_name_file, "r") as f:
            store_name = f.read().strip()
        
        print(f"\n--- Store Document Metadata (Store: {store_name}) ---")
        try:
            # The store document display_name seems to be the file ID (part after 'files/')
            file_id = file_name.split('/')[-1] if file_name else None
            
            docs = list(client.file_search_stores.documents.list(parent=store_name, config={'page_size': 20}))
            
            # Try to find by file_id or package_name
            doc = next((d for d in docs if getattr(d, 'display_name', '') in (file_id, package_name)), None)
            
            if doc:
                doc_meta = {
                    "name": doc.name,
                    "display_name": doc.display_name,
                    "create_time": str(doc.create_time),
                    "update_time": str(doc.update_time),
                }
                # Check for custom metadata if available
                if hasattr(doc, 'custom_metadata') and doc.custom_metadata:
                    doc_meta["custom_metadata"] = [
                        {'key': m.key, 'string_value': getattr(m, 'string_value', None)}
                        for m in doc.custom_metadata
                    ]
                
                print(json.dumps(doc_meta, indent=2))
            else:
                print(f"No document found in store '{store_name}' matching '{file_id}' or '{package_name}'.")
                print(docs)
        except Exception as e:
            print(f"Error fetching store document metadata: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get metadata for a package")
    parser.add_argument("package", help="Name of the package")
    args = parser.parse_args()
    
    get_package_metadata(args.package)
