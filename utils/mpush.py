import os
import json
import time
import hashlib
import requests
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
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
        self.pan = pan

    @staticmethod
    def compute_file_md5(file_path):
        """Calculate MD5 hash of a file"""
        with open(file_path, "rb") as f:
            md5 = hashlib.md5()
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                md5.update(chunk)
            return md5.hexdigest()

    def upload_file(self, file_path, parent_id=None, sure=None):
        """Upload a single file to 123Pan Cloud

        Args:
            file_path: Path to the file to upload
            parent_id: Parent folder ID (None for root)
            sure: How to handle duplicates - 1:keep both, 2:overwrite

        Returns:
            bool: True if upload successful, False otherwise
        """
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            print(f"Error: {file_path} is not a valid file")
            return False

        file_path = file_path.replace('"', "").replace("\\", "/")
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        print(f"Preparing to upload: {file_name} ({format_size(file_size)})")

        print("Calculating file MD5...")
        md5 = self.compute_file_md5(file_path)

        if parent_id is None:
            parent_id = self.pan.parentFileId

        # First check if file with same name exists and handle accordingly
        if sure == "2":  # If overwrite is pre-selected
            # Get current directory files
            self.pan.get_dir()
            # Find file with same name
            for i, file_info in enumerate(self.pan.list):
                if file_info["FileName"] == file_name and file_info["Type"] == 0:
                    print(f"Deleting existing file: {file_name}")
                    # Use file index to delete (by_num=True)
                    self.pan.delete_file(i, by_num=True, operation=True)
                    # Refresh directory listing
                    self.pan.get_dir()
                    break

        list_up_request = {
            "driveId": 0,
            "etag": md5,
            "fileName": file_name,
            "parentFileId": parent_id,
            "size": file_size,
            "type": 0,
            "duplicate": 0,
        }

        sign = getSign("/b/api/file/upload_request")
        up_res = requests.post(
            "https://www.123pan.com/b/api/file/upload_request",
            headers=self.pan.headerLogined,
            params={sign[0]: sign[1]},
            data=json.dumps(list_up_request),
        )

        up_res_json = up_res.json()
        code = up_res_json.get("code")

        if code == 5060:
            print("Duplicate file detected")
            if sure not in ["1", "2"]:
                sure = input(
                    "Duplicate file detected. Enter 1 to keep both, 2 to overwrite, 0 to cancel: "
                )

            if sure == "1":
                list_up_request["duplicate"] = 1
            elif sure == "2":
                # Delete the existing file and retry
                self.pan.get_dir()
                for i, file_info in enumerate(self.pan.list):
                    if file_info["FileName"] == file_name and file_info["Type"] == 0:
                        print(f"Deleting existing file: {file_name}")
                        self.pan.delete_file(i, by_num=True, operation=True)
                        # Refresh directory listing after deletion
                        self.pan.get_dir()
                        break

                # Now overwrite by setting duplicate=2
                list_up_request["duplicate"] = 2
            else:
                print("Upload cancelled")
                return False

            sign = getSign("/b/api/file/upload_request")
            up_res = requests.post(
                "https://www.123pan.com/b/api/file/upload_request",
                headers=self.pan.headerLogined,
                params={sign[0]: sign[1]},
                data=json.dumps(list_up_request),
            )
            up_res_json = up_res.json()
            code = up_res_json.get("code")

        if code == 0:
            reuse = up_res_json["data"].get("Reuse")
            if reuse:
                print(f"Upload successful, file MD5 reused: {file_name}")
                return True
        else:
            print(f"Upload request failed: {up_res_json}")
            return False

        bucket = up_res_json["data"]["Bucket"]
        storage_node = up_res_json["data"]["StorageNode"]
        upload_key = up_res_json["data"]["Key"]
        upload_id = up_res_json["data"]["UploadId"]
        up_file_id = up_res_json["data"]["FileId"]

        start_data = {
            "bucket": bucket,
            "key": upload_key,
            "uploadId": upload_id,
            "storageNode": storage_node,
        }

        start_res = requests.post(
            "https://www.123pan.com/b/api/file/s3_list_upload_parts",
            headers=self.pan.headerLogined,
            data=json.dumps(start_data),
        )

        start_res_json = start_res.json()
        if start_res_json["code"] != 0:
            print(f"Failed to get transfer list: {start_res_json}")
            return False

        block_size = 5242880  # 5MB
        part_number_start = 1

        with open(file_path, "rb") as f, tqdm(
            total=file_size, unit="B", unit_scale=True, desc=file_name
        ) as pbar:
            while True:
                data = f.read(block_size)
                if not data:
                    break

                get_link_data = {
                    "bucket": bucket,
                    "key": upload_key,
                    "partNumberEnd": part_number_start + 1,
                    "partNumberStart": part_number_start,
                    "uploadId": upload_id,
                    "StorageNode": storage_node,
                }

                get_link_res = requests.post(
                    "https://www.123pan.com/b/api/file/s3_repare_upload_parts_batch",
                    headers=self.pan.headerLogined,
                    data=json.dumps(get_link_data),
                )

                get_link_res_json = get_link_res.json()
                if get_link_res_json["code"] != 0:
                    print(f"Failed to get upload link: {get_link_res_json}")
                    return False

                upload_url = get_link_res_json["data"]["presignedUrls"][
                    str(part_number_start)
                ]
                requests.put(upload_url, data=data)

                pbar.update(len(data))
                part_number_start += 1

        print("Chunk upload complete, finalizing...")

        uploaded_list_url = "https://www.123pan.com/b/api/file/s3_list_upload_parts"
        uploaded_comp_data = {
            "bucket": bucket,
            "key": upload_key,
            "uploadId": upload_id,
            "storageNode": storage_node,
        }

        requests.post(
            uploaded_list_url,
            headers=self.pan.headerLogined,
            data=json.dumps(uploaded_comp_data),
        )

        comp_multipart_up_url = (
            "https://www.123pan.com/b/api/file/s3_complete_multipart_upload"
        )
        requests.post(
            comp_multipart_up_url,
            headers=self.pan.headerLogined,
            data=json.dumps(uploaded_comp_data),
        )

        if file_size > 64 * 1024 * 1024:
            time.sleep(3)

        close_up_session_url = "https://www.123pan.com/b/api/file/upload_complete"
        close_up_session_data = {"fileId": up_file_id}

        close_up_session_res = requests.post(
            close_up_session_url,
            headers=self.pan.headerLogined,
            data=json.dumps(close_up_session_data),
        )

        close_res_json = close_up_session_res.json()
        if close_res_json["code"] == 0:
            print(f"Upload successful: {file_name}")
            return True
        else:
            print(f"Upload failed: {close_res_json}")
            return False

    def upload_directory_concurrent(
        self,
        dir_path,
        parent_id=None,
        max_workers=8,
        file_types=None,
        sure=None,
        custom_dirname=None,
    ):
        """Upload a directory to 123Pan Cloud using concurrent threads

        Args:
            dir_path: Path to the directory to upload
            parent_id: Parent folder ID (None for root)
            max_workers: Maximum number of concurrent upload threads
            file_types: List of file extensions to include
            sure: How to handle duplicates
            custom_dirname: Custom name for the remote directory

        Returns:
            bool: True if upload successful, False otherwise
        """
        if not os.path.isdir(dir_path):
            print(f"Error: {dir_path} is not a valid directory")
            return False

        dir_path = dir_path.replace('"', "").replace("\\", "/")
        dir_name = custom_dirname if custom_dirname else os.path.basename(dir_path)

        print(f"Preparing to upload directory: {dir_name}")

        if parent_id is None:
            parent_id = self.pan.parentFileId

        folder_id = self.pan.mkdir(dir_name, parent_id, remake=False)
        if not folder_id:
            print(f"Failed to create directory {dir_name}")
            return False

        print(f"Directory created: {dir_name}, ID: {folder_id}")

        # Directory ID mapping
        mkdir_list = {dir_path: folder_id}

        # Directories to skip
        skip_dirs = ["venv", ".idea", "__pycache__", ".git", "node_modules"]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # First create directory structure
            for filepath, dirnames, _ in os.walk(dir_path):
                # Skip specific directories
                if any(skip_dir in filepath for skip_dir in skip_dirs):
                    continue

                # Create remote folders for subdirectories
                parent_path = os.path.dirname(filepath)
                if parent_path in mkdir_list and filepath != dir_path:
                    dir_name = os.path.basename(filepath)
                    parent_folder_id = mkdir_list[parent_path]
                    folder_id = self.pan.mkdir(dir_name, parent_folder_id, remake=False)
                    time.sleep(0.2)  # Avoid too frequent requests
                    mkdir_list[filepath] = folder_id
                    print(f"Created directory: {dir_name}, ID: {folder_id}")

            # Then upload files concurrently
            for filepath, _, filenames in os.walk(dir_path):
                if any(skip_dir in filepath for skip_dir in skip_dirs):
                    continue

                if filepath not in mkdir_list:
                    continue

                current_folder_id = mkdir_list[filepath]

                for filename in filenames:
                    # Filter by file type if specified
                    if file_types:
                        if not any(filename.endswith(ft) for ft in file_types):
                            continue

                    file_path = os.path.join(filepath, filename)
                    executor.submit(
                        self.upload_file, file_path, current_folder_id, sure
                    )

        print(f"Directory upload completed")
        return True
