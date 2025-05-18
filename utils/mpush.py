import os
import json
import time
import hashlib
import requests
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from tosasitill_123pan.sign_get import getSign


def format_size(size_bytes):
    """Convert bytes to human-readable format"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes/(1024*1024):.2f} MB"
    else:
        return f"{size_bytes/(1024*1024*1024):.2f} GB"


class MPush:
    """Class for uploading files to 123Pan Cloud Storage"""

    def __init__(self, pan):
        """Initialize MPush with an authenticated Pan123 instance"""
        self.pan = pan # Pan123 instance, expected to have headerLogined, parentFileId, get_dir, delete_file, mkdir

    @staticmethod
    def compute_file_md5(file_path):
        """Calculate MD5 hash of a file"""
        with open(file_path, "rb") as f:
            md5_hash = hashlib.md5()
            # Read and update hash in chunks of 4K
            for byte_block in iter(lambda: f.read(4096), b""):
                md5_hash.update(byte_block)
            return md5_hash.hexdigest()

    def _upload_chunk_worker(self, file_path, offset, chunk_size, part_number,
                             bucket, upload_key, upload_id, storage_node, pbar):
        """
        Worker function to upload a single chunk of a file.

        Args:
            file_path (str): Path to the file.
            offset (int): Starting byte offset in the file for this chunk.
            chunk_size (int): Size of the chunk to read and upload.
            part_number (int): The part number for this chunk in the multipart upload.
            bucket (str): S3 bucket name.
            upload_key (str): S3 object key.
            upload_id (str): S3 upload ID.
            storage_node (str): Storage node identifier.
            pbar (tqdm): Progress bar instance to update.

        Returns:
            tuple: (part_number, etag, success_bool, actual_data_length)
                   ETag is None if upload fails.
                   actual_data_length is for pbar update.
        """
        try:
            # Prepare request to get presigned URL for this specific part
            get_link_data = {
                "bucket": bucket,
                "key": upload_key,
                "partNumberEnd": part_number + 1, # API seems to expect end to be exclusive or next part
                "partNumberStart": part_number,
                "uploadId": upload_id,
                "StorageNode": storage_node, # Note: Original code had "StorageNode", API might be case-sensitive
            }

            # Get presigned URL for the chunk
            # Assuming getSign is available and works as in the original script
            sign_chunk = getSign("/b/api/file/s3_repare_upload_parts_batch")
            get_link_res = requests.post(
                "https://www.123pan.com/b/api/file/s3_repare_upload_parts_batch",
                headers=self.pan.headerLogined,
                params={sign_chunk[0]: sign_chunk[1]},
                data=json.dumps(get_link_data),
                timeout=30 # Added timeout
            )
            get_link_res.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            get_link_res_json = get_link_res.json()

            if get_link_res_json.get("code") != 0:
                print(f"Part {part_number}: Failed to get upload link: {get_link_res_json.get('message', 'Unknown error')}")
                return part_number, None, False, 0

            upload_url = get_link_res_json["data"]["presignedUrls"].get(str(part_number))
            if not upload_url:
                print(f"Part {part_number}: No presigned URL found in response: {get_link_res_json}")
                return part_number, None, False, 0

            # Read the specific chunk from the file
            with open(file_path, "rb") as f:
                f.seek(offset)
                data = f.read(chunk_size)
            
            if not data: # Should not happen if chunk_size > 0 and offset is correct
                print(f"Part {part_number}: No data read from file at offset {offset}, size {chunk_size}")
                return part_number, None, False, 0

            # Upload the chunk
            put_response = requests.put(upload_url, data=data, timeout=120) # Added timeout
            put_response.raise_for_status()
            
            etag = put_response.headers.get("ETag")
            if etag:
                etag = etag.strip('"') # S3 ETags are often quoted

            pbar.update(len(data))
            # print(f"Part {part_number}: Uploaded, ETag: {etag}")
            return part_number, etag, True, len(data)

        except requests.exceptions.RequestException as e:
            print(f"Part {part_number}: Network error during chunk upload: {e}")
            return part_number, None, False, 0
        except Exception as e:
            print(f"Part {part_number}: Unexpected error in_upload_chunk_worker: {e}")
            return part_number, None, False, 0

    def upload_file(self, file_path, parent_id=None, sure=None, num_concurrent_chunks=4):
        """
        Upload a single file to 123Pan Cloud with concurrent chunk uploading.

        Args:
            file_path (str): Path to the file to upload.
            parent_id (str, optional): Parent folder ID. Defaults to self.pan.parentFileId.
            sure (str, optional): How to handle duplicates - "1":keep both, "2":overwrite, None:prompt.
            num_concurrent_chunks (int, optional): Number of chunks to upload concurrently. Defaults to 4.

        Returns:
            bool: True if upload successful, False otherwise.
        """
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            print(f"Error: {file_path} is not a valid file.")
            return False

        file_path_norm = file_path.replace('"', "").replace("\\", "/")
        file_name = os.path.basename(file_path_norm)
        file_size = os.path.getsize(file_path_norm)

        print(f"Preparing to upload: {file_name} ({format_size(file_size)})")

        print("Calculating file MD5...")
        md5 = self.compute_file_md5(file_path_norm)
        print(f"MD5: {md5}")

        if parent_id is None:
            parent_id = self.pan.parentFileId

        # Handle pre-selected overwrite
        if sure == "2":
            print(f"Overwrite selected for {file_name}. Checking for existing file...")
            self.pan.get_dir() # Refresh directory listing in the Pan123 object
            for i, file_info in enumerate(self.pan.list): # Assuming self.pan.list is populated by get_dir()
                if file_info.get("FileName") == file_name and file_info.get("Type") == 0: # Type 0 for file
                    print(f"Deleting existing file: {file_name} (ID: {file_info.get('FileId')})")
                    delete_success = self.pan.delete_file(i, by_num=True, operation=True) # Assuming delete_file can take index
                    if not delete_success:
                        print(f"Failed to delete existing file {file_name}. Aborting overwrite.")
                        # return False # Or proceed with duplicate=2 if API handles it
                    self.pan.get_dir() # Refresh again after deletion
                    break
        
        list_up_request = {
            "driveId": 0, # Assuming default drive
            "etag": md5,
            "fileName": file_name,
            "parentFileId": parent_id,
            "size": file_size,
            "type": 0, # 0 for file
            "duplicate": 0, # Default: 0 (fail if duplicate), 1 (rename), 2 (overwrite)
        }

        sign_upload_req = getSign("/b/api/file/upload_request")
        up_res = requests.post(
            "https://www.123pan.com/b/api/file/upload_request",
            headers=self.pan.headerLogined,
            params={sign_upload_req[0]: sign_upload_req[1]},
            data=json.dumps(list_up_request),
            timeout=30
        )
        up_res_json = up_res.json()
        code = up_res_json.get("code")

        if code == 5060: # Duplicate file detected by server
            print("Duplicate file detected by server.")
            if sure not in ["1", "2"]: # If 'sure' wasn't pre-set for overwrite or keep_both
                user_choice = input(
                    "Enter 1 to keep both (rename), 2 to overwrite, or any other key to cancel: "
                )
                if user_choice == "1":
                    sure = "1"
                elif user_choice == "2":
                    sure = "2"
                else:
                    print("Upload cancelled by user due to duplicate.")
                    return False
            
            if sure == "1": # Keep both (rename)
                list_up_request["duplicate"] = 1
            elif sure == "2": # Overwrite
                # If server still says duplicate after client-side delete, this means server-side overwrite
                list_up_request["duplicate"] = 2
            
            # Retry upload_request with duplicate strategy
            sign_upload_req_retry = getSign("/b/api/file/upload_request")
            up_res = requests.post(
                "https://www.123pan.com/b/api/file/upload_request",
                headers=self.pan.headerLogined,
                params={sign_upload_req_retry[0]: sign_upload_req_retry[1]},
                data=json.dumps(list_up_request),
                timeout=30
            )
            up_res_json = up_res.json()
            code = up_res_json.get("code")

        if code == 0:
            if up_res_json["data"].get("Reuse"):
                print(f"Upload successful (MD5 Reuse): {file_name}")
                return True
            # Proceed to chunked upload if not reused
        else:
            print(f"Upload request failed: Code {code}, Message: {up_res_json.get('message', 'No message')}")
            return False

        # Extract details for chunked upload
        try:
            data_node = up_res_json["data"]
            bucket = data_node["Bucket"]
            storage_node = data_node["StorageNode"]
            upload_key = data_node["Key"]
            upload_id = data_node["UploadId"]
            up_file_id = data_node["FileId"] # To be used in upload_complete
        except KeyError as e:
            print(f"Missing expected data in upload_request response: {e}. Response: {up_res_json}")
            return False

        # S3 multi-part upload start (already initiated by upload_request)
        # The call to s3_list_upload_parts in original code before loop might be for resuming,
        # but for a new upload, we can skip it and go straight to uploading parts.

        block_size = 32 * 1024 * 1024  # 32MB per chunk
        total_parts = (file_size + block_size - 1) // block_size if file_size > 0 else 1
        if file_size == 0: total_parts = 1 # S3 requires at least one part even for 0 byte file if multipart

        print(f"Starting chunked upload for {file_name} ({total_parts} parts, {num_concurrent_chunks} concurrent)...")
        uploaded_parts_info = []
        upload_failed = False

        with tqdm(total=file_size, unit="B", unit_scale=True, desc=file_name) as pbar:
            with ThreadPoolExecutor(max_workers=num_concurrent_chunks) as executor:
                futures = []
                for i in range(total_parts):
                    part_number = i + 1
                    offset = i * block_size
                    current_chunk_size = min(block_size, file_size - offset)
                    if file_size == 0: # Handle 0 byte file specifically for S3
                        current_chunk_size = 0

                    futures.append(executor.submit(
                        self._upload_chunk_worker,
                        file_path_norm, offset, current_chunk_size, part_number,
                        bucket, upload_key, upload_id, storage_node, pbar
                    ))
                
                for future in as_completed(futures):
                    try:
                        part_num, etag, success, _ = future.result()
                        if success and etag:
                            uploaded_parts_info.append({"PartNumber": part_num, "ETag": etag})
                        else:
                            print(f"Failed to upload part {part_num}.")
                            upload_failed = True
                            # Optionally, cancel remaining futures:
                            # for f in futures: f.cancel() # May not stop already running tasks
                            break 
                    except Exception as e:
                        print(f"Error processing future result for a chunk: {e}")
                        upload_failed = True
                        break
            
            if upload_failed:
                print(f"Chunk upload failed for {file_name}. Aborting.")
                # Consider an API to abort multipart upload if available/needed
                return False

        if not uploaded_parts_info and file_size > 0 : # Check if any part was successfully uploaded
             print(f"No parts were successfully uploaded for {file_name}.")
             return False
        if len(uploaded_parts_info) != total_parts and file_size > 0:
            print(f"Mismatch in uploaded parts. Expected {total_parts}, got {len(uploaded_parts_info)}.")
            return False


        print("All chunks uploaded. Finalizing...")
        # Sort parts by PartNumber, crucial for S3 CompleteMultipartUpload
        uploaded_parts_info.sort(key=lambda x: x["PartNumber"])

        # Complete Multipart Upload
        complete_data = {
            "bucket": bucket,
            "key": upload_key,
            "uploadId": upload_id,
            "storageNode": storage_node,
            "Parts": uploaded_parts_info
        }
        if file_size == 0: # For 0-byte files, Parts might need to be empty or handled differently by API
            complete_data["Parts"] = [] # Or consult API docs; some S3 APIs expect empty Parts for 0-byte.
                                        # If the initial upload_request handles 0-byte files completely, this part might not be hit.

        sign_complete = getSign("/b/api/file/s3_complete_multipart_upload")
        complete_res = requests.post(
            "https://www.123pan.com/b/api/file/s3_complete_multipart_upload",
            headers=self.pan.headerLogined,
            params={sign_complete[0]: sign_complete[1]},
            data=json.dumps(complete_data),
            timeout=60
        )
        complete_res_json = complete_res.json()

        if complete_res_json.get("code") != 0:
            print(f"Failed to complete multipart upload: {complete_res_json.get('message', 'Unknown error')}")
            # Attempt to list parts to see what went wrong, if API supports/helps
            # list_parts_data = {"bucket": bucket, "key": upload_key, "uploadId": upload_id, "storageNode": storage_node}
            # requests.post("https://www.123pan.com/b/api/file/s3_list_upload_parts", headers=self.pan.headerLogined, data=json.dumps(list_parts_data))
            return False

        # Finalize upload session with the backend
        # The original code had a time.sleep(3) for large files, which might be for eventual consistency on the server.
        if file_size > 64 * 1024 * 1024:
            print("Waiting briefly for server processing of large file...")
            time.sleep(3)

        close_up_session_data = {"fileId": up_file_id}
        sign_upload_complete = getSign("/b/api/file/upload_complete")
        close_up_session_res = requests.post(
            "https://www.123pan.com/b/api/file/upload_complete",
            headers=self.pan.headerLogined,
            params={sign_upload_complete[0]: sign_upload_complete[1]},
            data=json.dumps(close_up_session_data),
            timeout=30
        )
        close_res_json = close_up_session_res.json()

        if close_res_json.get("code") == 0:
            print(f"Upload successful: {file_name}")
            return True
        else:
            print(f"Finalizing upload failed: Code {close_res_json.get('code')}, Message: {close_res_json.get('message', 'Unknown error')}")
            return False


    def upload_directory_concurrent(
        self,
        dir_path,
        parent_id=None,
        max_workers=8, # For number of files to upload concurrently
        file_types=None,
        sure=None, # For duplicate handling of each file
        custom_dirname=None,
        num_chunks_per_file=4 # For concurrent chunks per file
    ):
        """Upload a directory to 123Pan Cloud using concurrent threads for files,
           and each file upload can use concurrent chunks.

        Args:
            dir_path (str): Path to the directory to upload.
            parent_id (str, optional): Parent folder ID. Defaults to root.
            max_workers (int, optional): Max concurrent file uploads. Defaults to 8.
            file_types (list, optional): List of file extensions to include (e.g., ['.txt', '.jpg']).
            sure (str, optional): Duplicate handling for files ("1": keep both, "2": overwrite).
            custom_dirname (str, optional): Custom name for the remote directory.
            num_chunks_per_file (int, optional): Number of concurrent chunks for each file upload. Defaults to 4.

        Returns:
            bool: True if all operations successful, False otherwise. (Simplified: returns True if dir creation is ok)
        """
        if not os.path.isdir(dir_path):
            print(f"Error: {dir_path} is not a valid directory")
            return False

        dir_path_norm = dir_path.replace('"', "").replace("\\", "/")
        base_dir_name = custom_dirname if custom_dirname else os.path.basename(dir_path_norm)

        print(f"Preparing to upload directory: {base_dir_name}")

        if parent_id is None:
            parent_id = self.pan.parentFileId # Assuming this is the root or current remote dir ID

        # Create the base remote directory for the upload
        # The remake=False argument suggests it won't error if exists, but might return existing ID or new one.
        # Assuming self.pan.mkdir returns the folder ID (str or int) or None/False on failure.
        base_folder_id = self.pan.mkdir(base_dir_name, parent_id, remake=False)
        if not base_folder_id:
            print(f"Failed to create or access base remote directory {base_dir_name}")
            return False
        print(f"Base remote directory: '{base_dir_name}', ID: {base_folder_id}")

        # Dictionary to map local directory paths to remote folder IDs
        remote_dir_map = {dir_path_norm: base_folder_id}
        
        # Directories to skip (lowercase for case-insensitive comparison if needed, but os.walk is exact)
        skip_dirs_patterns = ["venv", ".idea", "__pycache__", ".git", "node_modules", ".vscode", ".pytest_cache", "dist", "build"]

        # First pass: Create directory structure on the remote server
        print("Creating remote directory structure...")
        for root, dirs, _ in os.walk(dir_path_norm):
            # Filter out skip_dirs from further traversal
            dirs[:] = [d for d in dirs if not any(skip_pattern in d.lower() for skip_pattern in skip_dirs_patterns)]
            
            current_remote_parent_id = remote_dir_map.get(root)
            if current_remote_parent_id is None:
                # This shouldn't happen if we process top-down, but as a safeguard:
                print(f"Warning: Could not find remote parent ID for local path {root}. Skipping subdirectories.")
                continue

            for dir_name in dirs:
                local_dir_full_path = os.path.join(root, dir_name)
                # Check against skip patterns again for the full path if necessary, though filtering dirs[:] is usually enough
                if any(skip_pattern in local_dir_full_path.lower() for skip_pattern in skip_dirs_patterns):
                    print(f"Skipping directory: {local_dir_full_path}")
                    continue

                print(f"Creating remote directory: {dir_name} under ID {current_remote_parent_id}")
                # Assuming self.pan.mkdir is robust (e.g., handles existing, returns ID)
                new_remote_folder_id = self.pan.mkdir(dir_name, current_remote_parent_id, remake=False)
                if new_remote_folder_id:
                    remote_dir_map[local_dir_full_path] = new_remote_folder_id
                    print(f"Created remote directory '{dir_name}', ID: {new_remote_folder_id}")
                    time.sleep(0.1) # Small delay to avoid overwhelming the server with mkdir requests
                else:
                    print(f"Failed to create remote directory '{dir_name}' in parent {current_remote_parent_id}")
                    # Decide if this is a fatal error for the whole directory upload

        # Second pass: Upload files
        print(f"Starting file uploads (max {max_workers} files concurrently)...")
        file_upload_tasks = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for root, dirs, files in os.walk(dir_path_norm):
                 # Ensure we don't descend into skipped directories for file listing either
                dirs[:] = [d for d in dirs if not any(skip_pattern in d.lower() for skip_pattern in skip_dirs_patterns)]
                if any(skip_pattern in root.lower() for skip_pattern in skip_dirs_patterns):
                    continue

                current_remote_folder_id = remote_dir_map.get(root)
                if current_remote_folder_id is None:
                    print(f"Warning: No remote folder ID mapped for {root}. Skipping files within.")
                    continue

                for filename in files:
                    if file_types: # Filter by extension if specified
                        if not any(filename.lower().endswith(ft.lower()) for ft in file_types):
                            continue
                    
                    local_file_path = os.path.join(root, filename)
                    
                    # Submit file upload task. Each self.upload_file will internally handle chunk concurrency.
                    task = executor.submit(self.upload_file, 
                                           local_file_path, 
                                           current_remote_folder_id, 
                                           sure,
                                           num_chunks_per_file)
                    file_upload_tasks.append(task)
            
            all_files_successful = True
            for future in as_completed(file_upload_tasks):
                try:
                    if not future.result(): # upload_file returns True on success
                        all_files_successful = False
                        # Potentially log which file failed if needed, future.result() would be False
                except Exception as e:
                    print(f"An error occurred during a file upload task: {e}")
                    all_files_successful = False
        
        if all_files_successful:
            print(f"Directory '{base_dir_name}' upload process completed.")
            return True
        else:
            print(f"Directory '{base_dir_name}' upload process completed with some errors.")
            return False
