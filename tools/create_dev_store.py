import os
from google import genai

client = genai.Client()

store = client.file_search_stores.create(
    config={'display_name': 'dev_bisect_store'}
)

with open('.dev_store_name', 'w') as f:
    f.write(store.name)

print(f"Created dev store: {store.name}")
