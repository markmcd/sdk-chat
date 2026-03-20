import os
import time
from google import genai

client = genai.Client()

def get_store_name():
    with open(".store_name", "r") as f:
        return f.read().strip()

STORE_NAME = get_store_name()
PENDING_OPS = []

def test_chunk(lines_chunk, start_idx, end_idx):
    content = "".join(lines_chunk)
    temp_filename = f"temp_bisect_{start_idx}_{end_idx}.txt"
    
    with open(temp_filename, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n[TESTING] lines {start_idx} to {end_idx} ({len(lines_chunk)} lines, {len(content)} bytes)...")
    
    while True:
        try:
            print(f"  --> Uploading {temp_filename}...")
            uploaded_file = client.files.upload(
                file=temp_filename,
                config={'display_name': temp_filename}
            )
            
            print(f"  --> Waiting for processing ({uploaded_file.name})...", end="", flush=True)
            while uploaded_file.state == "PROCESSING":
                print(".", end="", flush=True)
                time.sleep(2)
                uploaded_file = client.files.get(name=uploaded_file.name)
            print(f" State: {uploaded_file.state}")
                
            if uploaded_file.state == "FAILED":
                print(f"  [RESULT] BAD (Upload failed for {start_idx}-{end_idx})")
                client.files.delete(name=uploaded_file.name)
                os.remove(temp_filename)
                return True
                
            print(f"  --> Importing into store {STORE_NAME}...")
            op = client.file_search_stores.import_file(
                file_search_store_name=STORE_NAME,
                file_name=uploaded_file.name
            )
            
            print(f"  --> Waiting for import operation ({op.name})...", end="", flush=True)
            wait_time = 0
            timeout = 30
            while not op.done and wait_time < timeout:
                print(".", end="", flush=True)
                time.sleep(5)
                wait_time += 5
                op = client.operations.get(op)
            print(" Done." if op.done else " Timeout.")
            
            if not op.done:
                print(f"  [RESULT] GOOD (Assumed good due to timeout. Operation pending)")
                PENDING_OPS.append(op.name)
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
                return False
                
            # Clean up
            try:
                client.files.delete(name=uploaded_file.name)
            except:
                pass
            if os.path.exists(temp_filename):
                os.remove(temp_filename)

            # Check if op has an error inside
            if hasattr(op, 'error') and op.error:
                print(f"  [RESULT] BAD (Operation error: {op.error.code} - {op.error.message})")
                if op.error.code == 503 and "Failed to count tokens" in op.error.message:
                    return True
                else:
                    return True 
            
            print(f"  [RESULT] GOOD (Imported successfully)")
            return False
            
        except Exception as e:
            err_str = str(e)
            if "503" in err_str and "Failed to count tokens" in err_str:
                print(f"\n  [RESULT] BAD (Caught 503 exception: {err_str})")
                if 'uploaded_file' in locals():
                    try: client.files.delete(name=uploaded_file.name)
                    except: pass
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
                return True
            elif "429" in err_str or ("503" in err_str and "Failed to count tokens" not in err_str):
                print(f"\n  [RETRY] Rate limited or temporary error ({e}). Sleeping 10s...")
                time.sleep(10)
                continue
            else:
                print(f"\n  [ERROR] Unexpected exception: {e}")
                if 'uploaded_file' in locals():
                    try: client.files.delete(name=uploaded_file.name)
                    except: pass
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
                return True


def bisect(lines, start_idx, end_idx):
    if end_idx - start_idx <= 20:
        chunk_content = "".join(lines[start_idx:end_idx])
        print("\n=== PROBLEMATIC CHUNK FOUND ===")
        print(f"Lines {start_idx} to {end_idx}")
        print(chunk_content)
        print("===============================\n")
        
        final_filename = f"problematic_chunk_{start_idx}_{end_idx}.txt"
        with open(final_filename, "w", encoding="utf-8") as f:
            f.write(chunk_content)
        print(f"Narrowest chunk saved to: {final_filename}")
        
        return lines[start_idx:end_idx]

    mid_idx = (start_idx + end_idx) // 2
    
    # Test first half
    is_bad = test_chunk(lines[start_idx:mid_idx], start_idx, mid_idx)
    if is_bad:
        print(f"-> Problem is in first half ({start_idx}-{mid_idx})")
        return bisect(lines, start_idx, mid_idx)
        
    # If first half is good, test second half
    is_bad_2 = test_chunk(lines[mid_idx:end_idx], mid_idx, end_idx)
    if is_bad_2:
        print(f"-> Problem is in second half ({mid_idx}-{end_idx})")
        return bisect(lines, mid_idx, end_idx)
        
    print("Neither half failed! This is unexpected.")
    return None

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: uv run python bisect_file.py <filename.txt>")
        sys.exit(1)
        
    target_file = sys.argv[1]
    
    if not os.path.exists(target_file):
        print(f"File not found: {target_file}")
        sys.exit(1)
        
    with open(target_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    print(f"Total lines in {target_file}: {len(lines)}")
    # Start bisection. We assume the full file fails.
    bisect(lines, 0, len(lines))
    
    if PENDING_OPS:
        print("\n=== PENDING OPERATIONS ===")
        for op_name in PENDING_OPS:
            print(op_name)
