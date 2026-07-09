"""测试 local_files_only 是否生效 — 验证修复后不再请求 huggingface.co。"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("测试1: model_kwargs={'local_files_only': True}")
t0 = time.perf_counter()
try:
    from langchain_huggingface import HuggingFaceEmbeddings
    e = HuggingFaceEmbeddings(model_name='BAAI/bge-small-zh-v1.5', model_kwargs={'local_files_only': True})
    print(f"  OK: {time.perf_counter()-t0:.1f}s")
except Exception as ex:
    print(f"  FAIL: {ex} ({time.perf_counter()-t0:.1f}s)")

print("\n测试2: SentenceTransformer 直接 local_files_only=True")
t0 = time.perf_counter()
try:
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer('BAAI/bge-small-zh-v1.5', local_files_only=True)
    print(f"  OK: {time.perf_counter()-t0:.1f}s")
except Exception as ex:
    print(f"  FAIL: {ex} ({time.perf_counter()-t0:.1f}s)")

print("\n测试3: HF_HUB_OFFLINE=1")
os.environ['HF_HUB_OFFLINE'] = '1'
t0 = time.perf_counter()
try:
    from sentence_transformers import SentenceTransformer as ST2
    m2 = ST2('BAAI/bge-small-zh-v1.5')
    print(f"  OK: {time.perf_counter()-t0:.1f}s")
except Exception as ex:
    print(f"  FAIL: {ex} ({time.perf_counter()-t0:.1f}s)")
