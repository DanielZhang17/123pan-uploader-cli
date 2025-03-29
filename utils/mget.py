import argparse
import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm

def get_file_size(url):
    """Get file size using HEAD request"""
    response = requests.head(url)
    file_size = int(response.headers.get('content-length', 0))
    return file_size

def download_single_thread(url, output_path):
    """Download file using single thread"""
    start_time = time.time()
    
    response = requests.get(url, stream=True)
    file_size = int(response.headers.get('content-length', 0))
    
    print(f"Single thread download - File size: {file_size/1024/1024:.2f} MB")
    
    progress_bar = tqdm(total=file_size, unit='B', unit_scale=True, desc="Single thread")
    
    with open(output_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                progress_bar.update(len(chunk))
    
    progress_bar.close()
    elapsed_time = time.time() - start_time
    print(f"Single thread download completed in {elapsed_time:.2f} seconds")
    return elapsed_time

def download_chunk(args):
    """Download specific byte range of file"""
    url, start, end, output_path, chunk_id = args
    headers = {'Range': f'bytes={start}-{end}'}
    response = requests.get(url, headers=headers, stream=True)
    
    chunk_path = f"{output_path}.part{chunk_id}"
    with open(chunk_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    
    return chunk_path, chunk_id, end - start + 1

def download_multi_thread(url, output_path, num_threads=8):
    """Download file using multiple threads"""
    start_time = time.time()
    
    file_size = get_file_size(url)
    print(f"Multi-thread download - File size: {file_size/1024/1024:.2f} MB, Threads: {num_threads}")
    
    chunk_size = file_size // num_threads
    chunks = []
    for i in range(num_threads):
        start_byte = i * chunk_size
        end_byte = start_byte + chunk_size - 1 if i < num_threads - 1 else file_size - 1
        chunks.append((url, start_byte, end_byte, output_path, i))
    
    progress_bar = tqdm(total=file_size, unit='B', unit_scale=True, desc="Multi-thread")
    downloaded = 0
    
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        chunk_files = []
        for chunk_path, _, size in executor.map(download_chunk, chunks):
            chunk_files.append(chunk_path)
            downloaded += size
            progress_bar.update(size)
    
    progress_bar.close()
    
    with open(output_path, 'wb') as output_file:
        for i in range(num_threads):
            chunk_path = f"{output_path}.part{i}"
            with open(chunk_path, 'rb') as chunk_file:
                output_file.write(chunk_file.read())
            os.remove(chunk_path)
    
    elapsed_time = time.time() - start_time
    print(f"Multi-thread download completed in {elapsed_time:.2f} seconds")
    return elapsed_time

def main():
    parser = argparse.ArgumentParser(description='File Downloader - Single and Multi-threaded')
    parser.add_argument('url', help='URL of the file to download')
    parser.add_argument('-o', '--output', help='Output file path', default='downloaded_file')
    parser.add_argument('-t', '--threads', type=int, help='Number of threads', default=8)
    args = parser.parse_args()
    
    print(f"Starting download: {args.url}")
    
    single_output = f"{args.output}_single"
    single_time = download_single_thread(args.url, single_output)
    
    print("\n" + "-"*50 + "\n")
    
    multi_output = f"{args.output}_multi"
    multi_time = download_multi_thread(args.url, multi_output, args.threads)
    
    speedup = single_time / multi_time if multi_time > 0 else 0
    print("\n" + "="*50)
    print(f"Performance comparison:")
    print(f"Single thread: {single_time:.2f} seconds")
    print(f"Multi-thread: {multi_time:.2f} seconds")
    print(f"Speed-up: {speedup:.2f}x")
    print("="*50)
    
    single_size = os.path.getsize(single_output)
    multi_size = os.path.getsize(multi_output)
    if single_size == multi_size:
        print(f"File size verification successful: {single_size/1024/1024:.2f} MB")
    else:
        print(f"Warning: File sizes don't match! Single: {single_size/1024/1024:.2f} MB, Multi: {multi_size/1024/1024:.2f} MB")

if __name__ == "__main__":
    main()

# python megt.py "https://218-60-174-4.pd1.cjjd19.com:30443/download-cdn.cjjd19.com/123-568/07e4f7f6/1814768750-0/07e4f7f659fbe5c944a1d2f6f30dae38/c-m6?v=1&t=1743330515&s=6190f1ea5e6d8a492ccb956ca5995263&bzc=1&bzs=1814768750&filename=diff-sample-out.zip&x-mf-biz-cid=2019aab3-0f77-43f4-9a2e-57a16b60210d-3dab77&cache_type=1&auto_redirect=0&xmfcid=4c74e6ac-2ffe-47f1-9ee8-03ff4224b877-1-50111d3b1" -o filename -t 16