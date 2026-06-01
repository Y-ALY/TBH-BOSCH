import sys
from src.benchmark import benchmark_legacy
import logging
logging.basicConfig(level=logging.DEBUG)

# Let's try running benchmark_legacy
file_path = "./strict_drive"
try:
    res = benchmark_legacy(file_path, limit=1)
    print("Success benchmark!", res)
except Exception as e:
    import traceback
    traceback.print_exc()

