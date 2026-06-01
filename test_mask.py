import time
import random

full_text = "a" * 1000000
# Generate 100 random intervals
ranges = []
for _ in range(100):
    start = random.randint(0, 990000)
    ranges.append((start, start + 1000))
ranges.sort()

merged_ranges = []
for r in ranges:
    if not merged_ranges:
        merged_ranges.append(r)
    else:
        last = merged_ranges[-1]
        if r[0] <= last[1]:
            merged_ranges[-1] = (last[0], max(last[1], r[1]))
        else:
            merged_ranges.append(r)

t0 = time.perf_counter()
for _ in range(100):
    text_chars = list(full_text)
    for start_idx, end_idx in merged_ranges:
        for i in range(start_idx, end_idx):
            text_chars[i] = ' '
    remaining_text = "".join(text_chars)
t1 = time.perf_counter()

t2 = time.perf_counter()
for _ in range(100):
    parts = []
    last_idx = 0
    for start_idx, end_idx in merged_ranges:
        parts.append(full_text[last_idx:start_idx])
        parts.append(" " * (end_idx - start_idx))
        last_idx = end_idx
    parts.append(full_text[last_idx:])
    remaining_text2 = "".join(parts)
t3 = time.perf_counter()

print("List of chars:", t1 - t0)
print("Substring join:", t3 - t2)
